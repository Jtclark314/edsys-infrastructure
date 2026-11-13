param(
    [Parameter(Mandatory = $true)][string]$Url,
    [switch]$AppMode
)
$ErrorActionPreference = 'Stop'
$uri = [Uri]$Url
if ($uri.Scheme -ne 'https' -or $uri.Host -ne '9950x.taile832fe.ts.net' -or
    $uri.Port -ne 8444 -or $uri.UserInfo -or $uri.Query) {
    throw 'Expected the private OpenCode HTTPS address without embedded credentials.'
}
$desktop = [Environment]::GetFolderPath('Desktop')
if ($AppMode) {
    if ($uri.AbsolutePath -ne '/' -or $uri.Fragment) {
        throw 'App mode requires the private OpenCode root address.'
    }
    $chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
    if (-not (Test-Path -LiteralPath $chrome -PathType Leaf)) {
        throw 'The verified Nimo Chrome executable is unavailable; browser shortcut behavior is unchanged.'
    }
    $appTarget = Join-Path $desktop 'Local AI - OpenCode App.lnk'
    $appDescription = 'Managed by EdSys local-coder (OpenCode app)'
    $appArguments = '--app=' + $uri.AbsoluteUri
    $shell = New-Object -ComObject WScript.Shell
    if (Test-Path -LiteralPath $appTarget) {
        if (-not (Test-Path -LiteralPath $appTarget -PathType Leaf)) {
            throw 'An unrelated path already uses the app shortcut name; it was preserved.'
        }
        $existing = $shell.CreateShortcut($appTarget)
        if ($existing.Description -ne $appDescription -or
            $existing.TargetPath -ne $chrome -or $existing.Arguments -ne $appArguments) {
            throw 'An unrelated or modified app shortcut already uses this name; it was preserved.'
        }
        Write-Output 'Local AI - OpenCode App desktop shortcut is already ready.'
        return
    }
    $shortcut = $shell.CreateShortcut($appTarget)
    $shortcut.TargetPath = $chrome
    $shortcut.Arguments = $appArguments
    $shortcut.WorkingDirectory = Split-Path -Parent $chrome
    $shortcut.Description = $appDescription
    $shortcut.IconLocation = $chrome + ',0'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    $readback = $shell.CreateShortcut($appTarget)
    if ($readback.TargetPath -ne $chrome -or $readback.Arguments -ne $appArguments -or
        $readback.Description -ne $appDescription) {
        throw 'The app shortcut was written but its readback did not match the reviewed configuration.'
    }
    Write-Output 'Local AI - OpenCode App desktop shortcut is ready.'
    return
}
$target = Join-Path $desktop 'Local AI - OpenCode.url'
$marker = '; Managed by EdSys local-coder'
if ((Test-Path $target) -and -not (Get-Content -Raw $target).Contains($marker)) {
    throw 'An unrelated shortcut already uses this name; it was preserved.'
}
Set-Content -Encoding Ascii -Path $target -Value "$marker`r`n[InternetShortcut]`r`nURL=$Url`r`n"
Write-Output 'Local AI - OpenCode desktop shortcut is ready.'
