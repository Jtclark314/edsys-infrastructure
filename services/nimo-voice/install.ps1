param([string]$HomeAssistantUrl)
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'EdSys\NimoVoice'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $root /inheritance:r /grant:r "$($identity):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Private app ACL could not be set'}
$package=Get-Content (Join-Path $PSScriptRoot 'webview2-package.json') -Raw | ConvertFrom-Json
$archive=Join-Path $root 'webview2.zip'
if(-not (Test-Path $archive)){Invoke-WebRequest -UseBasicParsing -Uri $package.url -OutFile $archive}
if((Get-FileHash $archive -Algorithm SHA256).Hash.ToLower() -ne $package.sha256){throw 'WebView2 SDK digest mismatch'}
$sdk=Join-Path $root 'sdk'
if(-not (Test-Path $sdk)){Expand-Archive -LiteralPath $archive -DestinationPath $sdk}
foreach($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll')){
 Copy-Item (Join-Path $sdk "lib\net462\$name") (Join-Path $root $name) -Force
}
Copy-Item (Join-Path $sdk 'runtimes\win-x64\native\WebView2Loader.dll') (Join-Path $root 'WebView2Loader.dll') -Force
foreach($name in @('Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll')){
 $signature=Get-AuthenticodeSignature (Join-Path $root $name)
 if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){throw 'WebView2 publisher verification failed'}
}
foreach($name in @('NimoVoice.cs','start-assist.js','stop-assist.js')){Copy-Item (Join-Path $PSScriptRoot $name) (Join-Path $root $name) -Force}
$config=Join-Path $root 'settings.json'
if(-not (Test-Path $config)){
 $uri=[Uri]$HomeAssistantUrl
 if($uri.Scheme -ne 'https' -or $uri.UserInfo){throw 'An existing HTTPS Home Assistant URL is required'}
 @{home_assistant_url=$uri.AbsoluteUri;pipeline_name='EdSys Living Room Voice'} | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding UTF8
}
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 "/out:$root\NimoVoice.exe" "/reference:$root\Microsoft.Web.WebView2.Core.dll" "/reference:$root\Microsoft.Web.WebView2.WinForms.dll" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll (Join-Path $root 'NimoVoice.cs')
if($LASTEXITCODE -ne 0){throw 'Nimo Voice compilation failed'}
$shell=New-Object -ComObject WScript.Shell
foreach($directory in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Startup'))){
 $shortcut=$shell.CreateShortcut((Join-Path $directory 'Nimo Voice.lnk'))
 $shortcut.TargetPath=Join-Path $root 'NimoVoice.exe'
 $shortcut.WorkingDirectory=$root
 $shortcut.Description='Talk to Home Assistant with Alt+V'
 $shortcut.Save()
}
Start-Process (Join-Path $root 'NimoVoice.exe')
'Nimo Voice installed for the current user; sign in once in its window.'
