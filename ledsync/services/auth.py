"""Local administrator authentication (BRD Sections 6.1, 6.2).

ACCEPTED RISK (BRD 6.1): the single fixed administrator credential is stored in
PLAIN TEXT in `application_settings`. This was explicitly accepted because the
application runs only for the duration of an event on a venue-local machine.

SCOPE GUARD (Security checklist B2): this credential is used for the local login
and the change-password screen ONLY. It must never be reused for anything else
(Azure, shared folders, email, ...). Only this module reads or writes it; a test
(`test_credential_only_touched_by_auth_module`) enforces that.
"""

import hmac
import sqlite3
import threading
import time
from collections.abc import Callable

KEY_USERNAME = "admin_username"
KEY_PASSWORD = "admin_password"

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "Admin@123"  # BRD 6.1 - documented, accepted-risk default

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_USERNAME_LENGTH = 100


class PasswordChangeError(ValueError):
    """`counts_as_failure` is True when the current password was wrong, so the
    change-password form is throttled like the login form."""

    def __init__(self, message: str, counts_as_failure: bool = False):
        super().__init__(message)
        self.counts_as_failure = counts_as_failure


def seed_admin(conn: sqlite3.Connection) -> None:
    """Create the default administrator on first run. Never overwrites an existing
    (possibly changed) credential - upgrades keep the administrator's password."""
    conn.executemany(
        "INSERT OR IGNORE INTO application_settings (setting_name, setting_value) VALUES (?, ?)",
        [(KEY_USERNAME, DEFAULT_USERNAME), (KEY_PASSWORD, DEFAULT_PASSWORD)],
    )
    conn.commit()


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT setting_value FROM application_settings WHERE setting_name = ?", (key,)
    ).fetchone()
    return row["setting_value"] if row else None


def get_username(conn: sqlite3.Connection) -> str | None:
    return _get(conn, KEY_USERNAME)


def _equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def check_credentials(conn: sqlite3.Connection, username: str, password: str) -> bool:
    stored_user, stored_pass = _get(conn, KEY_USERNAME), _get(conn, KEY_PASSWORD)
    if stored_user is None or stored_pass is None:
        return False
    # Evaluate both comparisons so timing does not reveal which field was wrong.
    user_ok = _equal(username, stored_user)
    pass_ok = _equal(password, stored_pass)
    return user_ok and pass_ok


def change_password(conn: sqlite3.Connection, current: str, new: str, confirm: str) -> None:
    stored = _get(conn, KEY_PASSWORD)
    if stored is None or not _equal(current, stored):
        raise PasswordChangeError("Current password is incorrect.", counts_as_failure=True)
    if new != confirm:
        raise PasswordChangeError("New password and confirmation do not match.")
    if len(new) < MIN_PASSWORD_LENGTH:
        raise PasswordChangeError(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(new) > MAX_PASSWORD_LENGTH:
        raise PasswordChangeError(f"New password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if _equal(new, stored):
        raise PasswordChangeError("New password must be different from the current password.")
    # Compare-and-set: only update if the password is still the one we verified, so a
    # concurrent writer (e.g. a future headless run) cannot be silently overwritten.
    cur = conn.execute(
        "UPDATE application_settings SET setting_value = ? "
        "WHERE setting_name = ? AND setting_value = ?",
        (new, KEY_PASSWORD, stored),
    )
    conn.commit()
    if cur.rowcount != 1:
        raise PasswordChangeError("The password was changed elsewhere. Please try again.")


class LoginThrottle:
    """After `max_failures` consecutive failed attempts, refuse attempts for
    `lock_seconds`. In memory only (cleared on restart) and app-wide - the server
    is loopback-only and gated, so there is no meaningful per-client identity.

    `begin_attempt()` is ONE atomic step under a lock: it both checks the lock and
    counts the attempt. (A separate check-then-record pair would let parallel
    requests all pass the check before any is counted.) A correct password calls
    `reset()`, which clears the count and any lock."""

    def __init__(
        self,
        max_failures: int = 5,
        lock_seconds: int = 30,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._clock = clock
        self._attempts = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def begin_attempt(self) -> int:
        """Return 0 if this attempt may proceed (and count it), otherwise the
        whole seconds remaining until attempts are accepted again."""
        with self._lock:
            now = self._clock()
            remaining = self._locked_until - now
            if remaining > 0:
                return int(remaining) + 1
            self._attempts += 1
            if self._attempts >= self.max_failures:
                self._attempts = 0
                self._locked_until = now + self.lock_seconds
            return 0

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0
            self._locked_until = 0.0
