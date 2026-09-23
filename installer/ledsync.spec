# PyInstaller spec (Phase 13) - builds TWO executables from one shared bundle:
#   LEDAssetSync.exe            the windowed application (installer/run_gui.py)
#   LEDAssetSyncScheduled.exe   the headless scheduled-run entry point (installer/run_scheduled.py)
#
# Two executables, not one dispatching on an argv flag, so the shipped LEDAssetSyncScheduled.exe
# genuinely cannot import pywebview - see services/scheduler.py's SCHEDULED_RUN_EXE_NAME comment
# and installer/run_scheduled.py. Both share one COLLECT step (dependencies bundled once, not
# twice) - this is PyInstaller's standard "one spec, multiple EXEs" pattern.
#
# Build from the project root:  .venv\Scripts\pyinstaller installer\ledsync.spec --distpath dist --workpath build
# Output: dist\LEDAssetSync\LEDAssetSync.exe and dist\LEDAssetSync\LEDAssetSyncScheduled.exe
# (a "onedir" build, not "onefile" - Inno Setup packages the whole folder; onedir starts faster
# and makes a bad build easier to inspect than a self-extracting onefile exe would).

import sys
from pathlib import Path

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # installer/ -> project root

WEB_ROOT = PROJECT_ROOT / "ledsync" / "web"
DATAS = [
    (str(WEB_ROOT / "templates"), "ledsync/web/templates"),
    (str(WEB_ROOT / "static"), "ledsync/web/static"),
]

# The Azure SDKs' HTTP pipeline/auth machinery and pywebview's Windows backend both do a fair
# amount of dynamic/conditional importing that static analysis can miss - the webview hidden
# imports mirror what pyinstaller-hooks-contrib's own hook-webview.py already covers, listed here
# too so a future hook change or a `--exclude-module` mistake fails loudly at build time rather
# than silently at runtime on the venue machine.
HIDDEN_IMPORTS = [
    "clr_loader", "pythonnet",
    "azure.communication.email", "azure.storage.blob", "azure.core",
    "win32timezone",  # a common pywin32 gap PyInstaller misses
]

gui_a = Analysis(
    [str(PROJECT_ROOT / "installer" / "run_gui.py")],
    pathex=[str(PROJECT_ROOT)],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    excludes=[],
    noarchive=False,
)

scheduled_a = Analysis(
    [str(PROJECT_ROOT / "installer" / "run_scheduled.py")],
    pathex=[str(PROJECT_ROOT)],
    datas=[],  # the headless entry point never serves templates/static - see its own docstring
    hiddenimports=["azure.communication.email", "azure.storage.blob", "azure.core"],
    hookspath=[],
    excludes=["webview", "clr_loader", "pythonnet"],  # belt-and-braces: must never sneak in here
    noarchive=False,
)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
scheduled_pyz = PYZ(scheduled_a.pure, scheduled_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz, gui_a.scripts, [],
    exclude_binaries=True,
    name="LEDAssetSync",
    console=False,      # windowed - no console flash behind the WebView2 window
    icon=None,           # TODO: a real .ico from the owner's brand assets (WTT-Logo.png is not one)
)

scheduled_exe = EXE(
    scheduled_pyz, scheduled_a.scripts, [],
    exclude_binaries=True,
    name="LEDAssetSyncScheduled",
    console=True,        # a manual/debug run shows its own output; Task Scheduler's LogonType=Password
                         # runs it in a non-interactive session regardless of this flag
    icon=None,
)

# One COLLECT combining both executables' dependencies into a single install folder - the two EXEs
# above only contain each one's own bytecode; everything else (the Python runtime, shared and
# per-exe third-party packages, the datas above) lands once in dist\LEDAssetSync\.
coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.zipfiles, gui_a.datas,
    scheduled_exe, scheduled_a.binaries, scheduled_a.zipfiles, scheduled_a.datas,
    strip=False,
    upx=False,          # UPX-compressed WebView2/CLR-adjacent DLLs are a common source of false
                        # antivirus positives on a venue machine - not worth the smaller install size.
    name="LEDAssetSync",
)
