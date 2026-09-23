[CmdletBinding()]
param([string]$PortalUrl = 'https://9950x.taile832fe.ts.net', [string]$ProjectDrive = 'R:\')
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run in the normal, unelevated desktop session.' }
$pt = Join-Path $env:LOCALAPPDATA 'PowerToys'
$root = Join-Path $env:LOCALAPPDATA 'Microsoft\PowerToys\Workspaces'
$launcher = Join-Path $pt 'PowerToys.WorkspacesLauncher.exe'
if (-not (Test-Path $launcher)) { throw 'Install Microsoft PowerToys for this user first.' }
$backup = Join-Path $env:LOCALAPPDATA ('EdSys\Workspace-Backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $backup | Out-Null
$file = Join-Path $root 'workspaces.json'
$existing = @()
if (Test-Path $file) { Copy-Item $file $backup; $existing = @((Get-Content $file -Raw | ConvertFrom-Json).workspaces) }
# The official snapshot tool supplies monitor identifiers, DPI and coordinates.
# Its application snapshot is temporary and is never included in the saved profiles.
$temp = Join-Path $root 'temp-workspaces.json'
if (Test-Path $temp) { Move-Item $temp (Join-Path $backup 'temp-workspaces.json') }
$p = Start-Process (Join-Path $pt 'PowerToys.WorkspacesSnapshotTool.exe') -PassThru
if (-not $p.WaitForExit(30000)) { throw 'PowerToys monitor snapshot timed out.' }
$snapshot = Get-Content $temp -Raw | ConvertFrom-Json
$monitors = @($snapshot.'monitor-configuration' | Sort-Object {$_.'monitor-rect-dpi-aware'.left})
if ($monitors.Count -ne 3) { throw 'The Office profile requires the three connected office monitors.' }
$primary = @($monitors | Where-Object {$_.'monitor-rect-dpi-aware'.left -eq 0 -and $_.'monitor-rect-dpi-aware'.top -eq 0})[0]
if (-not $primary -or $primary.'monitor-rect-dpi-aware'.width -ne 1920 -or $primary.'monitor-rect-dpi-aware'.height -ne 1080 -or $primary.dpi -ne 96) { throw 'The Remote profile requires the verified 1920x1080, 100% primary display.' }
$codex = Get-AppxPackage OpenAI.Codex
if (-not $codex) { throw 'Codex desktop is missing for this user.' }
$manifest = Get-AppxPackageManifest $codex
$codexApp = @($manifest.Package.Applications.Application)[0]
$apps = @(
    @{Name='Codex';Path=(Join-Path $codex.InstallLocation $codexApp.Executable);Package=$codex.PackageFullName;Aumid=($codex.PackageFamilyName+'!'+$codexApp.Id);Args=''},
    @{Name='Google Chrome';Path='C:\Program Files\Google\Chrome\Application\chrome.exe';Package='';Aumid='';Args=('--new-window '+$PortalUrl)},
    @{Name='Bluebeam Revu';Path='C:\Program Files\Bluebeam Software\Bluebeam Revu\21\Revu\Revu.exe';Package='';Aumid='';Args=''},
    @{Name='Outlook (classic)';Path='C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE';Package='';Aumid='Microsoft.Office.OUTLOOK.EXE.15';Args='/recycle'},
    @{Name='File Explorer';Path=(Join-Path $env:SystemRoot 'explorer.exe');Package='';Aumid='Microsoft.Windows.Explorer';Args=$ProjectDrive}
)
foreach ($a in $apps) { if (-not (Test-Path -LiteralPath $a.Path)) { throw "Application is missing: $($a.Name)" } }
$profiles = foreach ($mode in @('Office','Remote')) {
    $name = 'EdSys - ' + $mode
    $old = @($existing | Where-Object name -eq $name)
    $id = if ($old.Count -eq 1) {$old[0].id} else {'{'+[guid]::NewGuid().ToString().ToUpperInvariant()+'}'}
    $entries = for ($i=0; $i -lt $apps.Count; $i++) {
        $a=$apps[$i];$m=$primary;$x=0;$y=0;$width=1920;$height=1032;$min=$false;$max=$false
        if ($mode -eq 'Office') {
            switch ($i) {
                0 {$width=1280}
                1 {$m=$monitors[2];$x=$m.'monitor-rect-dpi-aware'.left+960;$width=960}
                2 {$m=$monitors[1];$x=$m.'monitor-rect-dpi-aware'.left;$max=$true}
                3 {$m=$monitors[2];$x=$m.'monitor-rect-dpi-aware'.left;$width=960}
                4 {$x=1280;$width=640}
            }
        } else {
            switch ($i) {0 {$width=1100} 1 {$x=1100;$width=820} default {$min=$true;$max=$true}}
        }
        @{'id'='{'+[guid]::NewGuid().ToString().ToUpperInvariant()+'}';'application'=$a.Name;'application-path'=$a.Path;'package-full-name'=$a.Package;'app-user-model-id'=$a.Aumid;'pwa-app-id'='';'title'='';'command-line-arguments'=$a.Args;'is-elevated'=$false;'can-launch-elevated'=$false;'minimized'=$min;'maximized'=$max;'position'=@{X=$x;Y=$y;width=$width;height=$height};'monitor'=$m.'monitor-number';'version'=''}
    }
    @{'id'=$id;'name'=$name;'creation-time'=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds();'is-shortcut-needed'=$true;'move-existing-windows'=$true;'monitor-configuration'=$monitors;'applications'=@($entries)}
}
$all = @($existing | Where-Object name -notin @('EdSys - Office','EdSys - Remote')) + @($profiles)
[IO.File]::WriteAllText($file,(@{workspaces=$all}|ConvertTo-Json -Depth 12),(New-Object Text.UTF8Encoding($false)))
$installRoot=Join-Path $env:LOCALAPPDATA 'EdSys\Workspaces'
New-Item -ItemType Directory -Force $installRoot|Out-Null
Copy-Item (Join-Path $PSScriptRoot 'Start-EdSysWorkspace.ps1') $installRoot -Force
$shell = New-Object -ComObject WScript.Shell
foreach ($profile in $profiles) {
    $shortcut=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ($profile.name+'.lnk')))
    $shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $mode=$profile.name.Replace('EdSys - ','')
    $shortcut.Arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "'+(Join-Path $installRoot 'Start-EdSysWorkspace.ps1')+'" -Mode '+$mode
    $shortcut.WorkingDirectory=$installRoot;$shortcut.IconLocation=$launcher
    $shortcut.Description='Launch and arrange EdSys applications for '+$profile.name
    $shortcut.Save()
}
Remove-Item $temp
if (Test-Path (Join-Path $backup 'temp-workspaces.json')) { Move-Item (Join-Path $backup 'temp-workspaces.json') $temp }
[pscustomobject]@{Profiles=@($profiles|ForEach-Object {@{Name=$_.name;Id=$_.id;Applications=$_.applications.Count}});Backup=$backup;Settings=$file}
