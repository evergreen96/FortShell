param(
    [ValidateRange(1, 1000)]
    [int]$RepeatCount = 3,

    [switch]$RepeatForever,

    [ValidateRange(0, 3600)]
    [int]$DelaySeconds = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$codexCommand = Get-Command codex.cmd -ErrorAction Stop
$SessionId = '019cbe2a-35f6-75f1-92e9-8de2659f5164'
$UserMessage = 'check docs folder and docs/plan.md. check the current stage and the thing you did. also check remaining jobs. after that do what you do next(always check data structure, architecture, module, test, stability, maintenance, secure, high cohesion, low coupling)'
$LastMessageFile = Join-Path $PSScriptRoot 'codex-last-message.txt'

if ($SessionId -eq 'PUT_SESSION_ID_HERE') {
    throw 'Edit $SessionId in this script before running it.'
}

if ($UserMessage -eq 'PUT_MESSAGE_HERE') {
    throw 'Edit $UserMessage in this script before running it.'
}

function Get-CodexResumeArguments {
    $null = & $codexCommand.Source exec resume --help 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'This Codex CLI version does not support "codex exec resume".'
    }

    return @(
        'exec',
        'resume',
        '--full-auto',
        '--skip-git-repo-check',
        '-o',
        $LastMessageFile
    )
}

$baseArguments = Get-CodexResumeArguments

$iteration = 0

while ($RepeatForever -or $iteration -lt $RepeatCount) {
    $iteration++

    Write-Host ''
    if ($RepeatForever) {
        Write-Host ("[{0}/INF] Resuming session {1}" -f $iteration, $SessionId) -ForegroundColor Cyan
    }
    else {
        Write-Host ("[{0}/{1}] Resuming session {2}" -f $iteration, $RepeatCount, $SessionId) -ForegroundColor Cyan
    }

    $arguments = $baseArguments + @($SessionId, $UserMessage)
    & $codexCommand.Source @arguments

    if ($LASTEXITCODE -ne 0) {
        throw "codex exited with code $LASTEXITCODE while resuming session $SessionId"
    }

    if ($DelaySeconds -gt 0 -and ($RepeatForever -or $iteration -lt $RepeatCount)) {
        Start-Sleep -Seconds $DelaySeconds
    }
}

Write-Host ''
if ($RepeatForever) {
    Write-Host 'Codex resume loop stopped.' -ForegroundColor Green
}
else {
    Write-Host 'All Codex resume runs completed.' -ForegroundColor Green
}
