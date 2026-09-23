; Inno Setup script (Phase 13) - BRD Section 27 (Installation Requirements), Step 13.1/13.2.
; Requires the Inno Setup Compiler (iscc.exe, https://jrsoftware.org/isdl.php) and a build already
; produced by installer\ledsync.spec (see that file's own header for the exact command). Compile
; from the project root:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\ledsync.iss
; Output: installer\output\LEDAssetSync-Setup-<version>.exe
;
; BRD Addendum A 39.4 (confirmed 20 Sep 2026): the installer is distributed via the
; `desktopinstaller` blob container; code-signing is NOT required.
;
; --- Fresh install vs. upgrade (BRD 13.1/13.2) --------------------------------------------------
; [Files] below only ever touches the INSTALL folder ({app}, e.g.
; %LocalAppData%\Programs\LEDAssetSync) - never the DATA folder (%LocalAppData%\LEDAssetSync,
; config.py's own default_data_dir(), where ledsync.db/mappings/operational history actually live).
; Those are two different folders by design, so an upgrade (re-running this installer over an
; existing install) automatically satisfies "preserve the existing database" - there is no special
; upgrade-path code needed here at all for that guarantee; it falls out of never pointing [Files] at
; the data folder.
;
; --- The pre-install configuration file (owner request, 23 Sep 2026) ---------------------------
; An operator preparing several venue machines edits ONE `install-config.json`, placed next to
; THIS COMPILED Setup.exe (a plain file the operator can change without ever recompiling the
; installer - e.g. on the same USB drive/network share as Setup.exe). If present, and only on a
; genuinely fresh install (see IsFreshInstall below), it is applied via
; `LEDAssetSync.exe --seed-config` right after the files are copied - see ledsync/services/
; install_config.py for the full field list and validation (never a Windows account password -
; that is refused outright, see that module).
; Defense in depth: install_config.py's own should_seed() independently refuses to touch a
; database that already has ANY saved setting, regardless of what IsFreshInstall below decides -
; so a wrong fresh/upgrade guess here can never overwrite a configured installation.
;
; --- Account context (documented, not automatically solved) ------------------------------------
; PrivilegesRequired=lowest below means Setup (and therefore the --seed-config step) runs as
; whichever Windows account launches Setup.exe, with no elevation - so the seeded database lands
; in THAT account's own data folder. This is almost always correct (the same operator installs
; and uses the application day to day). Scheduling under a SEPARATE, dedicated unattended account
; (Addendum A 39.6) is unaffected either way - Phase 12's Settings -> Scheduling page bakes the
; real data folder into the Task at the moment scheduling is enabled, regardless of which account
; installed the application.

#define MyAppName "LED Asset Download & Sync"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "WTT"
#define MyAppExeName "LEDAssetSync.exe"
#define MyScheduledExeName "LEDAssetSyncScheduled.exe"

[Setup]
; Generated once for this application and never changed - Inno Setup uses this (not the app name)
; to recognise "this is the same application" across versions, which is what makes a later Setup
; run an upgrade instead of a parallel second install. The doubled leading brace is Inno Setup's
; own escape for a literal "{" (a bare "{GUID}" here is parsed as a {constant} reference instead).
AppId={{C9E6C6B0-6C0B-4C6E-9B5B-6C7C7B9D6B10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\LEDAssetSync
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=output
OutputBaseFilename=LEDAssetSync-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
; No SignTool= entry - BRD Addendum A 39.4 confirms code-signing is not required.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; The whole PyInstaller COLLECT output (both executables plus their shared runtime/dependencies).
; Never touches {localappdata}\LEDAssetSync (the DATA folder) - see the header note above.
Source: "..\dist\LEDAssetSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Step 13.1: install Task Scheduler components. Registration itself needs a real account/password
; (never handled by the installer - see services/scheduler.py and Settings -> Scheduling), so
; there is nothing to run here; Settings -> Scheduling already does the actual schtasks.exe
; registration the moment the operator enables it (owner decision, Phase 12), and will keep doing
; so under Phase 13 without any change.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--seed-config ""{code:InstallConfigPath}"""; \
    Flags: runhidden waituntilterminated; StatusMsg: "Applying pre-install configuration..."; \
    Check: ShouldSeedConfig
; Deliberately NO "launch now" entry: live-tested (23 Sep 2026) that Inno Setup's own
; `skipifsilent` flag does not reliably suppress a postinstall launch under /VERYSILENT in every
; environment - not worth the risk of a silent/unattended install unexpectedly opening a window
; against a venue's real data folder. The operator opens the application deliberately, from the
; shortcut, when ready - exactly like any other install with no "run after install" step.

[UninstallDelete]
; The application files only - the DATA folder (database, mappings, operational history, logs) is
; deliberately NOT removed on uninstall, so a reinstall (or "uninstall then reinstall" as a crude
; upgrade) never loses venue data. An operator who genuinely wants a clean slate deletes
; %LocalAppData%\LEDAssetSync by hand.
Type: filesandordirs; Name: "{app}"

[Code]
function InstallConfigPath(Param: String): String;
begin
  Result := ExtractFilePath(ExpandConstant('{srcexe}')) + 'install-config.json';
end;

function IsFreshInstall: Boolean;
begin
  // %LocalAppData%\LEDAssetSync is config.py's default_data_dir() - see the header note above for
  // why this is the DATA folder, distinct from {app} (the install folder [Files] above targets).
  Result := not FileExists(ExpandConstant('{localappdata}\LEDAssetSync\ledsync.db'));
end;

function ShouldSeedConfig: Boolean;
begin
  Result := FileExists(InstallConfigPath('')) and IsFreshInstall;
end;
