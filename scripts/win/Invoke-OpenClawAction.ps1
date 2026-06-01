# Invoke-OpenClawAction.ps1 - central dispatcher for approved OpenClaw Windows actions.
param(
  [Parameter(Mandatory = $true)]
  [string]$RequestJsonBase64
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AllowedRoots = @(
  "C:\dev",
  "D:\work"
)

$AllowedApps = @{
  "vscode" = "code.cmd"
  "chrome" = "chrome.exe"
  "edge" = "msedge.exe"
}

$AllowedTaskRecipes = @(
  "check",
  "policy:validate",
  "schema:validate",
  "eval:intents",
  "test:browser",
  "browser:probe",
  "approval:queue",
  "approval:status",
  "e2e:dry-run",
  "voice:dry-run",
  "debug:autoloop",
  "debug:auto-once",
  "browser:doctor",
  "openclaw:doctor"
)

$AllowedActions = @(
  "notify",
  "open_url_readonly",
  "open_vscode_codex_plan",
  "open_app_allowlisted",
  "run_task_recipe"
)

$AllowedApprovalMethods = @("voice", "telegram", "manual")
$AllowedRiskTiers = @("low", "medium", "high", "critical")
$PayloadHashPattern = "^sha256:[a-f0-9]{64}$"

function Decode-Base64UrlJson {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $normalized = $Value.Replace("-", "+").Replace("_", "/")
  switch ($normalized.Length % 4) {
    0 { }
    2 { $normalized += "==" }
    3 { $normalized += "=" }
    default { throw "Invalid base64url length." }
  }

  $bytes = [Convert]::FromBase64String($normalized)
  $json = [Text.Encoding]::UTF8.GetString($bytes)
  return $json | ConvertFrom-Json
}

function Get-RequiredProperty {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Object,

    [Parameter(Mandatory = $true)]
    [string]$Name
  )

  if ($null -eq $Object) {
    throw "Missing object while reading property: $Name"
  }

  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property -or $null -eq $property.Value) {
    throw "Missing required property: $Name"
  }

  return $property.Value
}

function ConvertTo-CanonicalJsonString {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $builder = [Text.StringBuilder]::new()
  [void]$builder.Append('"')

  foreach ($char in $Value.ToCharArray()) {
    $code = [int][char]$char
    switch ($code) {
      8 { [void]$builder.Append('\b') }
      9 { [void]$builder.Append('\t') }
      10 { [void]$builder.Append('\n') }
      12 { [void]$builder.Append('\f') }
      13 { [void]$builder.Append('\r') }
      34 { [void]$builder.Append('\"') }
      92 { [void]$builder.Append('\\') }
      default {
        if ($code -lt 32) {
          [void]$builder.Append(('\u{0:x4}' -f $code))
        } else {
          [void]$builder.Append($char)
        }
      }
    }
  }

  [void]$builder.Append('"')
  return $builder.ToString()
}

function ConvertTo-CanonicalJson {
  param(
    [AllowNull()]
    [object]$Value
  )

  if ($null -eq $Value) {
    return "null"
  }

  if ($Value -is [string]) {
    return ConvertTo-CanonicalJsonString $Value
  }

  if ($Value -is [bool]) {
    if ($Value) {
      return "true"
    }
    return "false"
  }

  if ($Value -is [byte] -or
      $Value -is [sbyte] -or
      $Value -is [int16] -or
      $Value -is [uint16] -or
      $Value -is [int] -or
      $Value -is [uint32] -or
      $Value -is [long] -or
      $Value -is [uint64] -or
      $Value -is [float] -or
      $Value -is [double] -or
      $Value -is [decimal]) {
    return [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
  }

  if ($Value -is [System.Collections.IDictionary]) {
    $parts = @()
    foreach ($key in ($Value.Keys | Sort-Object)) {
      $encodedKey = ConvertTo-CanonicalJsonString ([string]$key)
      $encodedValue = ConvertTo-CanonicalJson $Value[$key]
      $parts += "$encodedKey`:$encodedValue"
    }
    return "{$($parts -join ',')}"
  }

  if ($Value -is [pscustomobject]) {
    $parts = @()
    foreach ($property in ($Value.PSObject.Properties.Name | Sort-Object)) {
      $encodedKey = ConvertTo-CanonicalJsonString $property
      $encodedValue = ConvertTo-CanonicalJson $Value.PSObject.Properties[$property].Value
      $parts += "$encodedKey`:$encodedValue"
    }
    return "{$($parts -join ',')}"
  }

  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
    $items = @()
    foreach ($item in $Value) {
      $items += ConvertTo-CanonicalJson $item
    }
    return "[$($items -join ',')]"
  }

  return ConvertTo-CanonicalJsonString ([string]$Value)
}

