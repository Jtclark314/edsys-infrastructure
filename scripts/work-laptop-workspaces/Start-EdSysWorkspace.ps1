[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidateSet('Office','Remote')][string]$Mode)
$ErrorActionPreference='Stop'
$root=Join-Path $env:LOCALAPPDATA 'Microsoft\PowerToys\Workspaces'
$workspace=@((Get-Content (Join-Path $root 'workspaces.json') -Raw|ConvertFrom-Json).workspaces|Where-Object name -eq ('EdSys - '+$Mode))[0]
if(-not $workspace){throw 'The EdSys workspace is missing.'}
# Ensure the project Explorer window exists: the native launcher can mistake
# the desktop shell for an existing File Explorer window.
$shell=New-Object -ComObject Shell.Application
$folder=@($shell.Windows()|Where-Object {$_.FullName -match '\\explorer.exe$' -and $_.LocationURL -eq 'file:///R:/'})
if(-not $folder){Start-Process explorer.exe -ArgumentList 'R:\'}
$p=Start-Process (Join-Path $env:LOCALAPPDATA 'PowerToys\PowerToys.WorkspacesLauncher.exe') -ArgumentList ($workspace.id+' 1') -PassThru
if(-not $p.WaitForExit(60000)){throw 'Workspace launch did not finish within one minute.'}
Add-Type @'
using System;using System.Runtime.InteropServices;
public static class EdSysWindows {
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr after,int x,int y,int w,int ht,uint flags);
 [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
}
'@
$null=[EdSysWindows]::SetProcessDpiAwarenessContext([IntPtr](-4))
$results=foreach($a in $workspace.applications){
    $handles=@()
    $deadline=(Get-Date).AddSeconds(15)
    do {
        if($a.'application-path' -match '\\explorer.exe$'){
            $handles=@($shell.Windows()|Where-Object {$_.FullName -match '\\explorer.exe$' -and $_.LocationURL -eq 'file:///R:/'}|ForEach-Object {[IntPtr]$_.HWND})
        } else {
            $handles=@(Get-Process|Where-Object {$_.MainWindowHandle -ne 0 -and $_.Path -eq $a.'application-path'}|ForEach-Object {$_.MainWindowHandle})
        }
        if(-not $handles){Start-Sleep -Milliseconds 500}
    }while(-not $handles -and (Get-Date) -lt $deadline)
    foreach($h in $handles){
        # Restore before moving, then maximize on the target monitor. This also
        # fixes applications whose own startup placement overrides PowerToys.
        $null=[EdSysWindows]::ShowWindowAsync($h,9)
        Start-Sleep -Milliseconds 150
        $r=$a.position
        $null=[EdSysWindows]::SetWindowPos($h,[IntPtr]::Zero,$r.X,$r.Y,$r.width,$r.height,0x14)
        if($a.maximized){$null=[EdSysWindows]::ShowWindowAsync($h,3)}
        if($a.minimized){$null=[EdSysWindows]::ShowWindowAsync($h,6)}
    }
    [pscustomobject]@{Application=$a.application;Windows=$handles.Count;Monitor=$a.monitor;Minimized=$a.minimized}
}
$results|ConvertTo-Json|Set-Content (Join-Path $env:LOCALAPPDATA ('EdSys\Workspace-'+$Mode+'-status.json'))
if(@($results|Where-Object Windows -eq 0).Count){throw 'One or more workspace applications have no window; see the workspace status.'}
