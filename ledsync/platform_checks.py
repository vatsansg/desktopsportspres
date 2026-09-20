"""Windows-specific startup checks and the fatal-error dialog."""

import sys

# Registry key names (Edge Update client GUIDs) for the WebView2 Runtime and its
# Beta/Dev/Canary channels - the same set pywebview itself consults.
_WEBVIEW2_CLIENT_KEYS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Evergreen runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
)


def _read_pv(hive_name: str, subkey: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(getattr(winreg, hive_name), subkey) as k:
            value, _ = winreg.QueryValueEx(k, "pv")
            return str(value) if value and str(value) != "0.0.0.0" else None
    except OSError:
        return None


def webview2_version() -> str | None:
    """Installed Edge WebView2 Runtime version, or None if absent.

    pywebview silently falls back to the deprecated MSHTML (Internet Explorer)
    renderer when the runtime is missing - even if Edge is requested explicitly -
    so we must detect this ourselves and fail loudly instead.
    """
    if sys.platform != "win32":
        return None
    for guid in _WEBVIEW2_CLIENT_KEYS:
        for hive, prefix in (
            ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"),
            ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
            ("HKEY_CURRENT_USER", r"SOFTWARE\Microsoft\EdgeUpdate\Clients"),
        ):
            version = _read_pv(hive, rf"{prefix}\{guid}")
            if version:
                return version
    return None


def fatal_dialog(title: str, message: str) -> None:
    """Show a blocking error box. The app may be running under pythonw (no
    console), so a dialog is the only way an operator will ever see a startup
    failure."""
    if sys.platform != "win32":
        print(f"{title}: {message}", file=sys.stderr)
        return
    import ctypes

    MB_OK, MB_ICONERROR = 0x0, 0x10
    ctypes.windll.user32.MessageBoxW(None, message, title, MB_OK | MB_ICONERROR)
