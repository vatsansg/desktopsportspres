"""Phase 4 - the read-only Azure storage client (BRD 4, 15, 16; Business Rule 13)."""

import re
from pathlib import Path

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError, HttpResponseError, ResourceNotFoundError, ServiceRequestError, ServiceResponseError,
)
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService, http_error

from ledsync.services import exceptions
from ledsync.services import registration as reg
from ledsync.services.storage import (
    AzureReadOnlyStorage, EventLocation, StorageError, map_error,
)

ROOT = Path(__file__).resolve().parent.parent


def make(fake=None, key=FAKE_KEY):
    fake = fake or FakeBlobService()
    return AzureReadOnlyStorage(ACCOUNT, key, service_factory=fake.factory), fake


# --- finding an event's folder ---------------------------------------------------------------

def test_finds_the_event_folder_from_the_event_id_in_the_preferred_container():
    storage, fake = make()
    fake.put("2026", "1000 - Star contender Doha/_GUID.json", b"{}")
    fake.put("2026", "1000 - Star contender Doha/Table 1/Inner/a.png", b"x")
    fake.put("2026", "2000 - Another/_GUID.json", b"{}")
    assert storage.find_event("1000", "2026") == EventLocation("2026", "1000 - Star contender Doha")


def test_the_folder_prefix_is_exact_so_10000_is_not_mistaken_for_1000():
    storage, fake = make()
    fake.put("2026", "10000 - Big Event/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.find_event("1000", "2026")
    assert exc.value.category == exceptions.MISSING_FOLDER


def test_searches_other_year_containers_newest_first_when_not_in_the_preferred_one():
    storage, fake = make()
    fake.put("2026", "5000 - Elsewhere/_GUID.json", b"{}")
    fake.put("2025", "1000 - Old Doha/_GUID.json", b"{}")
    fake.put("2024", "1000 - Older Doha/_GUID.json", b"{}")
    loc = storage.find_event("1000", "2026")
    assert loc == EventLocation("2025", "1000 - Old Doha")          # 2025 before 2024


def test_a_preferred_container_that_does_not_exist_is_skipped_not_fatal():
    storage, fake = make()
    fake.put("2025", "1000 - Doha/_GUID.json", b"{}")
    assert storage.find_event("1000", "2030").container == "2025"


def test_non_year_containers_are_never_searched():
    storage, fake = make()
    fake.put("archive", "1000 - Doha/_GUID.json", b"{}")
    fake.put("rpi", "1000 - Doha/_GUID.json", b"{}")
    with pytest.raises(StorageError):
        storage.find_event("1000", "2026")


def test_not_found_message_names_the_event_and_where_it_looked():
    storage, fake = make()
    fake.containers.add("2026")
    with pytest.raises(StorageError) as exc:
        storage.find_event("4242", "2026")
    assert exc.value.category == exceptions.MISSING_FOLDER
    assert "4242" in exc.value.message and "2026" in exc.value.message


def test_two_folders_for_one_event_id_is_reported_as_ambiguous_not_guessed():
    storage, fake = make()
    fake.put("2026", "1000 - Doha/_GUID.json", b"{}")
    fake.put("2026", "1000 - Doha Copy/_GUID.json", b"{}")
    with pytest.raises(StorageError) as exc:
        storage.find_event("1000", "2026")
    assert exc.value.category == exceptions.CONFIGURATION and "More than one folder" in exc.value.message


def test_with_no_preferred_container_all_year_containers_are_searched():
    storage, fake = make()
    fake.put("2025", "7 - Seven/_GUID.json", b"{}")
    assert storage.find_event("7", "").container == "2025"


def test_guid_blob_path_and_recorded_address():
    loc = EventLocation("2026", "1000 - Star contender Doha")
    assert loc.guid_blob_path == "1000 - Star contender Doha/_GUID.json"
    url = loc.blob_url(ACCOUNT, "_GUID.json")
    assert url == "https://sasportspresentation.blob.core.windows.net/2026/1000%20-%20Star%20contender%20Doha/_GUID.json"
    assert "?" not in url and "@" not in url and FAKE_KEY not in url          # never a credential


# --- downloading ---------------------------------------------------------------------------------

def test_reads_a_blob():
    storage, fake = make()
    fake.put("2026", "1000 - Doha/_GUID.json", b'{"a": 1}')
    assert storage.read_blob(EventLocation("2026", "1000 - Doha"), "_GUID.json", 1000) == b'{"a": 1}'


def test_a_huge_blob_is_never_fully_downloaded():
    """The size cap is applied to the download itself (max+1 bytes requested), so a hostile or
    corrupt blob cannot fill memory - and the parser then rejects it as too large."""
    storage, fake = make()
    fake.put("2026", "1000 - Doha/_GUID.json", b"x" * 10_000_000)
    data = storage.read_blob(EventLocation("2026", "1000 - Doha"), "_GUID.json", reg.MAX_CONFIG_BYTES)
    assert len(data) == reg.MAX_CONFIG_BYTES + 1
    assert fake.downloads[-1][2:] == (0, reg.MAX_CONFIG_BYTES + 1)
    with pytest.raises(reg.RegistrationError, match="too large"):
        reg.parse_event_config(data)


def test_folder_without_a_guid_file_gets_a_specific_helpful_message():
    storage, fake = make()
    fake.put("2026", "1000 - Doha/Table 1/Inner/a.png", b"x")
    with pytest.raises(StorageError) as exc:
        storage.read_blob(EventLocation("2026", "1000 - Doha"), "_GUID.json", 1000)
    assert exc.value.category == exceptions.MISSING_FOLDER
    assert "no _GUID.json" in exc.value.message and "Export Event" in exc.value.message


# --- connection test ---------------------------------------------------------------------------------

def test_connection_report_lists_containers_and_flags_the_default_one():
    storage, fake = make()
    for c in ("2025", "2026", "archive"):
        fake.containers.add(c)
    report = storage.test_connection("2026")
    assert report.account == ACCOUNT
    assert report.containers == ("2025", "2026", "archive")
    assert report.year_containers == ("2025", "2026")
    assert report.preferred_container_found is True
    assert storage.test_connection("2030").preferred_container_found is False
    assert storage.test_connection("").preferred_container_found is None


# --- error mapping: BRD 21 categories, safe messages ---------------------------------------------------

SECRET = "secret-request-id-abc123"


@pytest.mark.parametrize("exc,category", [
    (ClientAuthenticationError("AuthenticationFailed " + SECRET), exceptions.PERMISSION),
    (http_error(403), exceptions.PERMISSION),
    (http_error(401), exceptions.PERMISSION),
    (ServiceRequestError("Failed to resolve 'sasportspresentation.blob.core.windows.net' " + SECRET), exceptions.STORAGE_CONNECTIVITY),
    (ServiceResponseError("Connection aborted " + SECRET), exceptions.STORAGE_CONNECTIVITY),
    (TimeoutError("timed out " + SECRET), exceptions.STORAGE_CONNECTIVITY),
    (ConnectionResetError("reset " + SECRET), exceptions.STORAGE_CONNECTIVITY),
    (http_error(500), exceptions.STORAGE_CONNECTIVITY),
    (http_error(503), exceptions.STORAGE_CONNECTIVITY),
    (ResourceNotFoundError("ContainerNotFound " + SECRET), exceptions.MISSING_FOLDER),
    (http_error(404), exceptions.MISSING_FOLDER),
    (RuntimeError("something odd " + SECRET), exceptions.DOWNLOAD),
    (ValueError(FAKE_KEY), exceptions.DOWNLOAD),
])
def test_every_failure_maps_to_a_category_and_never_echoes_sdk_text_or_the_key(exc, category):
    err = map_error(exc, "testing")
    assert err.category == category
    assert SECRET not in err.message and FAKE_KEY not in err.message and "sasportspresentation" not in err.message
    assert err.message and err.message[0].isupper()


@pytest.mark.parametrize("op", ["list_containers", "find", "read", "test"])
def test_errors_from_every_operation_surface_as_storage_errors(op):
    storage, fake = make()
    fake.put("2026", "1000 - Doha/_GUID.json", b"{}")
    fake.fail_with = ServiceRequestError("no network " + SECRET)
    with pytest.raises(StorageError) as exc:
        {"list_containers": lambda: storage.test_connection(""),
         "find": lambda: storage.find_event("1000", "2026"),
         "read": lambda: storage.read_blob(EventLocation("2026", "1000 - Doha"), "_GUID.json", 100),
         "test": lambda: storage.test_connection("2026")}[op]()
    assert exc.value.category == exceptions.STORAGE_CONNECTIVITY and SECRET not in exc.value.message


def test_a_wrong_key_is_a_permission_error_from_any_operation():
    storage, fake = make(key="WRONG" + "A" * 83)
    fake.put("2026", "1000 - Doha/_GUID.json", b"{}")
    for call in (lambda: storage.test_connection("2026"), lambda: storage.find_event("1000", "2026"),
                 lambda: storage.read_blob(EventLocation("2026", "1000 - Doha"), "_GUID.json", 100)):
        with pytest.raises(StorageError) as exc:
            call()
        assert exc.value.category == exceptions.PERMISSION
        assert "WRONG" not in exc.value.message and "AuthenticationFailed" not in exc.value.message


def test_the_storage_object_does_not_expose_the_key():
    storage, _ = make()
    public = [n for n in dir(storage) if not n.startswith("_")]
    assert not any("key" in n.lower() or "credential" in n.lower() for n in public)


def test_the_real_client_builds_offline_with_bounded_timeouts_and_retries():
    from ledsync.services import storage as st

    key = "dGVzdC1rZXktbm90LXJlYWw=" * 4
    client = st.AzureReadOnlyStorage._default_service(f"https://{ACCOUNT}.blob.core.windows.net", key)
    assert client.url.startswith(f"https://{ACCOUNT}.blob.core.windows.net")
    assert st.CONNECT_TIMEOUT <= 10 and st.READ_TIMEOUT <= 30            # an offline venue fails fast


# --- READ-ONLY guarantee (BRD 4 / Business Rule 13) - AST based, so aliasing and string tricks do not evade it ---

import ast

APP = ROOT / "ledsync"

# Every mutating Azure Blob operation (exact names), plus SAS generation. Forbidden in ALL application code.
FORBIDDEN_CALLS = {
    "upload_blob", "delete_blob", "delete_blobs", "set_blob_metadata", "set_blob_tags", "set_http_headers",
    "create_container", "delete_container", "start_copy_from_url", "begin_copy", "abort_copy", "stage_block",
    "stage_block_from_url", "commit_block_list", "append_block", "append_block_from_url", "create_snapshot",
    "set_standard_blob_tier", "set_premium_page_blob_tier", "undelete_blob", "acquire_lease", "renew_lease",
    "release_lease", "break_lease", "change_lease", "set_service_properties", "create_append_blob",
    "create_page_blob", "upload_page", "upload_pages_from_url", "clear_page", "resize_blob", "seal_append_blob",
    "set_blob_expiry", "set_legal_hold", "set_immutability_policy", "delete_immutability_policy",
    "set_container_metadata", "set_container_access_policy", "set_blob_legal_hold", "restore_container",
    "generate_account_sas", "generate_container_sas", "generate_blob_sas", "get_user_delegation_key",
    "send_request", "from_connection_string", "from_blob_url", "upload_blob_from_url", "start_copy",
}
PRIVATE_SDK_ATTRS = {"_client", "_pipeline", "pipeline", "_config", "_hosts", "_query_str"}
FORBIDDEN_IMPORTS = ("requests", "urllib.request", "http.client", "httpx", "aiohttp", "socket", "ssl", "importlib",
                     "urllib3")
DYNAMIC_BUILTINS = {"getattr", "setattr", "delattr", "__import__", "eval", "exec", "compile", "globals", "vars"}
# getattr is only allowed with a literal name from this list (the one exception: winreg in platform_checks.py)
LITERAL_GETATTR_OK = {"name", "status_code", "frozen", "winerror"}

# Everything storage.py may call as an attribute: the READ operations of the SDK, plus plain Python helpers.
SDK_READ_CALLS = {"list_containers", "get_container_client", "walk_blobs", "get_blob_client", "download_blob", "readall"}
PYTHON_HELPER_CALLS = {"__init__", "_containers_to_search", "_folders_for_event", "_listing", "_search",
                       "_year_containers_newest_first", "append", "astimezone", "blob_path", "casefold", "compile",
                       "endswith", "find_blob", "fullmatch", "getLogger", "join", "min", "search", "split",
                       "startswith", "strftime", "translate"}


def _modules():
    for path in sorted(APP.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_no_application_code_calls_a_mutating_or_signing_azure_operation():
    hits = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_CALLS:
                hits.append(f"{path.name}:{node.lineno} {node.func.attr}")
            if isinstance(node, ast.Attribute) and node.attr in PRIVATE_SDK_ATTRS:
                hits.append(f"{path.name}:{node.lineno} private SDK attribute {node.attr}")
    assert hits == []


def test_no_dynamic_attribute_access_that_could_hide_a_write_call():
    hits = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in DYNAMIC_BUILTINS:
                second = node.args[1] if len(node.args) > 1 else None
                literal = isinstance(second, ast.Constant) and isinstance(second.value, str)
                ok = node.func.id == "getattr" and (
                    (literal and second.value in LITERAL_GETATTR_OK)
                    or path.name == "platform_checks.py")                     # winreg hive lookup, no Azure involved
                if not ok:
                    hits.append(f"{path.name}:{node.lineno} {node.func.id}")
    assert hits == []



# storage.py is the only module allowed to touch the web application's Azure STORAGE account (the
# read-only guarantee this whole file enforces). email_notify.py (Phase 11) imports a different Azure
# service entirely - Communication Services, for sending the completion email - which has no read-only
# constraint (sending an email is inherently a "write" to that unrelated service) and no access to the
# web application's storage account; it is explicitly allowed here. This only widens the IMPORT check
# below - the FORBIDDEN_CALLS / PRIVATE_SDK_ATTRS guards two tests down still scan every module
# unconditionally, email_notify.py included, so a mutating Blob-Storage method name would still be
# caught there even inside it. test_phase11_email.py separately asserts email_notify.py's own azure
# imports never stray beyond azure.communication./azure.core. - narrower than "any azure.*", which is
# all this shared list can express.
AZURE_ALLOWED_MODULES = {"storage.py", "email_notify.py"}


def test_no_other_http_client_and_azure_is_imported_only_by_the_storage_module():
    hits = []
    for path, tree in _modules():
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith(FORBIDDEN_IMPORTS):
                    hits.append(f"{path.name}:{node.lineno} imports {name}")
                if name.startswith("azure") and path.name not in AZURE_ALLOWED_MODULES:
                    hits.append(f"{path.name}:{node.lineno} imports {name} outside storage.py/email_notify.py")
    assert hits == []


def test_storage_module_calls_exactly_the_read_operations_and_nothing_else():
    tree = ast.parse((APP / "services" / "storage.py").read_text(encoding="utf-8"))
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert calls <= SDK_READ_CALLS | PYTHON_HELPER_CALLS, calls - (SDK_READ_CALLS | PYTHON_HELPER_CALLS)
    assert SDK_READ_CALLS <= calls                          # and it really does use them (guards the test itself)


def test_the_ast_guard_actually_catches_evasions():
    """The guard is only worth something if it fails on the tricks the old substring test missed."""
    for snippet in ("client.upload_blob(x)", "svc.delete_container('a')", "c._client.blob.delete()",
                    "getattr(c, 'upload_' + 'blob')(x)", "import requests", "import urllib.request",
                    "from azure.storage.blob import BlobClient"):
        tree = ast.parse(snippet)
        found = any(
            (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in FORBIDDEN_CALLS)
            or (isinstance(n, ast.Attribute) and n.attr in PRIVATE_SDK_ATTRS)
            or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                and not (isinstance(n.args[1], ast.Constant) and n.args[1].value in LITERAL_GETATTR_OK))
            or (isinstance(n, (ast.Import, ast.ImportFrom)) and (
                any(a.name.startswith(FORBIDDEN_IMPORTS) or a.name.startswith("azure") for a in getattr(n, "names", []))
                or (getattr(n, "module", "") or "").startswith("azure")))
            for n in ast.walk(tree))
        assert found, snippet


def test_a_write_attempt_on_the_service_would_fail_loudly_in_tests():
    """Belt and braces: the fake refuses every mutating call, so any accidental write in a full
    registration flow (see test_cloud_registration) fails the run."""
    fake = FakeBlobService()
    with pytest.raises(AssertionError, match="WRITE"):
        fake.upload_blob("x")
    with pytest.raises(AssertionError, match="WRITE"):
        fake.delete_container("2026")
