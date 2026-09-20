"""Guard: nothing key-shaped may exist in any file git could commit.

Checks every tracked file and every untracked-but-not-ignored file (i.e. exactly
what `git add .` would stage). Protects Security Checklist B1/F1 and Solution
Plan Section 1.1 - the Storage Account access key lives only in a git-ignored .env.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Patterns are assembled from fragments so this file does not match itself.
PATTERNS = {
    "storage connection string": re.compile("Account" + "Key=", re.IGNORECASE),
    "storage key assignment": re.compile(r"STORAGE_ACCOUNT_KEY\s*=\s*\S{20,}"),
    "azure account key (88-char base64)": re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{86}==(?![A-Za-z0-9+/=])"),
    "SAS token": re.compile(r"[?&]sig=[A-Za-z0-9%+/=]{20,}"),
}

TEXT_SUFFIXES = {
    "", ".py", ".md", ".txt", ".json", ".csv", ".ini", ".cfg", ".toml", ".yml",
    ".yaml", ".bat", ".ps1", ".html", ".css", ".js", ".example", ".env",
}


def _committable_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available / not a git checkout")
    return [ROOT / p for p in out.split("\0") if p]


def test_no_secrets_in_committable_files():
    findings = []
    for path in _committable_files():
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    assert not findings, "Possible secret in a committable file: " + "; ".join(findings)


def test_env_files_are_git_ignored():
    for name in (".env", ".env.local", "OBSERVATIONS.txt"):
        r = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT)
        assert r.returncode == 0, f"{name} must be git-ignored"
