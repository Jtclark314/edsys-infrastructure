# Prepare beside the active client. Never start the application or open capture.
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'EdSys\NimoVoice'
$prepared=Join-Path $root 'prepared-ok-nabu'
New-Item -ItemType Directory -Path $prepared -Force | Out-Null
$receiptPath=Join-Path $prepared 'prepare-status.json'
function Write-Receipt($state,$detail){
 @{state=$state;detail=$detail;listening_enabled=$false;microphone_opened=$false;version='0.4.0'} |
  ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
}
function Digest($path){(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower()}
Write-Receipt 'running' 'Isolated runtime, model and native compilation checks only'
try {
 $protected=@{}
 foreach($name in @('NimoVoice.exe','NimoVoice.cs','settings.json','lg-remote.json','camera-view.js','start-assist.js','stop-assist.js','lg-key.js')){
  $file=Join-Path $root $name
  if(Test-Path $file){$protected[$name]=Digest $file}
 }
 if(-not $protected.ContainsKey('NimoVoice.exe') -or -not $protected.ContainsKey('settings.json')){throw 'Existing Nimo Voice installation required'}
 $beforeProcess=@(Get-Process NimoVoice -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
 $basePython=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
 if(-not(Test-Path $basePython)){throw 'Existing Python3.11 x64 required'}
 $runtime=Join-Path $root 'wake-runtime'
 if(-not(Test-Path $runtime)){
  & $basePython -m venv $runtime
  if($LASTEXITCODE -ne 0){throw 'Isolated Python environment creation failed'}
 }
 $python=Join-Path $runtime 'Scripts\python.exe'
 & $python -m pip --disable-pip-version-check install --require-hashes --only-binary=:all: -r (Join-Path $PSScriptRoot 'wake\requirements-windows.lock.txt')
 if($LASTEXITCODE -ne 0){throw 'Pinned isolated dependency installation failed'}
 $models=Join-Path $root 'wake-models'
 & $python -I (Join-Path $PSScriptRoot 'wake\prepare_models.py') --models-dir $models
 if($LASTEXITCODE -ne 0){throw 'Pinned model preparation failed'}
 $worker=Join-Path $root 'wake'
 if(Test-Path $worker){
  # Refuse to overwrite a different previous package without review.
  foreach($f in Get-ChildItem (Join-Path $PSScriptRoot 'wake') -File -Recurse){
   $relative=$f.FullName.Substring((Join-Path $PSScriptRoot 'wake').Length+1)
   $existing=Join-Path $worker $relative
   if((Test-Path $existing) -and (Digest $existing) -ne (Digest $f.FullName)){throw 'An earlier wake package differs; review before replacing'}
  }
 }
 Copy-Item (Join-Path $PSScriptRoot 'wake') $root -Recurse -Force
 foreach($name in @('wake-idle.js','wake-capture.js','start-assist.js','stop-assist.js')){Copy-Item (Join-Path $PSScriptRoot $name) $prepared -Force}
 # Native LiteRT diagnostics on stderr are informational; PowerShell5 can
 # promote redirected native stderr to a terminating error. Use exit status.
 $engineArguments='-I "'+(Join-Path $worker 'wake_worker.py')+'" --models-dir "'+$models+'" --check'
 $engine=Start-Process -FilePath $python -ArgumentList $engineArguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput (Join-Path $prepared 'engine-check.json') -RedirectStandardError (Join-Path $prepared 'engine-check.stderr.log')
 if($engine.ExitCode -ne 0){throw 'Offline Windows engine check failed'}
 $check=Get-Content (Join-Path $prepared 'engine-check.json') -Raw | ConvertFrom-Json
 if(-not $check.check -or $check.microphone_opened -ne $false -or $check.model_count -ne 3){throw 'Engine check did not establish audio-free preparation'}
 $compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
 foreach($dll in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll')){
  if((Get-AuthenticodeSignature (Join-Path $root $dll)).Status -ne 'Valid'){throw 'Installed WebView2 signature invalid'}
 }
 & $compiler /nologo /target:winexe /platform:x64 "/out:$prepared\NimoVoice.exe" "/reference:$root\Microsoft.Web.WebView2.Core.dll" "/reference:$root\Microsoft.Web.WebView2.WinForms.dll" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'NimoVoice.cs') (Join-Path $PSScriptRoot 'WakeBridge.cs')
 if($LASTEXITCODE -ne 0){throw 'Candidate native compilation failed'}
 & $compiler /nologo /target:exe /platform:x64 "/out:$prepared\WakeEpochTests.exe" /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'WakeBridge.cs') (Join-Path $PSScriptRoot 'TestWakeEpoch.cs')
 if($LASTEXITCODE -ne 0){throw 'Native test compilation failed'}
 & (Join-Path $prepared 'WakeEpochTests.exe') > (Join-Path $prepared 'native-check.txt')
 if($LASTEXITCODE -ne 0){throw 'Native lifecycle checks failed'}
 foreach($name in $protected.Keys){if((Digest (Join-Path $root $name)) -ne $protected[$name]){throw 'Protected active client file changed'}}
 $afterProcess=@(Get-Process NimoVoice -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
 if(@(Compare-Object $beforeProcess $afterProcess).Count -ne 0){throw 'Active Nimo Voice process changed during preparation'}
 $artifacts=@{}
 foreach($name in @('NimoVoice.exe','wake-idle.js','wake-capture.js','start-assist.js','stop-assist.js')){$artifacts[$name]=Digest (Join-Path $prepared $name)}
 @{format=1;protected_files=$protected;prepared_files=$artifacts;source_directory=$PSScriptRoot;source_files=@{'NimoVoice.cs'=(Digest (Join-Path $PSScriptRoot 'NimoVoice.cs'));'WakeBridge.cs'=(Digest (Join-Path $PSScriptRoot 'WakeBridge.cs'))};active_process_ids=$beforeProcess;activation_performed=$false} |
  ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $prepared 'activation-manifest.json') -Encoding UTF8
 Write-Receipt 'prepared' @{silence_frames=$check.silence_frames;model_count=$check.model_count;maximum_silence_score=$check.maximum_silence_score;candidate_sha256=$artifacts['NimoVoice.exe'];native_checks=(Get-Content (Join-Path $prepared 'native-check.txt') -Raw).Trim();active_process_ids=$beforeProcess}
} catch {
 Write-Receipt 'failed' $_.Exception.Message
 exit 1
}
