param([Parameter(Mandatory = $true)][string]$Url)
$ErrorActionPreference = 'Stop'
$uri = [Uri]$Url
if ($uri.Scheme -ne 'https' -or $uri.Host -ne '9950x.taile832fe.ts.net' -or
    $uri.Port -ne 8444 -or $uri.UserInfo -or $uri.Query) {
    throw 'Expected the private OpenCode HTTPS address without embedded credentials.'
}
$desktop = [Environment]::GetFolderPath('Desktop')
$target = Join-Path $desktop 'Local AI - OpenCode.url'
$marker = '; Managed by EdSys local-coder'
if ((Test-Path $target) -and -not (Get-Content -Raw $target).Contains($marker)) {
    throw 'An unrelated shortcut already uses this name; it was preserved.'
}
Set-Content -Encoding Ascii -Path $target -Value "$marker`r`n[InternetShortcut]`r`nURL=$Url`r`n"
Write-Output 'Local AI - OpenCode desktop shortcut is ready.'
