; Inno Setup Script for JARVIS Mark X
; Compiles dist/JARVIS into JARVIS_Setup_v<версия>.exe

#define MyAppName "JARVIS Mark X"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppPublisher "JARVIS Team"
#define MyAppURL "https://github.com/Sardorbek505/jarvis-mark-x"
#define MyAppExeName "JARVIS.exe"

[Setup]
; NOTE: The value of AppId uniquely identifies this application.
AppId={{D37F8E32-4821-4B9E-862B-9832B6AF71AA}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
; Output setup file configuration
OutputDir=..\dist
OutputBaseFilename=JARVIS_Setup_v{#MyAppVersion}
SetupIconFile=..\app.ico
; Шар из точек, 1× и 2× (экраны 150–200 %) — scripts/build_art.py
WizardImageFile=..\assets\art\installer\wizard_large.bmp,..\assets\art\installer\wizard_large_2x.bmp
WizardSmallImageFile=..\assets\art\installer\wizard_small.bmp,..\assets\art\installer\wizard_small_2x.bmp
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать JARVIS автоматически при входе в Windows"; GroupDescription: "Автозагрузка:"; Flags: unchecked

[Files]
Source: "..\dist\JARVIS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
