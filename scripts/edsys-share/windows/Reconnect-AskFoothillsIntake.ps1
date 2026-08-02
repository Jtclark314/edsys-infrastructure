$ProgressPreference='SilentlyContinue'
$ErrorActionPreference='Stop'
$LocalPath='I:'
$ServerName='9950x.taile832fe.ts.net'
$ShareName='Foothills-Inbox'
$SubPath='ask-foothills-intake'
$Label='Ask Foothills Intake'
$RemotePath=([string][char]92+[char]92+$ServerName+[char]92+$ShareName+[char]92+$SubPath)
$statePath=Join-Path $env:LOCALAPPDATA 'EdSys\Ask-Foothills-Intake-I-status.json'
function Write-State([string]$Status,[string]$Detail,[bool]$Reachable){[pscustomobject]@{status=$Status;detail=$Detail;time=(Get-Date).ToString('o');localPath=$LocalPath;remotePath=$RemotePath;label=$Label;reachable=$Reachable;user=[Security.Principal.WindowsIdentity]::GetCurrent().Name;sessionId=(Get-Process -Id $PID).SessionId}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $statePath -Encoding UTF8}
function Test-SmbEndpoint{$client=New-Object Net.Sockets.TcpClient;try{$connect=$client.BeginConnect($ServerName,445,$null,$null);if(-not$connect.AsyncWaitHandle.WaitOne(3000,$false)){return $false};$client.EndConnect($connect);return $true}catch{return $false}finally{$client.Close()}}
try{
 Write-State 'running' 'Script started.' $false
 $deadline=(Get-Date).AddSeconds(600)
 while(-not(Test-SmbEndpoint)){if((Get-Date)-ge$deadline){throw 'SMB endpoint did not become ready within 600 seconds.'};Start-Sleep -Seconds 5}
 Write-State 'running' 'SMB endpoint is reachable.' $false
 $logical=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$LocalPath'" -ErrorAction SilentlyContinue
 if($null-ne$logical -and $logical.ProviderName -and $logical.ProviderName -ine $RemotePath){
   Write-State 'running' ("Replacing the prior I mapping from $($logical.ProviderName).") $false
 }
 & "$env:SystemRoot\System32\cmd.exe" /d /c "net use $LocalPath /delete /y >nul 2>&1" | Out-Null
 Write-State 'running' 'Creating the dedicated Ask Foothills intake mapping.' $false
 & "$env:SystemRoot\System32\cmd.exe" /d /c "net use $LocalPath $RemotePath /persistent:yes >nul 2>&1"
 $netExit=$LASTEXITCODE
 if($netExit-ne0){throw "net use returned exit $netExit."}
 if(-not(Test-Path -LiteralPath ($LocalPath+'\'))){throw 'Mapping exists but is not accessible.'}
 $mountRoot='HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2'
 $oldMountKey=Join-Path $mountRoot '##9950x.taile832fe.ts.net#Foothills-Inbox'
 $mountKey=Join-Path $mountRoot '##9950x.taile832fe.ts.net#Foothills-Inbox#ask-foothills-intake'
 Remove-ItemProperty -LiteralPath $oldMountKey -Name '_LabelFromReg' -ErrorAction SilentlyContinue
 New-Item -Path $mountKey -Force|Out-Null
 New-ItemProperty -Path $mountKey -Name '_LabelFromReg' -PropertyType String -Value $Label -Force|Out-Null
 Write-State 'ok' 'Dedicated Ask Foothills intake mapping and File Explorer label are ready.' $true
}catch{Write-State 'error' $_.Exception.Message $false;exit 1}
