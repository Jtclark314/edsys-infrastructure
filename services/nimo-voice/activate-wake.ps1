# Run only after the owner releases the live-voice hold and exits Nimo Voice.
# This installs the prepared client, initially OFF. It never opens a microphone.
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'EdSys\NimoVoice'
$prepared=Join-Path $root 'prepared-ok-nabu'
function Digest($path){(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()}
$status=Get-Content (Join-Path $prepared 'prepare-status.json') -Raw | ConvertFrom-Json
$manifest=Get-Content (Join-Path $prepared 'activation-manifest.json') -Raw | ConvertFrom-Json
if($status.state -ne 'prepared' -or $manifest.activation_performed){throw 'A fresh prepared candidate is required'}
if(@(Get-Process NimoVoice -ErrorAction SilentlyContinue).Count){throw 'Exit Nimo Voice and the TV remote normally before activating the new executable'}
foreach($file in $manifest.protected_files.PSObject.Properties){
 if((Digest (Join-Path $root $file.Name)) -ne $file.Value){throw 'Active client differs from prepared baseline; review required'}
}
foreach($file in $manifest.prepared_files.PSObject.Properties){
 if((Digest (Join-Path $prepared $file.Name)) -ne $file.Value){throw 'Prepared artifact digest differs'}
}
# Source is compile provenance only; refuse a moved/edited package.
foreach($name in @('NimoVoice.cs','WakeBridge.cs')){
 if((Digest (Join-Path $manifest.source_directory $name)) -ne $manifest.source_files.$name){throw 'Prepared source is missing'}
}
$recovery=Join-Path $root ('recovery\before-ok-nabu-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $recovery | Out-Null
foreach($name in @('NimoVoice.exe','NimoVoice.cs','start-assist.js','stop-assist.js')){Copy-Item (Join-Path $root $name) $recovery}
Copy-Item (Join-Path $prepared 'NimoVoice.exe') (Join-Path $root 'NimoVoice.exe') -Force
foreach($name in @('NimoVoice.cs','WakeBridge.cs')){Copy-Item (Join-Path $manifest.source_directory $name) $root -Force}
foreach($name in @('wake-idle.js','wake-capture.js','start-assist.js','stop-assist.js')){Copy-Item (Join-Path $prepared $name) $root -Force}
if((Digest (Join-Path $root 'NimoVoice.exe')) -ne $manifest.prepared_files.'NimoVoice.exe'){throw 'Activated executable digest differs'}
$manifest.activation_performed=$true
$manifest | Add-Member -NotePropertyName recovery_directory -NotePropertyValue $recovery
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $prepared 'activation-manifest.json') -Encoding UTF8
'Prepared0.4.0 installed; listening remains OFF. Launch Nimo Voice normally; enable OK Nabu explicitly from its tray only when live testing is allowed.'
