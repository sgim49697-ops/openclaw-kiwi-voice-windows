# Invoke-KiwiDryRunOpenClaw.ps1 - Kiwi OpenClaw CLI dry-run bridge.
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$OpenClawArgs
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Distro = "Ubuntu-22.04"
$RepoWslPath = "/home/user/projects/openclaw-kiwi-voice-windows"
$RepoUncPath = "\\wsl.localhost\Ubuntu-22.04\home\user\projects\openclaw-kiwi-voice-windows"
$LogPath = Join-Path $RepoUncPath ".debugloop\runs\kiwi-live-dry-run.jsonl"

function Write-BridgeLog {
    param([hashtable]$Event)

    $Event["createdAt"] = (Get-Date).ToUniversalTime().ToString("o")
    $Event["bridge"] = "kiwi-dry-run-openclaw"
    $logDir = Split-Path -Parent $LogPath
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    ($Event | ConvertTo-Json -Compress -Depth 32) | Add-Content -LiteralPath $LogPath -Encoding UTF8
}

function Deny-Command {
    param(
        [string]$Reason,
        [int]$Code = 64
    )

    Write-BridgeLog @{
        status = "denied"
        reason = $Reason
        args = $OpenClawArgs
    }
    [Console]::Error.WriteLine("Kiwi dry-run bridge denied command: $Reason")
    exit $Code
}

if (-not $OpenClawArgs -or $OpenClawArgs.Count -eq 0) {
    Deny-Command "missing arguments"
}

if ($OpenClawArgs.Count -eq 1 -and $OpenClawArgs[0] -eq "--version") {
    Write-Output "OpenClaw Kiwi Dry-run Bridge 0.1.0"
    exit 0
}

if ($OpenClawArgs[0] -ne "agent") {
    Deny-Command "only the agent command is allowed"
}

$sessionId = "kiwi-voice"
$message = $null
$timeout = 120
$agent = $null
$index = 1

while ($index -lt $OpenClawArgs.Count) {
    $flag = $OpenClawArgs[$index]
    switch ($flag) {
        "--session-id" {
            $index += 1
            if ($index -ge $OpenClawArgs.Count) { Deny-Command "missing value for --session-id" }
            $sessionId = $OpenClawArgs[$index]
        }
        "--message" {
            $index += 1
            if ($index -ge $OpenClawArgs.Count) { Deny-Command "missing value for --message" }
            $message = $OpenClawArgs[$index]
        }
        "--timeout" {
            $index += 1
            if ($index -ge $OpenClawArgs.Count) { Deny-Command "missing value for --timeout" }
            if (-not [int]::TryParse($OpenClawArgs[$index], [ref]$timeout)) {
                Deny-Command "invalid --timeout value"
            }
        }
        "--agent" {
            $index += 1
            if ($index -ge $OpenClawArgs.Count) { Deny-Command "missing value for --agent" }
            $agent = $OpenClawArgs[$index]
        }
        default {
            Deny-Command "unsupported agent argument: $flag"
        }
    }
    $index += 1
}

if ([string]::IsNullOrWhiteSpace($message)) {
    Deny-Command "agent --message is required"
}

$requestId = "kiwi-live-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff")
$wslArgs = @(
    "-d", $Distro,
    "--cd", $RepoWslPath,
    "python3", "scripts/wsl/kiwi_transcript_dry_run.py",
    "--transcript", $message,
    "--request-id", $requestId
)

$outputLines = & wsl.exe @wslArgs 2>&1
$exitCode = $LASTEXITCODE
$outputText = ($outputLines | Out-String).Trim()

if ($exitCode -ne 0) {
    Write-BridgeLog @{
        status = "failed"
        reason = "kiwi_transcript_dry_run.py failed"
        exitCode = $exitCode
        sessionId = $sessionId
        agent = $agent
        timeout = $timeout
        message = $message
        output = $outputText
    }
    [Console]::Error.WriteLine($outputText)
    exit $exitCode
}

try {
    $preview = $outputText | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-BridgeLog @{
        status = "failed"
        reason = "dry-run output was not valid JSON"
        sessionId = $sessionId
        agent = $agent
        timeout = $timeout
        message = $message
        output = $outputText
    }
    [Console]::Error.WriteLine("Kiwi dry-run bridge failed to parse dry-run JSON.")
    exit 65
}

if ($preview.wouldExecute -ne $false) {
    Write-BridgeLog @{
        status = "failed"
        reason = "dry-run preview attempted execution"
        sessionId = $sessionId
        agent = $agent
        timeout = $timeout
        message = $message
        result = $preview
    }
    [Console]::Error.WriteLine("Kiwi dry-run bridge blocked a non-dry-run preview.")
    exit 66
}

$route = $preview.route
$action = if ($route.action) { $route.action } else { "none" }

Write-BridgeLog @{
    status = "dry-run"
    sessionId = $sessionId
    agent = $agent
    timeout = $timeout
    message = $message
    result = $preview
}

Write-Output ("Kiwi dry-run only. No action executed. Route: {0}/{1}." -f $route.lane, $action)
exit 0
