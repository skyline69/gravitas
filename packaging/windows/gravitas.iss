; Inno Setup script for the Gravitas Windows installer.
;
; Build (after `pyinstaller packaging/gravitas.spec`, which produces
; dist/Gravitas/):
;
;   iscc packaging\windows\gravitas.iss
;
; Output: dist/Gravitas-Setup.exe
;
; Installs per user by default -- no UAC prompt, no admin account needed, and
; the stremio:// handler lands in HKCU where it belongs. An admin who runs the
; installer elevated gets the machine-wide install instead (HKLM, Program
; Files); HKA resolves to whichever of the two is in force.

#define AppName "Gravitas"
#define AppVersion "0.1.0"
#define AppPublisher "skyline69"
#define AppURL "https://github.com/skyline69/gravitas"
#define AppExe "gravitas.exe"

[Setup]
; Never change AppId: it is what upgrades and the uninstaller match on.
AppId={{F9D57A4D-FD3B-4540-8613-7A89C7752E9A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=Gravitas-Setup
SetupIconFile=..\icon\gravitas.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Qt 6.11 and the mpv builds are 64-bit only, so refuse a 32-bit host outright
; instead of installing something that cannot start.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; The stremio:// handler below is a shell association: this tells Explorer to
; refresh instead of caching the old (absent) handler.
ChangesAssociations=yes
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller one-dir tree: gravitas.exe plus _internal/, which holds
; Qt, libmpv-2.dll, yt-dlp.exe and the QML.
Source: "..\..\dist\Gravitas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; stremio:// protocol handler -- the "Install" button on every addon site.
; Windows re-executes the app with the URL as argv[1]; main.pending_link picks
; it up, and a running instance receives it over the single-instance pipe.
Root: HKA; Subkey: "Software\Classes\stremio"; ValueType: string; ValueName: ""; ValueData: "URL:Stremio Addon Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\stremio"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\stremio\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\stremio\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

; Registered application metadata, so Gravitas shows up in Settings > Default
; apps rather than only as an anonymous handler.
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Qt's compiled-QML cache under the install dir, if Qt ever wrote one there.
; User data (settings, watchlist, progress) lives in %APPDATA%/%LOCALAPPDATA%
; and is deliberately left behind: uninstalling is not "forget my library".
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
