$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'Documents\VisioMcp\EdSysPilot'
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
foreach ($required in @((Join-Path $root '.codex\config.toml'), (Join-Path $root 'run_native_codex.py'), $python)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Stage the reviewed prerequisite first: $required" }
}
$launcher = Join-Path $root 'Open-VisioPilot.ps1'
$oldBody = @'
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'Documents\VisioMcp\EdSysPilot'
Set-Location -LiteralPath $root
Write-Host 'Visio AI Pilot - native Windows evaluation workspace. Save new versioned files.'
& (Get-Command codex -ErrorAction Stop).Source -p visio -C $root
'@
$body = @'
[CmdletBinding()]
param([string]$BriefFile, [switch]$ReadOnly)
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'Documents\VisioMcp\EdSysPilot'
$jobs = [IO.Path]::GetFullPath((Join-Path $root 'jobs'))
$python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$worker = Join-Path $root 'run_native_codex.py'
if ($PSBoundParameters.ContainsKey('BriefFile') -and -not [string]::IsNullOrWhiteSpace($BriefFile)) {
    $BriefFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BriefFile)
}
Set-Location -LiteralPath $root

function Assert-RegularItem([string]$Path, [switch]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.PSIsContainer -ne [bool]$Directory) {
        throw 'Refusing an unexpected directory or filesystem link in the job path'
    }
}

function Submit-PilotBrief([string]$Text, [string]$File, [bool]$Inspection) {
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    if ($File) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { throw 'The selected UTF-8 brief file does not exist' }
        if ((Get-Item -LiteralPath $File).Length -gt 256000) { throw 'The UTF-8 brief file must not exceed 256 KB' }
        $Text = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $File).ProviderPath, $utf8)
    }
    if ([string]::IsNullOrWhiteSpace($Text)) { throw 'Enter a request or select a nonempty UTF-8 brief file' }
    if ($utf8.GetByteCount($Text) -gt 256000) { throw 'The UTF-8 request must not exceed 256 KB' }
    foreach ($required in @($worker, $python, (Join-Path $root '.codex\config.toml'))) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing reviewed prerequisite: $required" }
    }
    New-Item -ItemType Directory -Path $jobs -Force | Out-Null
    Assert-RegularItem $jobs -Directory
    $scope = if ($Inspection) {
        'Read-only inspection of the exact saved drawing in the brief. Do not edit, save, repair, export or close/reopen the source. Report missing files without substitution. Read complete chunks and required nested shapes; verify native membership and source evidence separately.'
    } else {
        'Create or edit evaluation copies only. Preserve original drawings and unrelated work. Follow the brief, keep unknown sources unknown, save versioned outputs explicitly and verify saved content and previews.'
    }
    $prompt = "Use the installed visio-desktop-pilot skill on Nimo native interactive Windows.`r`n$scope`r`n`r`nUser brief:`r`n$Text"
    $bytes = $utf8.GetBytes($prompt)
    if ($bytes.Length -gt 256000) { throw 'The UTF-8 request including its scope header must fit within 256 KB' }
    $promptPath = Join-Path $jobs ('manual-' + [guid]::NewGuid().ToString('N') + '.txt')
    $stream = [IO.File]::Open($promptPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    $workerArgs = @($worker, $promptPath)
    if ($Inspection) { $workerArgs += '--read-only' }
    Write-Host 'Running the native Visio worker. Completion is checked against its receipt and answer.'
    $resultLines = @(& $python @workerArgs)
    $workerExit = $LASTEXITCODE
    $result = ($resultLines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1) | ConvertFrom-Json
    if (-not $result.receipt -or -not [IO.Path]::IsPathRooted([string]$result.receipt)) { throw 'Worker returned no valid receipt path; inspect the jobs folder' }
    $receiptPath = [IO.Path]::GetFullPath([string]$result.receipt)
    $report = Split-Path -Parent $receiptPath
    if ([IO.Path]::GetFileName($receiptPath) -ne 'receipt.json' -or [IO.Directory]::GetParent($report).FullName -ne $jobs) {
        throw 'Worker receipt is outside the expected jobs directory'
    }
    Assert-RegularItem $report -Directory
    Assert-RegularItem $receiptPath
    Write-Host ('Report folder: ' + $report)
    $receipt = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
    if ($workerExit -ne 0 -or $result.status -ne 'complete' -or $receipt.status -ne 'complete' -or $receipt.exitCode -ne 0) {
        throw ('Visio job failed; inspect the report folder. ' + [string]$receipt.error)
    }
    $answerPath = Join-Path $report 'answer.md'
    Assert-RegularItem $answerPath
    $answer = [IO.File]::ReadAllText($answerPath, (New-Object Text.UTF8Encoding($false, $true)))
    if ([string]::IsNullOrWhiteSpace($answer)) { throw 'Worker completed without an actual answer; inspect the report folder' }
    Write-Output $answer
}

