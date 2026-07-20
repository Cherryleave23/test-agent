<#
.SYNOPSIS
    母婴智能顾问 - 注册开机自启（Windows 计划任务）
.DESCRIPTION
    注册一个 Windows 计划任务，开机后自动启动 Agent 进程。
    使用 SYSTEM 账户运行，无需登录即可启动。
#>

param(
    [string]$InstallDir = "$env:LOCALAPPDATA\MaternalAgent",
    [string]$TaskName = "MaternalAgent"
)

$ErrorActionPreference = "Stop"

$runScript = Join-Path $InstallDir "deploy\postinstall\run-agent.ps1"
if (-not (Test-Path $runScript)) {
    Write-Host "[错误] 未找到运行脚本: $runScript" -ForegroundColor Red
    exit 1
}

# 检查是否已注册
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[警告] 计划任务 '$TaskName' 已存在" -ForegroundColor Yellow
    $overwrite = Read-Host "是否覆盖？(y/N)"
    if ($overwrite -ne "y" -and $overwrite -ne "Y") {
        Write-Host "已取消" -ForegroundColor Yellow
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 注册计划任务
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`" -InstallDir `"$InstallDir`""

$trigger = New-ScheduledTaskTrigger -AtStartup

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "母婴智能顾问 Agent（开机自启）"

Write-Host "[OK] 已注册开机自启: $TaskName" -ForegroundColor Green
Write-Host ""
Write-Host "管理命令:" -ForegroundColor White
Write-Host "  查看状态:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  手动启动:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  停止:      Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  取消自启:  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
