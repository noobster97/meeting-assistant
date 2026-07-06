; Inno Setup script for Meeting Assistant — builds a per-user installer (no admin).
; Compile with:  ISCC.exe MeetingAssistant.iss   (output -> dist\MeetingAssistant-Setup.exe)

#define AppName    "Meeting Assistant"
#define AppVersion "1.0.0"
#define AppExe     "MeetingAssistant.exe"

[Setup]
AppId={{7C1B4A2E-9D3F-4E6A-B5C8-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=noobster97
AppPublisherURL=https://github.com/noobster97/meeting-assistant
; Per-user install: no administrator rights, no UAC prompt, and the install
; folder stays user-writable (the app saves .env / config.json / reports next to itself).
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=dist
OutputBaseFilename=MeetingAssistant-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Icon + version metadata on the installer itself (reduces heuristic AV false positives).
SetupIconFile=appicon.ico
VersionInfoVersion={#AppVersion}
VersionInfoCompany=noobster97
VersionInfoProductName={#AppName}
VersionInfoDescription=Meeting Assistant Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\MeetingAssistant\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
