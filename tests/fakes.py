"""A faithful in-memory fake of the parts of the Azure blob SDK that the application uses,
so `AzureReadOnlyStorage` itself (folder search, container order, size capping, error
mapping) is what the tests exercise - not a mock of it.

    fake = FakeBlobService(key=KEY)
    fake.put("2026", "1000 - Star contender Doha/_GUID.json", b"...")
    storage = AzureReadOnlyStorage("sasportspresentation", KEY, service_factory=fake.factory)
"""

import hashlib
from datetime import datetime, timezone

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceModifiedError, ResourceNotFoundError

# Shaped like a real 88-character account key, but obviously fake.
FAKE_KEY = "FAKEKEY" + "A" * 79 + "=="
ACCOUNT = "sasportspresentation"


class _Named:
    def __init__(self, name):
        self.name = name


class _Settings:
    def __init__(self, md5):
        self.content_md5 = md5


class _BlobItem:
    """What `walk_blobs` yields for a file: name, size, content settings (MD5) and last-modified time."""

    def __init__(self, name, data, service):
        self.name = name
        self.size = len(data)
        digest = service.md5_overrides.get(name, None if service.no_md5 else hashlib.md5(data).digest())
        self.content_settings = _Settings(bytearray(digest) if digest else None)
        self.last_modified = service.modified
        self.etag = f'"{hashlib.md5(data).hexdigest()}"'


class _Download:
    def __init__(self, data):
        self._data = data

    def readall(self):
        return self._data


class _BlobClient:
    def __init__(self, service, container, blob):
        self._service, self._container, self._blob = service, container, blob

    def download_blob(self, offset=0, length=None, etag=None, match_condition=None):
        self._service._check("download")
        if self._container not in self._service.containers or (self._container, self._blob) not in self._service.blobs:
            raise ResourceNotFoundError("BlobNotFound")
        data = self._service.blobs[(self._container, self._blob)]
        if etag is not None and etag != f'"{hashlib.md5(data).hexdigest()}"':
            raise ResourceModifiedError("ConditionNotMet")             # the file is no longer the version that was listed
        self._service.downloads.append((self._container, self._blob, offset, length))
        end = None if length is None else offset + length
        return _Download(data[offset:end])


class _ContainerClient:
    def __init__(self, service, container):
        self._service, self._container = service, container

    def walk_blobs(self, name_starts_with="", delimiter="/"):
        self._service._check("list")
        if self._container not in self._service.containers:
            raise ResourceNotFoundError("ContainerNotFound")
        seen = []
        for (container, path) in sorted(self._service.blobs):
            if container != self._container or not path.startswith(name_starts_with):
                continue
            rest = path[len(name_starts_with):]
            if delimiter in rest:                      # a 'folder': report the prefix once
                prefix = path[: len(name_starts_with) + rest.index(delimiter) + 1]
                if prefix not in seen:
                    seen.append(prefix)
                    yield _Named(prefix)
            else:
                yield _BlobItem(path, self._service.blobs[(container, path)], self._service)


class FakeBlobService:
    def __init__(self, key=FAKE_KEY):
        self.expected_key = key
        self.given_key = None
        self.containers = set()
        self.blobs = {}
        self.downloads = []
        self.fail_with = None          # an exception raised by the next network call
        self.calls = []
        self.no_md5 = False            # True: Azure has no fingerprint for the files
        self.md5_overrides = {}        # blob name -> a (wrong) MD5 to report, to test corruption checks
        self.modified = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    # --- test helpers
    def put(self, container, path, data):
        self.containers.add(container)
        self.blobs[(container, path)] = data if isinstance(data, bytes) else data.encode("utf-8")

    def factory(self, account_url, access_key):
        self.given_key = access_key
        self.account_url = account_url
        return self

    def _check(self, what):
        self.calls.append(what)
        if self.given_key != self.expected_key:
            raise ClientAuthenticationError("AuthenticationFailed: the key was wrong")
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc

    # --- the SDK surface the application uses
    def list_containers(self, **_kw):
        self._check("list_containers")
        return [_Named(n) for n in sorted(self.containers)]

    def get_container_client(self, container):
        return _ContainerClient(self, container)

    def get_blob_client(self, container, blob):
        return _BlobClient(self, container, blob)

    # Anything mutating must never be called by the application; if it is, fail loudly.
    def __getattr__(self, name):
        if name.startswith(("upload", "delete", "create", "set_", "stage", "commit", "begin_", "undelete")):
            raise AssertionError(f"the application called a WRITE operation on Azure: {name}")
        raise AttributeError(name)


def http_error(status: int) -> HttpResponseError:
    class _Resp:
        status_code = status
        reason = "test"
        headers = {}

        def text(self):
            return ""

    err = HttpResponseError(message=f"HTTP {status} secret-request-id-abc123", response=_Resp())
    err.status_code = status
    return err
