param([Parameter(Mandatory=$true)][string]$RelayAddress)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$ip=[Net.IPAddress]::Parse($RelayAddress)
if ($ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'IPv4 relay required' }
$root=Join-Path $env:LOCALAPPDATA 'EdSys\KaliStream'
$report=[ordered]@{paired=$false;shortcut=$false;error=$null}
try {
 $exe=Join-Path $env:ProgramFiles 'Moonlight Game Streaming\Moonlight.exe'
 if ((Get-AuthenticodeSignature $exe).Status -ne 'Valid') { throw 'Moonlight signature check failed' }
 if (Get-Process Moonlight -ErrorAction SilentlyContinue) { throw 'Moonlight is already open; preserve its session' }
 function Test-DesktopPairing {
  $list=Start-Process $exe -WindowStyle Normal -ArgumentList "list $RelayAddress" -PassThru -RedirectStandardOutput (Join-Path $root 'apps.out') -RedirectStandardError (Join-Path $root 'apps.err')
  $handle=$list.Handle
  if (-not $list.WaitForExit(15000)) { Stop-Process -Id $list.Id; return $false }
  return (Get-Content (Join-Path $root 'apps.out') -Raw) -match 'Desktop'
 }
 if (-not (Test-DesktopPairing)) {
  $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
  $bytes=New-Object byte[] 4
  $rng.GetBytes($bytes); $rng.Dispose()
  $pin=([BitConverter]::ToUInt32($bytes,0) % 10000).ToString('D4')
  $pair=Start-Process $exe -WindowStyle Normal -ArgumentList "pair $RelayAddress --pin $pin" -PassThru
  $handle=$pair.Handle
  Start-Sleep -Seconds 2
  $pin | & ssh.exe -o BatchMode=yes 9950x 'edcore-control lab run -- sudo -u jeremy python3 /usr/local/libexec/edsys-kali-stream/pair-local.py' | Out-Null
  $accepted=$LASTEXITCODE -eq 0
  $pin=$null
  if (-not $accepted) { throw 'Sunshine did not accept pairing' }
  # Moonlight 6.1 pairing deliberately waits for its success dialog to close.
  Add-Type -AssemblyName UIAutomationClient
  $condition=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty,$pair.Id)
  $dismissed=$false
  for($attempt=0;$attempt -lt 15;$attempt++) {
   $nodes=[Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Descendants,$condition)
   if (@($nodes | Where-Object {$_.Current.Name -eq 'Pairing completed successfully'}).Count) {
    foreach($node in $nodes) { if($node.Current.Name -eq 'OK') { $node.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke(); $dismissed=$true; break } }
    break
   }
   Start-Sleep -Seconds 1
  }
  if (-not $dismissed -or -not $pair.WaitForExit(10000)) { throw 'Pairing confirmation did not close' }
  if (-not (Test-DesktopPairing)) { throw 'Paired Desktop application not verified' }
 }
 $report.paired=$true
 $shell=New-Object -ComObject WScript.Shell
 $path=Join-Path ([Environment]::GetFolderPath('Desktop')) 'Kali Desktop.lnk'
 if (Test-Path $path) { Copy-Item $path (Join-Path $root ('shortcut-before-'+(Get-Date -Format yyyyMMddHHmmss)+'.lnk')) }
 $link=$shell.CreateShortcut($path)
 $link.TargetPath=$exe
 $link.Arguments="--display-mode borderless --resolution 1920x1080 --fps 60 --bitrate 20000 --video-codec H.264 --video-decoder hardware --capture-system-keys always --absolute-mouse --quit-after stream $RelayAddress Desktop"
 $link.WorkingDirectory=Split-Path $exe
 $link.IconLocation="$exe,0"
 $link.Description='Kali desktop over the private Nimo-only streaming relay'
 $link.Save()
 $check=$shell.CreateShortcut($path)
 if ($check.TargetPath -ne $exe -or $check.Arguments -notlike "*stream $RelayAddress Desktop") { throw 'Shortcut validation failed' }
 $report.shortcut=$true
} catch {
 $report.error=$_.Exception.Message
} finally {
 $report | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $root 'setup-result.json')
}
