[CmdletBinding()]
param(
    [string]$OutputRoot = "C:\EdSys-Codex\_local-audits",
    [string]$AuditId = ("windows-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
)

$ErrorActionPreference = "Continue"
$auditPath = Join-Path $OutputRoot $AuditId
New-Item -ItemType Directory -Force -Path $auditPath | Out-Null

@{
    audit_id = $AuditId
    started_at = (Get-Date).ToString("s")
    host = $env:COMPUTERNAME
    safety = "read-only Windows metadata; review before committing"
} | ConvertTo-Json | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "metadata.json")

hostname | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "hostname.txt")
Get-ComputerInfo -Property OsName,OsVersion,OsBuildNumber,CsName,CsManufacturer,CsModel |
    Format-List | Out-String | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "computer-info.txt")
Get-NetIPConfiguration |
    Format-List | Out-String | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "net-ip-configuration.txt")
Get-NetAdapter |
    Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed |
    Format-Table -AutoSize | Out-String | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "net-adapters.txt")
arp -a | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "arp-a_REVIEW_BEFORE_COMMIT.txt")
route print | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "route-print_REVIEW_BEFORE_COMMIT.txt")
ipconfig /all | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "ipconfig-all_REVIEW_BEFORE_COMMIT.txt")

Write-Output "Windows baseline written to: $auditPath"
Write-Output "Review files ending REVIEW_BEFORE_COMMIT before copying any content into Git."
