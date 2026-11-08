$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'Documents\VisioMcp\EdSysPilot'
$profile = Join-Path $env:USERPROFILE '.codex\visio.config.toml'
if (-not (Test-Path -LiteralPath (Join-Path $root '.codex\config.toml'))) { throw 'Stage the reviewed workspace configuration first' }
if (-not (Test-Path -LiteralPath $profile)) { throw 'Create and verify the dedicated visio profile first' }
$codex = (Get-Command codex -ErrorAction Stop).Source
$config = & $codex -p visio mcp get visio-local --json
if ($LASTEXITCODE -ne 0 -or ($config | ConvertFrom-Json).name -ne 'visio-local') { throw 'Named profile does not resolve visio-local' }
$launcher = Join-Path $root 'Open-VisioPilot.ps1'
$body = @'
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'Documents\VisioMcp\EdSysPilot'
Set-Location -LiteralPath $root
Write-Host 'Visio AI Pilot - native Windows evaluation workspace. Save new versioned files.'
& (Get-Command codex -ErrorAction Stop).Source -p visio -C $root
'@
if ((Test-Path -LiteralPath $launcher) -and [IO.File]::ReadAllText($launcher).Replace("`r`n", "`n") -ne $body.Replace("`r`n", "`n")) { throw 'Existing launcher differs; inspect before replacing it' }
[IO.File]::WriteAllText($launcher, $body, (New-Object Text.UTF8Encoding($false)))
$shell = New-Object -ComObject WScript.Shell
$folders = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
foreach ($folder in $folders) {
    $linkPath = Join-Path $folder 'Visio AI Pilot.lnk'
    $target = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoExit -NoProfile -File "' + $launcher + '"'
    if (Test-Path -LiteralPath $linkPath) {
        $existing = $shell.CreateShortcut($linkPath)
        if ($existing.TargetPath -ne $target -or $existing.Arguments -ne $arguments) { throw 'Existing shortcut differs; inspect before replacing it' }
    }
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
Write-Output 'Desktop and Start menu Visio AI Pilot shortcuts verified; default Codex configuration unchanged.'
