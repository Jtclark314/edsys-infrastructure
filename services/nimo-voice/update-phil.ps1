# Run as the ordinary signed-in Nimo user after this three-script package and
# its reviewed before/after manifest have been copied to a private staging dir.
param([string]$StagedPath=$PSScriptRoot)
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'EdSys\NimoVoice'
$manifest=Get-Content (Join-Path $StagedPath 'manifest.json') -Raw | ConvertFrom-Json
$names=@('camera-view.js','start-assist.js','stop-assist.js')
if(@($manifest.PSObject.Properties).Count -ne 3){throw 'Unexpected script package'}
$allCurrent=$true
foreach($name in $names){
 $entry=$manifest.$name
 if(-not $entry -or $entry.before -notmatch '^[a-f0-9]{64}$' -or $entry.after -notmatch '^[a-f0-9]{64}$'){throw 'Invalid digest manifest'}
 $source=(Get-FileHash (Join-Path $StagedPath $name) -Algorithm SHA256).Hash.ToLower()
 $current=(Get-FileHash (Join-Path $root $name) -Algorithm SHA256).Hash.ToLower()
 if($source -ne $entry.after){throw 'Staged source differs from reviewed source'}
 if($current -ne $entry.after){$allCurrent=$false}
 if($current -ne $entry.before -and $current -ne $entry.after){throw 'Installed script changed; preserve it and inspect'}
}
if($allCurrent){'Reviewed Phil scripts are already installed.';return}
$backup=Join-Path $root ('recovery\phil-voice-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach($name in $names){Copy-Item (Join-Path $root $name) (Join-Path $backup $name)}
foreach($name in $names){
 $temporary=Join-Path $root ('.phil-'+[Guid]::NewGuid().ToString()+'.js')
 Copy-Item (Join-Path $StagedPath $name) $temporary
 Move-Item -LiteralPath $temporary -Destination (Join-Path $root $name) -Force
 if((Get-FileHash (Join-Path $root $name) -Algorithm SHA256).Hash.ToLower() -ne $manifest.$name.after){throw 'Installed digest uncertain; inspect before retry'}
}
'Phil scripts installed. The next Alt+V loads them; sign-in, executable, microphone and audio settings are retained.'
