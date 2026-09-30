# xmu-rollcall 看门狗：监控 xmu start 进程，退出后自动拉起
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File watchdog.ps1
# 注意: xmu start 正常退出（Ctrl+C 或异常）后等 10 秒重启；睡眠期间进程被冻结不误判

# 解析 xmu.exe 路径：PATH 优先（editable install），其次 Python314 Scripts，最后 py 启动器定位
$xmuExe = $null
$cmd = Get-Command xmu.exe -ErrorAction SilentlyContinue
if ($cmd) {
    $xmuExe = $cmd.Source
} else {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Python\Python314\Scripts\xmu.exe"
    )
    try {
        $pyExec = py -c "import sys; print(sys.executable)" 2>$null
        if ($pyExec) {
            $candidates += (Join-Path (Split-Path $pyExec.Trim()) "Scripts\xmu.exe")
        }
    } catch {}
    $xmuExe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $xmuExe) {
    Write-Error "xmu.exe not found; install xmu-rollcall first"
    exit 1
}

# 日志目录：跟随 XMU_ROLLCALL_CONFIG_DIR，否则默认 ~\.xmu_rollcall
$configDir = if ($env:XMU_ROLLCALL_CONFIG_DIR) { $env:XMU_ROLLCALL_CONFIG_DIR }
             else { Join-Path $env:USERPROFILE ".xmu_rollcall" }
$logFile = Join-Path $configDir "watchdog.log"

function Write-Log {
    param([string]$msg)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Write-Log "=== Watchdog started ==="

$restartCount = 0

while ($true) {
    Write-Log "Starting xmu start (attempt $($restartCount + 1))"
    try {
        # --no-browser：自动拉起时不弹浏览器，用户自行打开 127.0.0.1:5000
        $proc = Start-Process -FilePath $xmuExe -ArgumentList "start", "--no-browser" -NoNewWindow -PassThru
        $proc | Wait-Process
        $exitCode = $proc.ExitCode
        Write-Log "xmu exited with code $exitCode"
    }
    catch {
        Write-Log "Failed to start xmu: $_"
    }

    $restartCount++
    Write-Log "Restarting in 10 seconds..."
    Start-Sleep -Seconds 10
}
