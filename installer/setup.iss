; Inno Setup 6 安装与卸载脚本 —— 外设电量查看器 (PeripheralBatteryViewer)
; 使用方法：安装 Inno Setup 6 后运行 iscc installer\setup.iss（或先运行 build_exe.bat 生成上层 exe）

#define MyAppName "外设电量查看器"
#define MyAppEnName "PeripheralBatteryViewer"
#define MyAppVersion "1.1.0"
#define MyAppExeName "外设电量查看器.exe"

[Setup]
AppId={{8E94B2B0-7E3A-4F8B-A3C9-4C81A0192D40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppEnName}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist_installer
OutputBaseFilename=外设电量查看器-v{#MyAppVersion}-Setup
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "autostart"; Description: "开机自动在后台启动并驻留系统托盘（含守护进程）"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "..\..\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\2.4G外设电量托盘"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--watchdog --minimized"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--quit"; Flags: runhidden waituntilterminated; Check: FileExists(ExpandConstant('{app}\{#MyAppExeName}'))
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--quit"; Flags: runhidden waituntilterminated; RunOnceId: "QuitApp"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := ExpandConstant('{userappdata}\{#MyAppEnName}');
    if DirExists(DataPath) then
    begin
      if MsgBox('是否同时清理保存的外设配置、电量历史与告警记录？' + #13#10 + DataPath, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(DataPath, True, True, True);
      end;
    end;
  end;
end;
