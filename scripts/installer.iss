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
; Картинки шагов, 1× и 2× (экраны 150–200 %) — scripts/build_art.py из design/installer.
; Здесь — «Добро пожаловать» и «Папка установки»; остальные шаги меняет [Code] ниже.
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
; Картинки шагов — только для мастера, в папку программы не копируются. Стоят ПЕРВЫМИ:
; при сплошном сжатии достать файл из конца архива = распаковать всё до него.
Source: "..\assets\art\installer\wizard_small*.bmp"; Flags: dontcopy
Source: "..\assets\art\installer\step_*.bmp"; Flags: dontcopy
Source: "..\assets\art\installer\wizard_finish*.bmp"; Flags: dontcopy
Source: "..\dist\JARVIS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; NOTE: Don't use "Flags: ignoreversion" on any shared system files

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
russian.CreditMade=Создал JARVIS:
english.CreditMade=JARVIS is made by
russian.CreditWrite=Написать автору:
english.CreditWrite=Say hi:

[Code]
const
  CreditNick = '@atabekovch';
  CreditTelegram = 'https://t.me/atabekovch';
  CreditInstagram = 'https://www.instagram.com/atabekovch/';
  LinkColor = $7E8C12;   { бирюзовый потемнее, чтобы читался на белом; цвет в Inno — BGR }

var
  CreditMade, CreditNickLabel, CreditWrite, CreditTg, CreditDot, CreditIg: TNewStaticText;

{ Своя картинка у каждого шага. Окно больше обычного (экран 150–200 %) — берём 2×. }
procedure ShowStepImage(Img: TBitmapImage; Name: String; BaseWidth: Integer);
var
  F, Path: String;
begin
  if Img.Width > BaseWidth then
    F := Name + '_2x.bmp'
  else
    F := Name + '.bmp';
  Path := ExpandConstant('{tmp}\') + F;
  try
    if not FileExists(Path) then
      ExtractTemporaryFile(F);
    Img.Bitmap.LoadFromFile(Path);
  except
    Log('Картинка шага не загрузилась: ' + F + ': ' + GetExceptionMessage);
  end;
end;

procedure OpenLink(Url: String);
var
  Code: Integer;
begin
  ShellExec('open', Url, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure CreditClick(Sender: TObject);
begin
  if Sender = CreditIg then
    OpenLink(CreditInstagram)
  else
    OpenLink(CreditTelegram);
end;

function CreditText(Caption: String; Link: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := WizardForm.FinishedPage;
  Result.Caption := Caption;
  if Link then
  begin
    Result.Font.Color := LinkColor;
    Result.Font.Style := [fsBold];
    Result.Cursor := crHand;
    Result.OnClick := @CreditClick;
  end;
end;

{ Подпись автора внизу последнего экрана: «Создал JARVIS: @atabekovch»,
  ниже — ссылки на Telegram и Instagram (ник ведёт в Telegram). }
procedure PlaceCredit;
var
  X, Y: Integer;
begin
  X := WizardForm.FinishedLabel.Left;
  Y := WizardForm.FinishedPage.Height - ScaleY(44);
  CreditMade.SetBounds(X, Y, CreditMade.Width, CreditMade.Height);
  CreditNickLabel.SetBounds(X + CreditMade.Width + ScaleX(4), Y, CreditNickLabel.Width, CreditNickLabel.Height);
  Y := Y + CreditMade.Height + ScaleY(6);
  CreditWrite.SetBounds(X, Y, CreditWrite.Width, CreditWrite.Height);
  X := X + CreditWrite.Width + ScaleX(4);
  CreditTg.SetBounds(X, Y, CreditTg.Width, CreditTg.Height);
  X := X + CreditTg.Width;
  CreditDot.SetBounds(X, Y, CreditDot.Width, CreditDot.Height);
  X := X + CreditDot.Width;
  CreditIg.SetBounds(X, Y, CreditIg.Width, CreditIg.Height);
end;

procedure InitializeWizard;
begin
  CreditMade := CreditText(CustomMessage('CreditMade'), False);
  CreditNickLabel := CreditText(CreditNick, True);
  CreditWrite := CreditText(CustomMessage('CreditWrite'), False);
  CreditTg := CreditText('Telegram', True);
  CreditDot := CreditText('  ·  ', False);
  CreditIg := CreditText('Instagram', True);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  case CurPageID of
    wpSelectDir:   ShowStepImage(WizardForm.WizardSmallBitmapImage, 'wizard_small', 55);
    wpSelectTasks: ShowStepImage(WizardForm.WizardSmallBitmapImage, 'step_tasks', 55);
    wpReady:       ShowStepImage(WizardForm.WizardSmallBitmapImage, 'step_ready', 55);
    wpInstalling:  ShowStepImage(WizardForm.WizardSmallBitmapImage, 'step_installing', 55);
    wpFinished:
      begin
        ShowStepImage(WizardForm.WizardBitmapImage2, 'wizard_finish', 164);
        PlaceCredit;
      end;
  end;
end;
