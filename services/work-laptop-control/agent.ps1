param([string]$Root=$PSScriptRoot)
$ErrorActionPreference='Stop'
trap { [IO.File]::WriteAllText((Join-Path $Root 'startup-error.txt'),($_|Out-String));exit 1 }
$env:WINAPP_CLI_TELEMETRY_OPTOUT='1'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class EdSysDesktop {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint f, bool i, uint a);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 public static bool Available() { var h=OpenInputDesktop(0,false,0x0100); if(h==IntPtr.Zero)return false; CloseDesktop(h); return true; }
}
'@
[EdSysDesktop]::SetProcessDPIAware()|Out-Null
$mutex=New-Object Threading.Mutex($false,('Local\EdSysComputerControl-'+[Security.Principal.WindowsIdentity]::GetCurrent().User.Value))
if(-not $mutex.WaitOne(0)){exit 0}
$paused=Join-Path $Root 'paused'
$tray=New-Object Windows.Forms.NotifyIcon
$tray.Icon=[Drawing.SystemIcons]::Application
$tray.Text='EdSys Codex control: ready'
$tray.Visible=$true
$menu=New-Object Windows.Forms.ContextMenuStrip
$pauseItem=$menu.Items.Add('Pause Codex desktop control')
$pauseItem.add_Click({[IO.File]::WriteAllText($paused,'Paused locally')})
$resumeItem=$menu.Items.Add('Resume Codex desktop control')
$resumeItem.add_Click({if(Test-Path $paused){Remove-Item $paused -Force}})
$tray.ContextMenuStrip=$menu

