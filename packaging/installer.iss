; Stable AppId is shared by all versions so upgrades reuse the existing installation.
#ifndef AppVersion
  #error AppVersion must be supplied by the release build
#endif
#ifndef PayloadDir
  #error PayloadDir must be the validated PyInstaller onedir folder
#endif
#ifndef InstallerOutputDir
  #error InstallerOutputDir must be supplied by the release build
#endif
#define AppName "SPD Report Generator"
#define AppExe "SPD Report Generator.exe"

[Setup]
AppId={{B6C9C279-1BA4-45F7-ACFA-0E3D386B13CB}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=yunhyok
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile={#PayloadDir}\LICENSE
OutputDir={#InstallerOutputDir}
OutputBaseFilename=SPD-Report-Generator-v{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
VersionInfoVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName}\Help"; Filename: "{app}\help.html"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Default uninstall removes only files recorded during installation and empty
; directories. User-created reports and projects are deliberately not enumerated.
