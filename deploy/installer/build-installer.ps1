<#
.SYNOPSIS
    母婴智能顾问 - 安装包构建编排
.DESCRIPTION
    自动化构建 EXE 安装包：
      1. 下载 Python 3.11 embeddable（如未缓存）
      2. 下载轻量 pip wheels（离线安装用）
      3. 调用 Inno Setup 编译 .iss → 输出 maternal-agent-setup.exe

    前置条件：安装 Inno Setup 6（https://jrsoftware.org/isdl.php）
    运行：.\build-installer.ps1
#>

param(
    [string]$PythonVersion = "3.11.9",
    [string]$OutputDir = "build"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallerDir = $ScriptDir
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $InstallerDir)

# ── 工具函数 ──

function Write-Step($text) {
    Write-Host "[构建] $text" -ForegroundColor Cyan
}

function Write-OK($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Write-Err($text) {
    Write-Host "[错误] $text" -ForegroundColor Red
}

# ── 1. 准备依赖目录 ──

$DepsDir = Join-Path $InstallerDir "deps"
$WheelsDir = Join-Path $DepsDir "wheels-light"
New-Item -ItemType Directory -Path $DepsDir -Force | Out-Null
New-Item -ItemType Directory -Path $WheelsDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $InstallerDir $OutputDir) -Force | Out-Null

# ── 2. 下载 Python embeddable ──

$PythonZip = Join-Path $DepsDir "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"

if (-not (Test-Path $PythonZip)) {
    Write-Step "下载 Python $PythonVersion embeddable..."
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip -UseBasicParsing
    Write-OK "Python embeddable 下载完成"
} else {
    Write-OK "Python embeddable 已缓存"
}

# ── 3. 下载轻量 pip wheels ──

Write-Step "下载轻量 pip wheels（离线安装用）..."
$LightReqs = Join-Path $ProjectRoot "deploy\requirements-light.txt"

# 使用当前 Python 下载 wheels（构建机需要装 Python）
$buildPython = "python"
$wheelsCount = (Get-ChildItem $WheelsDir -ErrorAction SilentlyContinue | Measure-Object).Count
if ($wheelsCount -eq 0) {
    & $buildPython -m pip download -r $LightReqs -d $WheelsDir
    Write-OK "轻量 wheels 下载完成"
} else {
    Write-OK "轻量 wheels 已缓存（$wheelsCount 个文件）"
}

# ── 4. 检查 Inno Setup ──

$InnoSetup = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoSetup)) {
    $InnoSetup = "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $InnoSetup)) {
    Write-Err "未找到 Inno Setup 6，请安装：https://jrsoftware.org/isdl.php"
    Write-Host "  安装后重新运行本脚本"
    exit 1
}

# ── 5. 编译安装包 ──

$IssFile = Join-Path $InstallerDir "maternal-agent.iss"
Write-Step "编译安装包: $IssFile"
& $InnoSetup $IssFile

if ($LASTEXITCODE -eq 0) {
    $outputExe = Join-Path $InstallerDir "$OutputDir\maternal-agent-setup.exe"
    $size = [math]::Round((Get-Item $outputExe).Length / 1MB, 1)
    Write-OK "安装包构建完成: $outputExe ($size MB)"
    Write-Host ""
    Write-Host "分发方式:" -ForegroundColor White
    Write-Host "  1. 将 maternal-agent-setup.exe 拷贝到门店电脑"
    Write-Host "  2. 双击运行 → 安装后自动弹出配置向导"
    Write-Host "  3. 配置向导中选择模式 → 按需拉取插件 → 启动"
} else {
    Write-Err "Inno Setup 编译失败"
    exit 1
}
