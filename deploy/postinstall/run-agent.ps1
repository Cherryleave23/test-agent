<#
.SYNOPSIS
    母婴智能顾问 - 运行脚本
.DESCRIPTION
    加载 .env.local 环境变量并启动 Agent 进程。
    由 configure.ps1 配置完成后调用，或日常手动启动。
#>

param(
    [string]$InstallDir = "$env:LOCALAPPDATA\MaternalAgent"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $InstallDir
$EnvFile = Join-Path $ProjectRoot ".env.local"

# ── 加载环境变量 ──
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "Env:$name" -Value $value
            Write-Host "[env] $name = $(if ($name -match 'KEY|TOKEN') { '***' } else { $value })" -ForegroundColor DarkGray
        }
    }
} else {
    Write-Host "[警告] 未找到 .env.local，使用 enterprise.yaml 默认值" -ForegroundColor Yellow
    Write-Host "  请先运行 configure.ps1 进行配置" -ForegroundColor Yellow
}

# ── 启动 Agent ──
$python = Join-Path $InstallDir "python\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"  # 回退到系统 Python
}

$configPath = Join-Path $ProjectRoot "deploy\enterprise.yaml"
$env:PYTHONPATH = $ProjectRoot

Write-Host "`n启动母婴智能顾问..." -ForegroundColor Cyan
Write-Host "  Python:    $python"
Write-Host "  配置:      $configPath"
Write-Host "  工作目录:  $ProjectRoot"
Write-Host ""

& $python (Join-Path $ProjectRoot "src\main.py") $configPath