if ($PSBoundParameters.ContainsKey('BriefFile')) {
    try {
        if ([string]::IsNullOrWhiteSpace($BriefFile)) { throw 'BriefFile must name an existing UTF-8 brief' }
        Submit-PilotBrief -File $BriefFile -Inspection ([bool]$ReadOnly)
        exit 0
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
        [Console]::Error.WriteLine('Jobs folder: ' + $jobs)
        exit 1
    }
}
if ($ReadOnly) { throw 'Use -ReadOnly together with -BriefFile, or choose I from the menu' }
Write-Host 'Visio AI Pilot - native Windows evaluation; jobs run through the isolated worker.'
while ($true) {
    $choice = (Read-Host 'C=create/edit evaluation copy; I=inspect saved drawing; Q=quit').Trim().ToUpperInvariant()
    if ($choice -eq 'Q') { exit 0 }
    if ($choice -notin @('C', 'I')) { continue }
    try {
        $entry = Read-Host 'Enter a one-line request or the path of an existing UTF-8 brief file'
        if (Test-Path -LiteralPath $entry -PathType Leaf) {
            Submit-PilotBrief -File $entry -Inspection ($choice -eq 'I')
        } else {
            Submit-PilotBrief -Text $entry -Inspection ($choice -eq 'I')
        }
    } catch { Write-Host ('Job failed: ' + $_.Exception.Message); Write-Host ('Jobs folder: ' + $jobs) }
}
'@
$existingBody = $null
if (Test-Path -LiteralPath $launcher) {
    $item = Get-Item -LiteralPath $launcher -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Existing launcher is not a regular file' }
    $existingBody = [IO.File]::ReadAllText($launcher)
    $normalized = $existingBody.Replace("`r`n", "`n")
    if ($normalized -cne $oldBody.Replace("`r`n", "`n") -and $normalized -cne $body.Replace("`r`n", "`n")) {
        throw 'Existing launcher differs; inspect before replacing it'
    }
}
$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
$target = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = '-NoExit -NoProfile -File "' + $launcher + '"'
foreach ($folder in $folders) {
    $linkPath = Join-Path $folder 'Visio AI Pilot.lnk'
    if (Test-Path -LiteralPath $linkPath) {
        if ((Get-Item -LiteralPath $linkPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Existing shortcut is a filesystem link' }
        $existing = $shell.CreateShortcut($linkPath)
        if ($existing.TargetPath -ne $target -or $existing.Arguments -ne $arguments) { throw 'Existing shortcut differs; inspect before replacing it' }
    }
}
if ($null -ne $existingBody -and $existingBody.Replace("`r`n", "`n") -ceq $oldBody.Replace("`r`n", "`n")) {
    $recovery = Join-Path $env:LOCALAPPDATA 'EdSys\VisioPilot'
    New-Item -ItemType Directory -Path $recovery -Force | Out-Null
    $backup = Join-Path $recovery ('Open-VisioPilot-before-console-' + [guid]::NewGuid().ToString('N') + '.ps1')
    [IO.File]::Copy($launcher, $backup, $false)
}
if ($null -eq $existingBody -or $existingBody.Replace("`r`n", "`n") -cne $body.Replace("`r`n", "`n")) {
    [IO.File]::WriteAllText($launcher, $body, (New-Object Text.UTF8Encoding($false)))
}
if ([IO.File]::ReadAllText($launcher).Replace("`r`n", "`n") -cne $body.Replace("`r`n", "`n")) { throw 'Launcher readback failed' }
foreach ($folder in $folders) {
    $linkPath = Join-Path $folder 'Visio AI Pilot.lnk'
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $target
    $link.Arguments = $arguments
    $link.WorkingDirectory = $root
    $link.Description = 'Native Codex and official Visio MCP evaluation; no web interface'
    $icon = 'C:\Program Files\Microsoft Office\root\Office16\VISIO.EXE'
    if (Test-Path -LiteralPath $icon) { $link.IconLocation = $icon + ',0' }
    $link.Save()
    $check = $shell.CreateShortcut($linkPath)
    if ($check.TargetPath -ne $target -or $check.Arguments -ne $arguments) { throw 'Shortcut readback failed' }
}
Write-Output 'Desktop and Start menu Visio AI Pilot worker intake verified; default Codex configuration unchanged.'