function Write-JsonAtomic($Path,$Value){
 $tmp=$Path+'.tmp'
 [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 20 -Compress),(New-Object Text.UTF8Encoding($false)))
 Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Quote-Argument([string]$Value){
 # CommandLineToArgvW quoting; no cmd.exe or Invoke-Expression interpolation.
 '"'+([regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1'))+'"'
}
function Run-Process([string]$Exe,[string[]]$Arguments,[int]$Seconds=25){
 $si=New-Object Diagnostics.ProcessStartInfo
 $si.FileName=$Exe
 $si.Arguments=($Arguments|ForEach-Object {Quote-Argument $_}) -join ' '
 $si.WorkingDirectory=$Root
 $si.UseShellExecute=$false;$si.CreateNoWindow=$true
 $si.RedirectStandardOutput=$true;$si.RedirectStandardError=$true
 $si.StandardOutputEncoding=[Text.Encoding]::UTF8;$si.StandardErrorEncoding=[Text.Encoding]::UTF8
 $p=New-Object Diagnostics.Process;$p.StartInfo=$si
 try {
  if(-not $p.Start()){throw 'process_start_failed'}
  $out=$p.StandardOutput.ReadToEndAsync();$err=$p.StandardError.ReadToEndAsync()
  $deadline=[DateTime]::UtcNow.AddSeconds($Seconds)
  while(-not $p.HasExited){
   [Windows.Forms.Application]::DoEvents()
   if((Test-Path $paused) -or [DateTime]::UtcNow -gt $deadline){
    & taskkill.exe /PID $p.Id /T /F 2>&1|Out-Null
    throw 'action_interrupted_or_timed_out; outcome may be partial; inspect before retry'
   }
   Start-Sleep -Milliseconds 50
  }
  @{exit_code=$p.ExitCode;stdout=$out.Result;stderr=$err.Result}
 } finally {$p.Dispose()}
}
function Get-Status {
 @{version='1.0.0';session=(Get-Process -Id $PID).SessionId;paused=(Test-Path $paused);
 interactive=[EdSysDesktop]::Available();winapp_version='0.6.0';
 monitors=@([Windows.Forms.Screen]::AllScreens|ForEach-Object {@{device=$_.DeviceName;primary=$_.Primary;x=$_.Bounds.X;y=$_.Bounds.Y;width=$_.Bounds.Width;height=$_.Bounds.Height}})}
}
function Dispatch($Request){
 if($Request.action -eq 'status'){return Get-Status}
 if(Test-Path $paused){throw 'desktop_control_paused_locally'}
 if(-not [EdSysDesktop]::Available()){throw 'no_interactive_desktop; unlock the Windows session'}
 switch($Request.action){
  'ui' {
   $a=@($Request.arguments)
   $allowed=@('status','inspect','search','get-property','get-value','screenshot','record','invoke','click','drag','touch','pen','hover','send-keys','set-value','focus','scroll-into-view','scroll','wait-for','list-windows','get-focused')
   if($a.Count -lt 1 -or $a[0] -notin $allowed){throw 'unsupported_ui_command'}
   if($a.Count -gt 80 -or (($a -join '').Length -gt 30000)){throw 'arguments_too_large'}
   $artifact=$null
   if($a[0] -in @('screenshot','record')){
    if($a -contains '--output' -or $a -contains '-o' -or $a -contains '--frames'){throw 'artifact_path_managed_by_dispatcher'}
    $extension=if($a[0] -eq 'record'){'mp4'}else{'png'}
    $artifact=$Request.id+'.'+$extension
    $a+=@('--output',(Join-Path "$Root/artifacts" $artifact))
    if($a[0] -eq 'record' -and $a -notcontains '--duration-sec'){$a+=@('--duration-sec','5')}
   }
   $value=Run-Process "$Root/bin/winapp.exe" (@('ui')+$a)
   if($artifact -and (Test-Path "$Root/artifacts/$artifact")){$value.artifact=$artifact}
   return $value
  }
  'launch' {
   $exe=[string]$Request.executable
   if(-not [IO.Path]::IsPathRooted($exe) -or -not (Test-Path -LiteralPath $exe -PathType Leaf)){throw 'launch_requires_existing_absolute_executable'}
   $si=New-Object Diagnostics.ProcessStartInfo
   $si.FileName=$exe;$si.Arguments=(@($Request.arguments)|ForEach-Object {Quote-Argument $_}) -join ' '
   $si.UseShellExecute=$false;$si.WorkingDirectory=$Root
   $p=[Diagnostics.Process]::Start($si)
   return @{process_id=$p.Id;started=$true}
  }
  'powershell' {
   $script='$ErrorActionPreference=''Stop'';$ProgressPreference=''SilentlyContinue'';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);'+[string]$Request.script
   $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
   return Run-Process "$env:SystemRoot/System32/WindowsPowerShell/v1.0/powershell.exe" @('-NoProfile','-NonInteractive','-ExecutionPolicy','RemoteSigned','-EncodedCommand',$encoded)
  }
  'screenshot' {
   $screens=@([Windows.Forms.Screen]::AllScreens)
   $index=[int]$Request.monitor
   $screen=if($index -eq -1){[Windows.Forms.Screen]::PrimaryScreen}elseif($index -ge 0 -and $index -lt $screens.Count){$screens[$index]}else{throw 'monitor_index_out_of_range'}
   $b=$screen.Bounds
   $bitmap=New-Object Drawing.Bitmap($b.Width,$b.Height)
   $g=[Drawing.Graphics]::FromImage($bitmap)
   $name=$Request.id+'.png'
   try {$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);$bitmap.Save("$Root/artifacts/$name",[Drawing.Imaging.ImageFormat]::Png)}finally{$g.Dispose();$bitmap.Dispose()}
   return @{artifact=$name;origin_x=$b.X;origin_y=$b.Y;width=$b.Width;height=$b.Height}
  }
  default {throw 'unsupported_action'}
 }
}
$lastCleanup=[DateTime]::MinValue
try {
 while($true){
  [Windows.Forms.Application]::DoEvents()
  $tray.Text=if(Test-Path $paused){'EdSys Codex control: PAUSED'}else{'EdSys Codex control: ready'}
  foreach($file in @(Get-ChildItem "$Root/requests/*.json" -ErrorAction SilentlyContinue|Sort-Object Name)){
   $id=$file.BaseName
   if($id -notmatch '^[a-f0-9]{32}$'){Remove-Item -LiteralPath $file.FullName -Force;continue}
   $claimed=$file.FullName+'.running'
   Move-Item -LiteralPath $file.FullName -Destination $claimed
   $start=[DateTime]::UtcNow
   try {
    $request=Get-Content -LiteralPath $claimed -Raw -Encoding UTF8|ConvertFrom-Json
    if($request.id -ne $id -or [DateTime]::Parse($request.expires).ToUniversalTime() -lt $start){throw 'request_expired_or_invalid; not executed'}
    $tray.Icon=[Drawing.SystemIcons]::Warning;$tray.Text='EdSys Codex control: EXECUTING'
    $value=Dispatch $request
    $result=@{id=$id;ok=$true;value=$value}
   } catch {$result=@{id=$id;ok=$false;error=$_.Exception.Message}}
   $result.completed_at=[DateTime]::UtcNow.ToString('o')
   Write-JsonAtomic "$Root/responses/$id.json" $result
   # Audit metadata only: never typed text, arguments, output, or screen content.
   @{id=$id;action=$request.action;ok=$result.ok;at=$result.completed_at}|ConvertTo-Json -Compress|Add-Content "$Root/audit.jsonl" -Encoding UTF8
   Remove-Item -LiteralPath $claimed -Force
   $tray.Icon=[Drawing.SystemIcons]::Application
  }
  if(([DateTime]::UtcNow-$lastCleanup).TotalSeconds -gt 60){
   Write-JsonAtomic "$Root/heartbeat.json" @{at=[DateTime]::UtcNow.ToString('o');pid=$PID;status=(Get-Status)}
   Get-ChildItem "$Root/responses","$Root/artifacts" -File|Where-Object LastWriteTimeUtc -lt ([DateTime]::UtcNow.AddHours(-1))|Remove-Item -Force
   Get-ChildItem "$Root/requests" -File|Where-Object LastWriteTimeUtc -lt ([DateTime]::UtcNow.AddHours(-1))|Remove-Item -Force
   if((Test-Path "$Root/audit.jsonl") -and (Get-Item "$Root/audit.jsonl").Length -gt 1MB){Move-Item "$Root/audit.jsonl" "$Root/audit.previous.jsonl" -Force}
   $lastCleanup=[DateTime]::UtcNow
  }
  Start-Sleep -Milliseconds 150
 }
} finally {$tray.Visible=$false;$tray.Dispose();$mutex.ReleaseMutex();$mutex.Dispose()}