function Get-Sha256Hex {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    $hashBytes = $sha.ComputeHash($bytes)
    return (($hashBytes | ForEach-Object { $_.ToString("x2") }) -join "")
  } finally {
    $sha.Dispose()
  }
}

function Get-RequestPayloadHash {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Request
  )

  $payload = [ordered]@{
    action = [string](Get-RequiredProperty $Request "action")
    params = $Request.params
    requestId = [string](Get-RequiredProperty $Request "requestId")
    riskTier = [string](Get-RequiredProperty $Request "riskTier")
    source = [string](Get-RequiredProperty $Request "source")
    version = [int](Get-RequiredProperty $Request "version")
  }

  $json = ConvertTo-CanonicalJson $payload
  return "sha256:$(Get-Sha256Hex $json)"
}

function Assert-PayloadHash {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Request
  )

  $expected = [string](Get-RequiredProperty $Request "payloadHash")
  if ($expected -cnotmatch $PayloadHashPattern) {
    throw "Invalid payloadHash format."
  }

  $actual = Get-RequestPayloadHash $Request
  if ($expected -cne $actual) {
    throw "payloadHash mismatch."
  }
}

function Assert-ApprovedRequest {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Request
  )

  $version = [int](Get-RequiredProperty $Request "version")
  if ($version -ne 1) {
    throw "Unsupported request version: $version"
  }

  $requestId = [string](Get-RequiredProperty $Request "requestId")
  if ($requestId.Length -lt 8 -or $requestId.Length -gt 160) {
    throw "Invalid requestId length."
  }

  $source = [string](Get-RequiredProperty $Request "source")
  if ($source.Length -lt 2 -or $source.Length -gt 80) {
    throw "Invalid source length."
  }

  if ($Request.approvedByUser -ne $true) {
    throw "Request is missing explicit user approval."
  }

  $approvalMethod = [string](Get-RequiredProperty $Request "approvalMethod")
  if ($approvalMethod -notin $AllowedApprovalMethods) {
    throw "Invalid approvalMethod: $approvalMethod"
  }

  $riskTier = [string](Get-RequiredProperty $Request "riskTier")
  if ($riskTier -notin $AllowedRiskTiers) {
    throw "Invalid riskTier: $riskTier"
  }

  $action = [string](Get-RequiredProperty $Request "action")
  if ($action -notin $AllowedActions) {
    throw "Unknown or denied action: $action"
  }

  if ($null -eq $Request.PSObject.Properties["params"]) {
    $Request | Add-Member -NotePropertyName "params" -NotePropertyValue ([pscustomobject]@{})
  }

  Assert-PayloadHash $Request
}

function Assert-AllowedPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
  $fullPath = $resolved.Path

  foreach ($root in $AllowedRoots) {
    $rootFullPath = [IO.Path]::GetFullPath($root)
    if ($fullPath.StartsWith($rootFullPath, [StringComparison]::OrdinalIgnoreCase)) {
      return $fullPath
    }
  }

  throw "Path is outside allowed roots: $fullPath"
}

function Assert-SafeUrl {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Url
  )

  $uri = [Uri]$Url
  if ($uri.Scheme -notin @("http", "https")) {
    throw "Only http/https URLs are allowed."
  }
  if ([string]::IsNullOrWhiteSpace($uri.Host)) {
    throw "URL host is required."
  }

  return $uri.AbsoluteUri
}

