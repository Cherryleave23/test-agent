; 母婴智能顾问 - Inno Setup 安装脚本
; 构建：iscc maternal-agent.iss
; 输出：maternal-agent-setup.exe（~80MB，不含 torch/模型）
;
; 设计：安装包只含轻量核心，重型依赖通过 configure.ps1 按需拉取/导入

#define MyAppName "母婴智能顾问"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Cherryleave"
#define MyAppExeName "maternal-agent"
#define PythonVersion "3.11.9"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\MaternalAgent
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=build
OutputBaseFilename=maternal-agent-setup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico

; 安装后自动运行配置向导
[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\postinstall\configure.ps1"" -InstallDir ""{app}"""; Description: "运行配置向导"; Flags: postinstall nowait skipifsilent runascurrentuser

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\plugins\models"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\src\__pycache__"
Type: filesandordirs; Name: "{app}\src\common\__pycache__"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
; ── Python embeddable（离线内置，~15MB）──
Source: "deps\python-3.11.9-embed-amd64.zip"; DestDir: "{tmp}"; Flags: deleteafterinstall

; ── 轻量 pip wheels（离线内置，~200MB）──
Source: "deps\wheels-light\*"; DestDir: "{app}\deps\wheels-light"; Flags: recursesubdirs

; ── 业务代码 ──
Source: "..\..\src\*"; DestDir: "{app}\src"; Flags: recursesubdirs
Source: "..\..\scripts\*"; DestDir: "{app}\scripts"; Flags: recursesubdirs

; ── 部署配置 ──
Source: "..\enterprise.yaml"; DestDir: "{app}\deploy"; Flags: onlyifdoesntexist
Source: "..\requirements-light.txt"; DestDir: "{app}\deploy"
Source: "..\requirements-full.txt"; DestDir: "{app}\deploy"

; ── 插件清单 ──
Source: "..\..\plugins\manifest.yaml"; DestDir: "{app}\plugins"

; ── 后安装脚本 ──
Source: "..\postinstall\configure.ps1"; DestDir: "{app}\deploy\postinstall"
Source: "..\postinstall\run-agent.ps1"; DestDir: "{app}\deploy\postinstall"
Source: "..\postinstall\register-service.ps1"; DestDir: "{app}\deploy\postinstall"

; ── 图标 ──
Source: "assets\icon.ico"; DestDir: "{app}\assets"

[Icons]
Name: "{group}\母婴智能顾问"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\postinstall\run-agent.ps1"" -InstallDir ""{app}"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\配置向导"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\postinstall\configure.ps1"" -InstallDir ""{app}"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\开机自启"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\postinstall\register-service.ps1"" -InstallDir ""{app}"""; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\卸载"; Filename: "{uninstallexe}"
Name: "{autodesktop}\母婴智能顾问"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\deploy\postinstall\run-agent.ps1"" -InstallDir ""{app}"""; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon

[Code]
procedure InitializeSetup;
begin
  // 确保是 64 位 Windows
  if not IsWin64 then
    Abort;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PythonDir: String;
  WheelsDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    // 1. 解压 Python embeddable
    PythonDir := ExpandConstant('{app}\python');
    CreateDir(PythonDir);
    Exec(ExpandConstant('{tmp}\python-3.11.9-embed-amd64.zip'), '-o "' + PythonDir + '" -y',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // 2. 安装轻量 wheels（离线）
    WheelsDir := ExpandConstant('{app}\deps\wheels-light');
    Exec(PythonDir + '\python.exe', '-m pip install --no-index --find-links "' +
      WheelsDir + '" pydantic pyyaml chromadb httpx',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    // 3. 创建 data 目录
    CreateDir(ExpandConstant('{app}\data'));
    CreateDir(ExpandConstant('{app}\plugins\models'));
  end;
end;
