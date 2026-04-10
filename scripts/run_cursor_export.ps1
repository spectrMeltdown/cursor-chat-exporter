#!/usr/bin/env pwsh
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$Exporter = Join-Path $ScriptDir "export_cursor_chat_deltas.py"
$EnvFile = if ($env:CURSOR_EXPORT_ENV_FILE) { $env:CURSOR_EXPORT_ENV_FILE } else { Join-Path $BaseDir ".env" }
$LockFile = if ($env:CURSOR_EXPORT_LOCK_FILE) { $env:CURSOR_EXPORT_LOCK_FILE } else { Join-Path $env:TEMP "cursor-chat-export.lock" }
$PythonBin = if ($env:CURSOR_EXPORT_PYTHON_BIN) { $env:CURSOR_EXPORT_PYTHON_BIN } else { "python" }

function Write-StderrAndExit {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][int]$Code
    )
    [Console]::Error.WriteLine($Message)
    exit $Code
}

function Import-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#")) {
            continue
        }

        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) {
            continue
        }

        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim()
        if (($value.StartsWith("'") -and $value.EndsWith("'")) -or ($value.StartsWith('"') -and $value.EndsWith('"'))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Test-IsAbsolutePath {
    param([Parameter(Mandatory = $true)][string]$PathValue)
    return [System.IO.Path]::IsPathRooted($PathValue)
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-StderrAndExit "Environment file not found: $EnvFile" 11
}

Import-DotEnv -Path $EnvFile

$requiredVars = @(
    "CURSOR_TRANSCRIPTS_ROOT",
    "CURSOR_EXPORT_OUTPUT_ROOT",
    "CURSOR_EXPORT_STATE_DIR"
)

foreach ($varName in $requiredVars) {
    $value = [System.Environment]::GetEnvironmentVariable($varName, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-StderrAndExit "Missing required environment variable: $varName" 10
    }
    if (-not (Test-IsAbsolutePath -PathValue $value)) {
        Write-StderrAndExit "Environment variable must be an absolute path: $varName=$value" 11
    }
    if (-not (Test-Path -LiteralPath $value -PathType Container)) {
        Write-StderrAndExit "Configured path does not exist or is not a directory: $varName=$value" 11
    }
}

try {
    Get-Command -Name $PythonBin -ErrorAction Stop | Out-Null
}
catch {
    Write-StderrAndExit "Python binary not found: $PythonBin" 11
}

$outputRoot = [System.Environment]::GetEnvironmentVariable("CURSOR_EXPORT_OUTPUT_ROOT", "Process")
$logDir = Join-Path $outputRoot "logs"
[System.IO.Directory]::CreateDirectory($logDir) | Out-Null
$logFile = Join-Path $logDir "export.log"

$lockDir = Split-Path -Parent $LockFile
if (-not [string]::IsNullOrWhiteSpace($lockDir)) {
    [System.IO.Directory]::CreateDirectory($lockDir) | Out-Null
}

$timestamp = (Get-Date).ToString("o")
"[$timestamp] starting export" | Out-File -FilePath $logFile -Encoding utf8 -Append

$lockStream = $null
try {
    $lockStream = New-Object System.IO.FileStream(
        $LockFile,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch {
    $finishedTs = (Get-Date).ToString("o")
    "[$finishedTs] finished export rc=1 lock_busy=true" | Out-File -FilePath $logFile -Encoding utf8 -Append
    Write-StderrAndExit "Another export appears to be running (lock busy): $LockFile" 11
}

$exitCode = 0
try {
    $combinedOutput = & $PythonBin $Exporter @args 2>&1
    if ($combinedOutput) {
        $combinedOutput | Out-File -FilePath $logFile -Encoding utf8 -Append
    }
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
}
finally {
    if ($lockStream -ne $null) {
        $lockStream.Dispose()
    }
}

$finishedTs = (Get-Date).ToString("o")
"[$finishedTs] finished export rc=$exitCode" | Out-File -FilePath $logFile -Encoding utf8 -Append
exit $exitCode
