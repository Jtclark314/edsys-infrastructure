[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$RecipientCertificate, [string]$AccountName='EdSysWorkShares', [switch]$ResumeIncomplete)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
if($env:COMPUTERNAME -ne 'BASECAMP'){throw 'Run only on Basecamp.'}
if($AccountName -ne 'EdSysWorkShares'){throw 'Only the dedicated work-laptop identity is supported.'}
$existing=Get-LocalUser $AccountName -ErrorAction SilentlyContinue
if($existing -and (-not $ResumeIncomplete -or $existing.Enabled -or $existing.Description -ne 'Work laptop access to five EdSys SMB shares')){throw 'Account already exists outside a disabled incomplete setup; inspect before changing it.'}
$shares=@('03_Files','Kindle-Drop','Foothills_ASI','07_Transfer','Foothills_Unit_Selections_Intake')
$definitions=@($shares|ForEach-Object {Get-SmbShare -Name $_})
foreach($s in $definitions){if(-not (Get-SmbShareAccess $s.Name|Where-Object {$_.AccountName -eq 'BASECAMP\FoothillsShares' -and $_.AccessControlType -eq 'Allow'})){throw "Existing limited share access missing: $($s.Name)"}}
# Validate the public recipient before creating an account.
$null=Protect-CmsMessage -To $RecipientCertificate -Content 'preflight'
$root=Join-Path $env:ProgramData 'EdSys\WorkLaptopShares'
New-Item -ItemType Directory -Force $root|Out-Null
$acl=New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))}
Set-Acl $root $acl
$backup=@($definitions|ForEach-Object {@{Name=$_.Name;Path=$_.Path;SDDL=(Get-Acl $_.Path).Sddl;ShareAccess=@(Get-SmbShareAccess $_.Name|Select-Object AccountName,AccessControlType,AccessRight)}})
if(-not (Test-Path (Join-Path $root 'before-acls.json'))){$backup|ConvertTo-Json -Depth 6|Set-Content (Join-Path $root 'before-acls.json')}
$bytes=New-Object byte[] 36;$rng=[Security.Cryptography.RandomNumberGenerator]::Create();$rng.GetBytes($bytes);$rng.Dispose()
$password=[Convert]::ToBase64String($bytes)+'aA9!';[Array]::Clear($bytes,0,$bytes.Length)
$secure=ConvertTo-SecureString $password -AsPlainText -Force
if($existing){Set-LocalUser -Name $AccountName -Password $secure;$user=Get-LocalUser $AccountName}else{$user=New-LocalUser -Name $AccountName -Password $secure -Description 'Work laptop access to five EdSys SMB shares' -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword -Disabled}
# Deny local-console and RDP sign-in. No administrator or RDP group is added.
Add-Type @'
using System;using System.Runtime.InteropServices;using System.Security.Principal;
public static class ShareLogonRights {
 [StructLayout(LayoutKind.Sequential)] struct OA {public uint Length;public IntPtr RootDirectory,ObjectName;public uint Attributes;public IntPtr SecurityDescriptor,SecurityQualityOfService;}
 [StructLayout(LayoutKind.Sequential)] struct US {public ushort Length,MaximumLength;public IntPtr Buffer;}
 [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr name,ref OA a,uint access,out IntPtr h);
 [DllImport("advapi32.dll")] static extern uint LsaAddAccountRights(IntPtr h,IntPtr sid,US[] rights,uint count);
 [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr h);
 public static void DenyInteractive(string sid){var a=new OA();a.Length=(uint)Marshal.SizeOf(a);IntPtr h;uint r=LsaOpenPolicy(IntPtr.Zero,ref a,0x810,out h);if(r!=0)throw new Exception("LsaOpenPolicy failed: "+r);var s=new SecurityIdentifier(sid);byte[] b=new byte[s.BinaryLength];s.GetBinaryForm(b,0);IntPtr p=Marshal.AllocHGlobal(b.Length);Marshal.Copy(b,0,p,b.Length);try{foreach(string n in new[]{"SeDenyInteractiveLogonRight","SeDenyRemoteInteractiveLogonRight"}){var u=new US{Length=(ushort)(n.Length*2),MaximumLength=(ushort)((n.Length+1)*2),Buffer=Marshal.StringToHGlobalUni(n)};try{r=LsaAddAccountRights(h,p,new[]{u},1);if(r!=0)throw new Exception("LsaAddAccountRights failed: "+r);}finally{Marshal.FreeHGlobal(u.Buffer);}}}finally{Marshal.FreeHGlobal(p);LsaClose(h);}}
}
'@
try {
    [ShareLogonRights]::DenyInteractive($user.SID.Value)
    foreach($s in $definitions){
        $acl=Get-Acl $s.Path
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($user.SID,'Modify','ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule);Set-Acl $s.Path $acl
        Grant-SmbShareAccess -Name $s.Name -AccountName ("BASECAMP\"+$AccountName) -AccessRight Change -Force|Out-Null
    }
    $credential=New-Object PSCredential(("BASECAMP\"+$AccountName),$secure)
    Add-Type -AssemblyName System.Security
    $raw=[Text.Encoding]::Unicode.GetBytes($password)
    $protected=[Security.Cryptography.ProtectedData]::Protect($raw,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
    [IO.File]::WriteAllBytes((Join-Path $root 'credential.machine.dpapi'),$protected)
    [Array]::Clear($raw,0,$raw.Length)
    $payload=@{user=$credential.UserName;blob=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($password))}|ConvertTo-Json -Compress
    Protect-CmsMessage -To $RecipientCertificate -Content $payload | Set-Content -Encoding ASCII (Join-Path $root 'credential.cms')
    $payload=$null;$password=$null
    Enable-LocalUser $AccountName
    [pscustomobject]@{Account=$credential.UserName;EncryptedHandoff=(Join-Path $root 'credential.cms');Shares=$shares;InteractiveLogonDenied=$true;Administrator=$false}
} catch {
    Disable-LocalUser $AccountName
    throw
}
