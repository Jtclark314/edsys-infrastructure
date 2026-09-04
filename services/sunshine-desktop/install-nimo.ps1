[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HubTailnetAddress,
    [string]$HubLanAddress = '192.168.50.50',
    [Parameter(Mandatory=$true)][string[]]$AllowedLanAddresses
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ip = [System.Net.IPAddress]::Parse($HubTailnetAddress)
$bytes = $ip.GetAddressBytes()
if ($bytes.Length -ne 4 -or $bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127) { throw 'Expected a private Tailnet IPv4 identity' }
foreach ($address in @($HubLanAddress) + $AllowedLanAddresses) {
    $a = [System.Net.IPAddress]::Parse($address)
    if ($a.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or -not $a.ToString().StartsWith('192.168.50.')) { throw 'Expected exact EdSys LAN IPv4 addresses' }
}
$exe = Join-Path $env:ProgramFiles 'Moonlight Game Streaming\Moonlight.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Install the official Moonlight client first' }
if ((Get-AuthenticodeSignature $exe).Status -ne 'Valid') { throw 'Moonlight signature is not valid' }
$root = 'C:\ProgramData\EdSys\Sunshine'
New-Item -ItemType Directory -Force -Path $root | Out-Null
foreach ($path in @('C:\ProgramData','C:\ProgramData\EdSys',$root)) {
    if ((Get-Item -Force -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse-point install paths are forbidden' }
}
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
$admins = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl.SetOwner($admins)
foreach ($sid in @($admins,$system)) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
    $acl.AddAccessRule($rule)
}
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($owner,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')))
Set-Acl -LiteralPath $root -AclObject $acl
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'nimo-route.ps1') -Destination (Join-Path $root 'nimo-route.ps1') -Force
[pscustomobject]@{ HubLanAddress=$HubLanAddress; HubTailnetAddress=$ip.ToString(); AllowedLanAddresses=$AllowedLanAddresses } |
    ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $root 'client.json')
$action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$root\nimo-route.ps1`""
$triggers = @((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -AtLogOn),
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)))
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'EdSys Nimo 9950x LAN Route' -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description 'Use the direct qualified EdSys LAN interface for the 9950x; retain Tailscale off LAN.' -Force | Out-Null
& (Join-Path $root 'nimo-route.ps1')
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($transport in @('LAN','Tailscale')) {
    $name = if ($transport -eq 'LAN') {'9950x Desktop.lnk'} else {'9950x Desktop (Tailscale).lnk'}
    $hostAddress = if ($transport -eq 'LAN') {$HubLanAddress} else {$ip.ToString()}
    $bitrate = if ($transport -eq 'LAN') {25000} else {15000}
    $path = Join-Path $desktop $name
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $root ($name + '.before')) -Force }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $exe
    $shortcut.Arguments = "--display-mode borderless --resolution 1920x1080 --fps 60 --bitrate $bitrate --video-codec HEVC --video-decoder hardware --capture-system-keys always --absolute-mouse --quit-after stream $hostAddress Desktop"
    $shortcut.WorkingDirectory = Split-Path $exe
    $shortcut.IconLocation = "$exe,0"
    $shortcut.Description = "9950x physical desktop over $transport; temporary 1080p layout with restoration"
    $shortcut.Save()
    Write-Output "Created $name"
}