function Write-ActionLog {
  param(
    [Parameter(Mandatory = $true)]
    [object]$Request,

    [Parameter(Mandatory = $true)]
    [string]$Status,

    [Parameter(Mandatory = $true)]
    [string]$Message
  )

  $logDir = Join-Path $PSScriptRoot "logs"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null

  $entry = [pscustomobject]@{
    timestamp = (Get-Date).ToString("o")
    requestId = $Request.requestId
    source = $Request.source
    action = $Request.action
    riskTier = $Request.riskTier
    approvalMethod = $Request.approvalMethod
    status = $Status
    message = $Message
    payloadHash = $Request.payloadHash
  }

  $line = $entry | ConvertTo-Json -Compress -Depth 10
  Add-Content -LiteralPath (Join-Path $logDir "actions.jsonl") -Value $line
}

function Invoke-NotifyAction {
  param([Parameter(Mandatory = $true)][object]$Request)

  $title = [string](Get-RequiredProperty $Request.params "title")
  $body = [string](Get-RequiredProperty $Request.params "body")

  if ($title.Length -gt 120 -or $body.Length -gt 500) {
    throw "Notification text too long."
  }

  if (Get-Command New-BurntToastNotification -ErrorAction SilentlyContinue) {
    New-BurntToastNotification -Text $title, $body
  } else {
    Write-Host "[$title] $body"
  }

  Write-ActionLog $Request "ok" "notification shown or printed"
}

function Invoke-OpenUrlReadonlyAction {
  param([Parameter(Mandatory = $true)][object]$Request)

  $url = Assert-SafeUrl ([string](Get-RequiredProperty $Request.params "url"))
  Start-Process $url
  Write-ActionLog $Request "ok" "url opened"
}

function Invoke-VSCodeCodexPlanAction {
  param([Parameter(Mandatory = $true)][object]$Request)

  $projectPath = Assert-AllowedPath ([string](Get-RequiredProperty $Request.params "projectPath"))
  $task = [string](Get-RequiredProperty $Request.params "task")

  if ($task.Length -lt 3 -or $task.Length -gt 2000) {
    throw "Task length invalid."
  }

  code -r $projectPath

  $prompt = @"
/plan $task

Rules:
- Plan only.
- Do not edit files.
- Do not run commands unless explicitly approved.
- Summarize the plan first and wait for confirmation.
"@

  codex -C $projectPath `
    --sandbox read-only `
    --ask-for-approval on-request `
    $prompt

  Write-ActionLog $Request "ok" "codex read-only plan started"
}

function Invoke-OpenAppAllowlistedAction {
  param([Parameter(Mandatory = $true)][object]$Request)

  $app = [string](Get-RequiredProperty $Request.params "app")
  if (-not $AllowedApps.ContainsKey($app)) {
    throw "App not allowlisted: $app"
  }

  Start-Process $AllowedApps[$app]
  Write-ActionLog $Request "ok" "app opened: $app"
}

function Invoke-TaskRecipeAction {
  param([Parameter(Mandatory = $true)][object]$Request)

  $recipe = [string](Get-RequiredProperty $Request.params "recipe")
  if ($recipe -notin $AllowedTaskRecipes) {
    throw "Task recipe not allowlisted: $recipe"
  }

  task $recipe
  Write-ActionLog $Request "ok" "task recipe completed: $recipe"
}

$request = Decode-Base64UrlJson $RequestJsonBase64

try {
  Assert-ApprovedRequest $request

  switch ($request.action) {
    "notify" { Invoke-NotifyAction $request }
    "open_url_readonly" { Invoke-OpenUrlReadonlyAction $request }
    "open_vscode_codex_plan" { Invoke-VSCodeCodexPlanAction $request }
    "open_app_allowlisted" { Invoke-OpenAppAllowlistedAction $request }
    "run_task_recipe" { Invoke-TaskRecipeAction $request }
    default { throw "Unknown or denied action: $($request.action)" }
  }
} catch {
  try {
    if ($null -ne $request) {
      Write-ActionLog $request "denied_or_failed" $_.Exception.Message
    }
  } catch {
    Write-Warning "Failed to write action log: $($_.Exception.Message)"
  }

  throw
}
