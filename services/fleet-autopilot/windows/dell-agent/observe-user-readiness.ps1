[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$root=Join-Path $env:LOCALAPPDATA 'EdSys\FleetReadiness'
New-Item -ItemType Directory -Force $root | Out-Null
$script:checks=@()
function Add-Check($id,$label,$status,$detail) {
    $script:checks+=@{id=$id;label=$label;status=$status;detail=$detail}
}
$isAdmin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$sessionId=(Get-Process -Id $PID).SessionId
if($isAdmin -or $sessionId -eq 0) { throw 'Readiness observer requires a limited interactive desktop session.' }
$expected=[ordered]@{
 F='\\basecamp\03_Files'; I='\\9950x.taile832fe.ts.net\Foothills-Inbox\ask-foothills-intake'
 K='\\basecamp\Kindle-Drop'; Q='\\9950x.taile832fe.ts.net\EdSys-Share'
 R='\\9950x.taile832fe.ts.net\Foothills-Project'; S='\\basecamp\Foothills_ASI'
 T='\\basecamp\07_Transfer'; U='\\basecamp\Foothills_Unit_Selections_Intake'
}
$shell=New-Object -ComObject Shell.Application
$visible=@($shell.Namespace(17).Items() | ForEach-Object {$_.Path})
foreach($letter in $expected.Keys) {
 $status='warning';$detail='Mapping missing, incorrect, or not visible in Explorer.'
 try {
  $mapping=Get-SmbMapping -LocalPath ($letter+':') -ErrorAction Stop
  if($mapping.RemotePath.TrimEnd('\') -ieq $expected[$letter].TrimEnd('\') -and $visible -contains ($letter+':\')) {
   # SMB can block beyond PowerShell timeouts; isolate and bound each root read.
   $code="if(Test-Path -LiteralPath '${letter}:\'){exit 0}else{exit 1}"
   $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
   $proc=Start-Process powershell.exe -ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand',$encoded) -WindowStyle Hidden -PassThru
   try {
    if($proc.WaitForExit(4000)) {
     if($proc.ExitCode -eq 0) {$status='ok';$detail='Expected mapping is visible and readable in the normal desktop session.'}
     else {$detail='Expected mapping is visible but its root is not readable.'}
    } else { $proc.Kill();$detail='Root read exceeded four seconds.' }
   } finally {$proc.Dispose()}
  }
 } catch {$detail='Could not confirm the expected desktop mapping.'}
 Add-Check ('drive-'+$letter) ($letter+': drive') $status $detail
}
[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
$versions=@{}
try {$versions.codex_desktop=[string](Get-AppxPackage OpenAI.Codex | Select-Object -First 1).Version} catch {}
$known=@{
 powertoys=(Join-Path $env:LOCALAPPDATA 'PowerToys\PowerToys.exe')
 office='C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE'
 bluebeam='C:\Program Files\Bluebeam Software\Bluebeam Revu\21\Revu\Revu.exe'
 sunshine='C:\Program Files\Sunshine\sunshine.exe'
}
foreach($key in $known.Keys) {if(Test-Path -LiteralPath $known[$key]) {$versions[$key]=[string](Get-Item $known[$key]).VersionInfo.ProductVersion}}
$uninstall=@(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue)
foreach($pair in @(@('sunshine','Sunshine'),@('bluebeam','Bluebeam Revu x64'))) {
 $app=$uninstall | Where-Object DisplayName -eq $pair[1] | Select-Object -First 1
 if($app) {$versions[$pair[0]]=[string]$app.DisplayVersion}
}
try {
 # API key remains in memory and is never included in telemetry or errors.
 [xml]$config=Get-Content (Join-Path $env:LOCALAPPDATA 'Syncthing\config.xml') -Raw
 $address=[string]$config.configuration.gui.address
 if($address -notmatch '^(127\.0\.0\.1|localhost):[0-9]+$') {throw 'Unsupported GUI binding'}
 $base='http://'+$address
 $headers=@{'X-API-Key'=[string]$config.configuration.gui.apikey}
 $system=Invoke-RestMethod "$base/rest/system/version" -Headers $headers -TimeoutSec 4
 $versions.syncthing=[string]$system.version
 $folderResponse=Invoke-RestMethod "$base/rest/config/folders" -Headers $headers -TimeoutSec 4
 $folders=@($folderResponse | Where-Object {$_ -and $_.id})
 $bad=0;$syncing=0;$paused=0
 foreach($folder in $folders) {
  if([string]$folder.paused -eq 'true') {$paused++;continue}
  $id=[Uri]::EscapeDataString([string]$folder.id)
  $state=Invoke-RestMethod "$base/rest/db/status?folder=$id" -Headers $headers -TimeoutSec 4
  if($state.error -or [int]$state.pullErrors -gt 0 -or $state.state -eq 'error') {$bad++}
  elseif([long]$state.needTotalItems -gt 0 -or $state.state -eq 'syncing') {$syncing++}
 }
 $status=if($bad -gt 0){'warning'}elseif($folders.Count -eq 0){'not_applicable'}else{'ok'}
 Add-Check 'sync' 'Syncthing' $status ("{0} folders; {1} with errors; {2} syncing; {3} paused." -f $folders.Count,$bad,$syncing,$paused)
} catch {Add-Check 'sync' 'Syncthing' 'unknown' 'Local sync API could not be checked; process presence alone is not sync health.'}
$result=@{schema_version=1;checked_at=[DateTime]::UtcNow.ToString('o');session_id=$sessionId;elevated=$false;checks=$script:checks;versions=$versions}
$temp=Join-Path $root ('status-'+[guid]::NewGuid().ToString('N')+'.tmp')
[IO.File]::WriteAllText($temp,($result|ConvertTo-Json -Depth 8 -Compress),(New-Object Text.UTF8Encoding($false)))
Move-Item $temp (Join-Path $root 'status.json') -Force
