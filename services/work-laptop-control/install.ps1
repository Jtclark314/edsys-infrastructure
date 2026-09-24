param([Parameter(Mandatory=$true)][string]$Root,[Parameter(Mandatory=$true)][string]$User)
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath($Root)
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator installation required'}
$sid=(New-Object Security.Principal.NTAccount($User)).Translate([Security.Principal.SecurityIdentifier]).Value
$signature=Get-AuthenticodeSignature "$Root/bin/winapp.exe"
if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation'){throw 'Microsoft Authenticode verification failed'}
foreach($dir in @('requests','responses','artifacts')){New-Item -ItemType Directory -Force "$Root/$dir"|Out-Null}
& icacls.exe $Root /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q|Out-Null
if($LASTEXITCODE -ne 0){throw 'ACL installation failed'}
& icacls.exe "$Root\*" /reset /T /Q|Out-Null
if($LASTEXITCODE -ne 0){throw 'Child ACL inheritance failed'}
$name='EdSys-Codex-Desktop-Control'
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){Stop-ScheduledTask -TaskName $name}
$action=New-ScheduledTaskAction -Execute "$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -STA -ExecutionPolicy RemoteSigned -File `"$Root/agent.ps1`" -Root `"$Root`""
$principal=New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $User
$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Trigger $trigger -Settings $settings -Description 'SSH-carried Codex desktop tools; limited user, local pause, no listening port.' -Force|Out-Null
Start-ScheduledTask -TaskName $name
@{task=$name;run_level='Limited';publisher=$signature.SignerCertificate.Subject}|ConvertTo-Json
