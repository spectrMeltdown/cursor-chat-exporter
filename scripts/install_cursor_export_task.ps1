#!/usr/bin/env pwsh
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$Runner = Join-Path $ScriptDir "run_cursor_export.ps1"
$EnvFile = if ($env:CURSOR_EXPORT_ENV_FILE) { $env:CURSOR_EXPORT_ENV_FILE } else { Join-Path $BaseDir ".env" }
$TaskName = if ($env:CURSOR_EXPORT_TASK_NAME) { $env:CURSOR_EXPORT_TASK_NAME } else { "cursor-chat-export" }
$TaskTime = if ($env:CURSOR_EXPORT_TASK_TIME) { $env:CURSOR_EXPORT_TASK_TIME } else { "00:05" }

function Write-StderrAndExit {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][int]$Code
    )
    [Console]::Error.WriteLine($Message)
    exit $Code
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-StderrAndExit "Environment file not found: $EnvFile" 11
}

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    Write-StderrAndExit "Runner script not found: $Runner" 11
}

try {
    $startBoundary = [DateTime]::ParseExact($TaskTime, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
}
catch {
    Write-StderrAndExit "Invalid CURSOR_EXPORT_TASK_TIME (expected HH:mm): $TaskTime" 11
}

$escapedEnvFile = $EnvFile.Replace("'", "''")
$escapedRunner = $Runner.Replace("'", "''")
$psCommand = "`$env:CURSOR_EXPORT_ENV_FILE='$escapedEnvFile'; & '$escapedRunner' --bootstrap-mode baseline"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command ""$psCommand"""
$trigger = New-ScheduledTaskTrigger -Daily -At $startBoundary

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Cursor chat delta export" -Force | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "Installed scheduled task: $TaskName"
Write-Output "Task state: $($task.State)"
Write-Output "Next run time: $($info.NextRunTime)"
