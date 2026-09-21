"""Authentication service (BRD 6.1 / 6.2): credential, change-password rules, throttle."""

import re
from pathlib import Path

import pytest

from ledsync.db import connect, init_db
from ledsync.services import auth


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def test_seed_creates_the_brd_default_admin(conn):
    auth.seed_admin(conn)
    assert auth.check_credentials(conn, "admin", "Admin@123")
    assert auth.get_username(conn) == "admin"


def test_seed_is_idempotent_and_never_overwrites_a_changed_password(conn):
    """BRD 28.2: an upgrade/relaunch must keep the administrator's password."""
    auth.seed_admin(conn)
    auth.change_password(conn, "Admin@123", "NewSecret#1", "NewSecret#1")
    auth.seed_admin(conn)
    assert auth.check_credentials(conn, "admin", "NewSecret#1")
    assert not auth.check_credentials(conn, "admin", "Admin@123")


def test_credentials_are_case_sensitive_and_both_must_match(conn):
    auth.seed_admin(conn)
    assert not auth.check_credentials(conn, "Admin", "Admin@123")
    assert not auth.check_credentials(conn, "admin", "admin@123")
    assert not auth.check_credentials(conn, "admin", "")
    assert not auth.check_credentials(conn, "", "")
    assert not auth.check_credentials(conn, "other", "Admin@123")


def test_no_credential_rows_means_no_login(conn):
    assert not auth.check_credentials(conn, "admin", "Admin@123")


def test_credential_is_stored_as_plain_text_per_accepted_risk(conn):
    """BRD 6.1 documents plain-text storage as an ACCEPTED RISK. This test exists so
    a change to that decision is deliberate, not accidental."""
    auth.seed_admin(conn)
    row = conn.execute("SELECT setting_value FROM application_settings "
                       "WHERE setting_name = ?", (auth.KEY_PASSWORD,)).fetchone()
    assert row["setting_value"] == "Admin@123"


@pytest.mark.parametrize("current,new,confirm,fragment", [
    ("wrong", "NewSecret#1", "NewSecret#1", "Current password is incorrect"),
    ("Admin@123", "NewSecret#1", "different", "do not match"),
    ("Admin@123", "short", "short", "at least 8"),
    ("Admin@123", "x" * 129, "x" * 129, "at most 128"),
    ("Admin@123", "Admin@123", "Admin@123", "must be different"),
])
def test_change_password_rejections(conn, current, new, confirm, fragment):
    auth.seed_admin(conn)
    with pytest.raises(auth.PasswordChangeError, match=fragment):
        auth.change_password(conn, current, new, confirm)
    assert auth.check_credentials(conn, "admin", "Admin@123")  # unchanged


def test_only_wrong_current_password_counts_as_a_throttle_failure(conn):
    auth.seed_admin(conn)
    with pytest.raises(auth.PasswordChangeError) as wrong:
        auth.change_password(conn, "nope", "NewSecret#1", "NewSecret#1")
    with pytest.raises(auth.PasswordChangeError) as mismatch:
        auth.change_password(conn, "Admin@123", "NewSecret#1", "other")
    assert wrong.value.counts_as_failure is True
    assert mismatch.value.counts_as_failure is False


def test_password_may_contain_spaces_and_unicode(conn):
    auth.seed_admin(conn)
    auth.change_password(conn, "Admin@123", "  pässwörd 日本  ", "  pässwörd 日本  ")
    assert auth.check_credentials(conn, "admin", "  pässwörd 日本  ")


# --- throttle -------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_throttle_allows_five_attempts_then_locks_then_releases():
    clock = FakeClock()
    t = auth.LoginThrottle(clock=clock)
    for _ in range(5):
        assert t.begin_attempt() == 0
    assert 29 <= t.begin_attempt() <= 31   # 6th attempt: locked
    clock.now += 15
    assert 14 <= t.begin_attempt() <= 16   # refusals do not extend the lock
    clock.now += 16
    assert t.begin_attempt() == 0


