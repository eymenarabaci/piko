#requires -Version 5.1
<#
.SYNOPSIS
    External watchdog for the Piko unattended driver (Phase 4).

.DESCRIPTION
    Intended to run from Windows Task Scheduler every 2 minutes:
        powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1

    If the daemon heartbeat is older than -HeartbeatMaxAgeSeconds (default 5 min)
    the watchdog starts the daemon. If -RestartLimit (default 3) restarts were
    already recorded inside -RestartWindowMinutes (default 10), it stops
    restarting and writes the tail of the daemon logs to piko-crash.log.

    -DryRun prints the decisions it would make and executes nothing: no process
    start, no restart record, no crash-log write.

    The daemon command is variable, see -DaemonCommand / -DaemonModule below.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$DbPath = "",
    [string]$DaemonLogPath = "",
    [string]$CrashLogPath = "",
    [string]$PythonExe = "",
    [string]$DaemonModule = "manager.daemon",
    [string[]]$DaemonCommand = @(),
    [int]$HeartbeatMaxAgeSeconds = 300,
    [int]$RestartWindowMinutes = 10,
    [int]$RestartLimit = 3
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

if (-not $DbPath) { $DbPath = Join-Path $ProjectRoot "data\health.db" }
if (-not $DaemonLogPath) { $DaemonLogPath = Join-Path $ProjectRoot "data\daemon.log" }
if (-not $CrashLogPath) { $CrashLogPath = Join-Path $ProjectRoot "piko-crash.log" }
if (-not $PythonExe) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $PythonExe = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }
}
if ($DaemonCommand.Count -eq 0) { $DaemonCommand = @("-m", $DaemonModule) }

$DaemonErrorLogPath = "$DaemonLogPath.err"
# The daemon writes data\daemon.log through its own rotating file handler.
# Start-Process redirection truncates its target on every start, so the raw
# streams are captured in sibling files instead of wiping the real log.
$DaemonStartLogPath = "$DaemonLogPath.start"
$env:PIKO_HEALTH_DB = $DbPath

$reportProbe = @'
import json
from manager.health import HealthMonitor
print(json.dumps(HealthMonitor().report(), sort_keys=True))
'@

$restartProbe = @'
import json
from manager.health import record_restart
print(json.dumps(record_restart(), sort_keys=True))
'@

$errorProbe = @'
from manager.health import record_error
record_error("watchdog", "restart loop exhausted; watchdog stopped restarting the daemon")
'@

function Invoke-HealthCode {
    param([Parameter(Mandatory = $true)][string]$Code)
    # Feed the program on stdin: native argument quoting on Windows would
    # otherwise strip quotes from inline Python source.
    $raw = $Code | & $PythonExe - 2>$null
    if ($LASTEXITCODE -ne 0) { throw "health helper failed (exit $LASTEXITCODE)" }
    return ($raw | Out-String).Trim()
}

function Get-DaemonTail {
    $lines = @()
    foreach ($log in @($DaemonLogPath, $DaemonErrorLogPath, $DaemonStartLogPath)) {
        if (Test-Path -LiteralPath $log) {
            $lines += "--- tail: $log ---"
            $lines += @(Get-Content -LiteralPath $log -Tail 120 -ErrorAction SilentlyContinue)
        }
    }
    if ($lines.Count -eq 0) { $lines = @("(no daemon log found)") }
    return $lines
}

$exitCode = 0
Push-Location -LiteralPath $ProjectRoot
try {
    $report = $null
    if (Test-Path -LiteralPath $DbPath) {
        $raw = Invoke-HealthCode -Code $reportProbe
        if (-not $raw) { throw "health probe returned no data" }
        $report = $raw | ConvertFrom-Json
    }
    else {
        Write-Output "health db not found at $DbPath; treating daemon as absent"
        $report = [pscustomobject]@{
            daemon = $null
            daemon_alive = $false
            restart_count = 0
            stale_runs = @()
            last_error = $null
        }
    }

    $restartCount = 0
    if ($null -ne $report.restart_count) { $restartCount = [int]$report.restart_count }

    $daemonAge = [double]::PositiveInfinity
    if ($null -ne $report.daemon -and $null -ne $report.daemon.age_seconds) {
        $daemonAge = [double]$report.daemon.age_seconds
    }
    $daemonStale = ($null -eq $report.daemon) -or ($daemonAge -gt $HeartbeatMaxAgeSeconds)
    $killSwitch = $restartCount -ge $RestartLimit

    if (-not $daemonStale) {
        $ageText = "{0:N1}" -f $daemonAge
        Write-Output "OK: daemon heartbeat is fresh (age ${ageText}s <= ${HeartbeatMaxAgeSeconds}s)"
        if ($DryRun) { Write-Output "[DryRun] decision: do nothing" }
        $exitCode = 0
    }
    elseif ($killSwitch) {
        $reason = "restart loop: $restartCount restart(s) in the last $RestartWindowMinutes min (limit $RestartLimit)"
        Write-Output "KILL SWITCH: $reason; not starting the daemon"
        if ($DryRun) {
            Write-Output "[DryRun] decision: write crash report to $CrashLogPath and do not start the daemon"
            Get-DaemonTail | ForEach-Object { Write-Output "[DryRun][tail] $_" }
        }
        else {
            $tail = Get-DaemonTail
            $header = @(
                "=== Piko watchdog crash report ===",
                "time: $((Get-Date).ToUniversalTime().ToString('o'))",
                "reason: $reason",
                "daemon_age_seconds: $daemonAge",
                "health_db: $DbPath"
            )
            Add-Content -LiteralPath $CrashLogPath -Value ($header + $tail) -Encoding UTF8
            try {
                Invoke-HealthCode -Code $errorProbe | Out-Null
            }
            catch {
                Write-Warning "could not record watchdog error: $($_.Exception.Message)"
            }
            Write-Output "crash report appended to $CrashLogPath"
        }
        $exitCode = 0
    }
    else {
        $commandText = "$PythonExe $($DaemonCommand -join ' ')"
        Write-Output "STALE: daemon heartbeat missing/old (age ${daemonAge}s > ${HeartbeatMaxAgeSeconds}s)"
        if ($DryRun) {
            Write-Output "[DryRun] decision: record restart ($($restartCount + 1)/$RestartLimit in $RestartWindowMinutes min) and start: $commandText"
            $exitCode = 0
        }
        else {
            $restartJson = Invoke-HealthCode -Code $restartProbe
            $restart = $restartJson | ConvertFrom-Json
            Write-Output "restart recorded: $($restart.count)/$RestartLimit in $RestartWindowMinutes min"
            $process = Start-Process -FilePath $PythonExe `
                -ArgumentList $DaemonCommand `
                -WorkingDirectory $ProjectRoot `
                -RedirectStandardOutput $DaemonStartLogPath `
                -RedirectStandardError $DaemonErrorLogPath `
                -WindowStyle Hidden `
                -PassThru
            Write-Output "daemon start requested (pid $($process.Id)): $commandText"
            $exitCode = 0
        }
    }
}
catch {
    Write-Error "watchdog failed: $($_.Exception.Message)"
    $exitCode = 1
}
finally {
    Pop-Location
}

exit $exitCode
