param(
    [ValidateRange(1, 1000)]
    [int]$Rounds = 5,

    [string]$StateDir = (Join-Path $PSScriptRoot '.relay-state')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Edit these values before running.
$ConfiguredClaudeSessionId = '5476a459-12ae-4349-bcc1-6b2df3589f2e'
$ConfiguredCodexSessionId = '019cbe2a-35f6-75f1-92e9-8de2659f5164'
$ConfiguredInitialPrompt = @'
'@
$UseLastMessageAsInitialPrompt = $true

$claudeCommand = Get-Command claude -ErrorAction Stop
$codexCommand = Get-Command codex.cmd -ErrorAction Stop

$resolvedStateDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateDir)
$null = New-Item -ItemType Directory -Path $resolvedStateDir -Force

$lastFile = Join-Path $resolvedStateDir 'last.txt'
$relayLogFile = Join-Path $resolvedStateDir 'relay.log'

function Get-RequiredSetting {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Set `$${Name} in relay-loop.ps1 before running."
    }

    return $Value.Trim()
}

function Write-RelayLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Speaker,

        [Parameter(Mandatory = $true)]
        [int]$Round,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $entry = @(
        "=== $Speaker Round $Round ==="
        $Message
        ''
    ) -join [Environment]::NewLine

    Add-Content -Path $relayLogFile -Value $entry -Encoding utf8
}

function Invoke-ClaudeResume {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    if ([string]::IsNullOrWhiteSpace($Prompt)) {
        throw 'Initial prompt is empty.'
    }

    $Prompt | & $claudeCommand.Source `
        -p `
        -r $ConfiguredClaudeSessionId `
        --permission-mode bypassPermissions `
        --input-format text `
        --output-format text `
        | Tee-Object -FilePath $lastFile `
        | Out-Host

    if ($LASTEXITCODE -ne 0) {
        throw "Claude exited with code $LASTEXITCODE."
    }

    return Get-Content -Path $lastFile -Raw
}

function Invoke-CodexResume {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    if ([string]::IsNullOrWhiteSpace($Prompt)) {
        throw 'Prompt passed to Codex is empty.'
    }

    $Prompt | & $codexCommand.Source `
        exec `
        resume `
        --dangerously-bypass-approvals-and-sandbox `
        -o $lastFile `
        $ConfiguredCodexSessionId `
        - `
        | Out-Host

    if ($LASTEXITCODE -ne 0) {
        throw "Codex exited with code $LASTEXITCODE."
    }

    return Get-Content -Path $lastFile -Raw
}

$ConfiguredClaudeSessionId = Get-RequiredSetting -Name 'ConfiguredClaudeSessionId' -Value $ConfiguredClaudeSessionId
$ConfiguredCodexSessionId = Get-RequiredSetting -Name 'ConfiguredCodexSessionId' -Value $ConfiguredCodexSessionId

$initialPrompt = $null

if ($UseLastMessageAsInitialPrompt -and (Test-Path $lastFile)) {
    $savedLastMessage = Get-Content -Path $lastFile -Raw
    if (-not [string]::IsNullOrWhiteSpace($savedLastMessage)) {
        $initialPrompt = $savedLastMessage
    }
}

if ([string]::IsNullOrWhiteSpace($initialPrompt)) {
    $initialPrompt = $ConfiguredInitialPrompt
}

if ([string]::IsNullOrWhiteSpace($initialPrompt)) {
    throw "Set `$ConfiguredInitialPrompt or put a previous message in $lastFile before running."
}

$last = $initialPrompt
Set-Content -Path $lastFile -Value $last -Encoding utf8

for ($round = 1; $round -le $Rounds; $round++) {
    Write-Host ("[{0}/{1}] Claude" -f $round, $Rounds) -ForegroundColor Cyan
    $last = Invoke-ClaudeResume -Prompt $last
    Write-RelayLog -Speaker 'Claude' -Round $round -Message $last

    Write-Host ("[{0}/{1}] Codex" -f $round, $Rounds) -ForegroundColor Yellow
    $last = Invoke-CodexResume -Prompt $last
    Write-RelayLog -Speaker 'Codex' -Round $round -Message $last
}

Write-Host ''
Write-Host ("Finished {0} round(s)." -f $Rounds) -ForegroundColor Green
Write-Host ("Last message: {0}" -f $lastFile) -ForegroundColor Green
Write-Host ("Relay log: {0}" -f $relayLogFile) -ForegroundColor Green