def test_throttle_success_resets_the_counter_and_any_lock():
    t = auth.LoginThrottle(clock=FakeClock())
    for _ in range(4):
        t.begin_attempt()
    t.reset()
    for _ in range(5):
        assert t.begin_attempt() == 0   # fresh count of five
    assert t.begin_attempt() > 0


def test_throttle_counter_restarts_after_a_lock_expires():
    clock = FakeClock()
    t = auth.LoginThrottle(clock=clock)
    for _ in range(5):
        t.begin_attempt()
    assert t.begin_attempt() > 0
    clock.now += 31
    for _ in range(5):
        assert t.begin_attempt() == 0


def test_throttle_is_atomic_under_parallel_attempts():
    """Review finding: a separate check-then-count let parallel requests all pass
    the check. Exactly `max_failures` of many simultaneous attempts may proceed."""
    import threading

    t = auth.LoginThrottle(clock=lambda: 1.0)
    results, barrier = [], threading.Barrier(60)

    def attempt():
        barrier.wait()
        results.append(t.begin_attempt())

    threads = [threading.Thread(target=attempt) for _ in range(60)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(0) == 5
    assert sum(1 for r in results if r > 0) == 55


def test_password_change_is_compare_and_set(conn, monkeypatch):
    """If the stored password changed between verification and update (a second
    writer), the update must not silently overwrite it."""
    auth.seed_admin(conn)
    conn.execute("UPDATE application_settings SET setting_value = 'Other#1234' "
                 "WHERE setting_name = ?", (auth.KEY_PASSWORD,))
    conn.commit()
    monkeypatch.setattr(auth, "_get", lambda c, k: "Admin@123")  # stale read
    with pytest.raises(auth.PasswordChangeError, match="changed elsewhere"):
        auth.change_password(conn, "Admin@123", "NewSecret#1", "NewSecret#1")
    monkeypatch.undo()
    assert auth.check_credentials(conn, "admin", "Other#1234")


# --- scope guard (Security B2) --------------------------------------------

def test_credential_only_touched_by_auth_module():
    """The plain-text admin credential is an accepted risk ONLY for the local login.
    No other module may read or reference it."""
    root = Path(__file__).resolve().parent.parent / "ledsync"
    pattern = re.compile(r"admin_password|admin_username|Admin@123|KEY_PASSWORD|"
                         r"KEY_USERNAME|DEFAULT_PASSWORD|DEFAULT_USERNAME")
    offenders = [
        str(p.relative_to(root)) for p in root.rglob("*.py")
        if p.name != "auth.py" and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_only_the_two_settings_modules_touch_application_settings():
    """Two owners, each confined to its own keys: services/auth.py (the admin credential) and
    services/settings.py (cloud storage settings + the Azure key). Nothing else may read it."""
    root = Path(__file__).resolve().parent.parent / "ledsync"
    users = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py")
                   if "application_settings" in p.read_text(encoding="utf-8")
                   and p.name not in ("schema.py",))
    assert users == ["services/auth.py", "services/settings.py"]


def test_the_two_settings_owners_never_reference_each_others_keys():
    root = Path(__file__).resolve().parent.parent / "ledsync" / "services"
    auth_src = (root / "auth.py").read_text(encoding="utf-8")
    settings_src = (root / "settings.py").read_text(encoding="utf-8")
    # settings.py must not be able to read the admin credential (no admin key names/constants) ...
    assert not re.search(r"admin_password|admin_username|Admin@123|KEY_PASSWORD|KEY_USERNAME", settings_src.replace("`admin_*`", ""))
    # ... and auth.py must not touch the Azure key or cloud settings.
    assert not re.search(r"cloud_|KEY_ACCESS|KEY_ACCOUNT|KEY_CONTAINER|access_key", auth_src)
    assert 'assert key in OWNED_KEYS' in settings_src        # settings.py refuses any key it does not own
