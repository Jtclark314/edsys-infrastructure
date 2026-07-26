param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9_-]{1,20}$")]
    [string]$DeviceName
)

$ErrorActionPreference = "Stop"
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$PrincipalCheck = [Security.Principal.WindowsPrincipal]::new($Identity)
if (-not $PrincipalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}

$Group = "KindleDropClients"
$UserName = ("kindledrop-" + $DeviceName).ToLowerInvariant()
$Password = Read-Host "Enter a unique password for $UserName" -AsSecureString
if (Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue) {
    Set-LocalUser -Name $UserName -Password $Password -UserMayChangePassword $false
} else {
    New-LocalUser -Name $UserName -Password $Password `
        -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword `
        -Description "SMB-only Kindle Drop identity for $DeviceName" | Out-Null
}

Add-LocalGroupMember -Group $Group -Member $UserName -ErrorAction SilentlyContinue
Remove-LocalGroupMember -Group "Users" -Member $UserName -ErrorAction SilentlyContinue

Get-SmbShare | Where-Object {
    $_.Name -ne "Kindle-Drop" -and -not $_.Special
} | ForEach-Object {
    Block-SmbShareAccess -Name $_.Name -AccountName "$env:COMPUTERNAME\$UserName" -Force |
        Out-Null
}

$DeniedRights = @(
    "SeDenyInteractiveLogonRight",
    "SeDenyRemoteInteractiveLogonRight",
    "SeDenyBatchLogonRight",
    "SeDenyServiceLogonRight"
)
$Temporary = Join-Path $env:TEMP ("kindledrop-rights-" + [guid]::NewGuid() + ".inf")
$Database = Join-Path $env:TEMP ("kindledrop-rights-" + [guid]::NewGuid() + ".sdb")
try {
    secedit.exe /export /cfg $Temporary /areas USER_RIGHTS | Out-Null
    $Sid = (New-Object Security.Principal.NTAccount("$env:COMPUTERNAME\$UserName")).
        Translate([Security.Principal.SecurityIdentifier]).Value
    $Lines = Get-Content $Temporary
    foreach ($Right in $DeniedRights) {
        $Pattern = "^" + [regex]::Escape($Right) + "\s*="
        $Index = -1
        for ($i = 0; $i -lt $Lines.Count; $i++) {
            if ($Lines[$i] -match $Pattern) {
                $Index = $i
                break
            }
        }
        if ($Index -ge 0) {
            if ($Lines[$Index] -notmatch [regex]::Escape($Sid)) {
                $Lines[$Index] = $Lines[$Index].TrimEnd() + ",*$Sid"
            }
        } else {
            $Lines += "$Right = *$Sid"
        }
    }
    Set-Content -Encoding Unicode -Path $Temporary -Value $Lines
    secedit.exe /configure /db $Database /cfg $Temporary /areas USER_RIGHTS /quiet
    if ($LASTEXITCODE -ne 0) {
        throw "secedit failed with exit code $LASTEXITCODE"
    }
} finally {
    Remove-Item -Force $Temporary, $Database -ErrorAction SilentlyContinue
}

Write-Host "Created or rotated $env:COMPUTERNAME\$UserName."
Write-Host "The identity is in KindleDropClients, blocked from other ordinary shares,"
Write-Host "and denied interactive, Remote Desktop, batch, and service logon."
