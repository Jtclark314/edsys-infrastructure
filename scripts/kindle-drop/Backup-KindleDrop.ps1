$ErrorActionPreference = "Stop"

$InstallRoot = "C:\EdSys\KindleDrop"
$ReleasePath = (Get-Content (Join-Path $InstallRoot "current.txt") -Raw).Trim()
$Python = Join-Path $ReleasePath ".venv\Scripts\python.exe"
$Application = Join-Path $ReleasePath "kindle_drop.py"
$Config = Join-Path $InstallRoot "settings.json"
$Output = Join-Path $InstallRoot "backups"

& $Python $Application backup --config $Config --output-root $Output
if ($LASTEXITCODE -ne 0) {
    throw "Kindle Drop snapshot failed with exit code $LASTEXITCODE."
}

$Daily = Get-ChildItem $Output -Filter "kindle-drop-daily-*.zip" |
    Sort-Object LastWriteTimeUtc -Descending
$Daily | Select-Object -Skip 35 | ForEach-Object {
    Remove-Item -Force $_.FullName
    Remove-Item -Force ($_.FullName + ".sha256") -ErrorAction SilentlyContinue
}

$ExistingMonthly = Get-ChildItem $Output -Filter "kindle-drop-monthly-*.zip"
if (((Get-Date).Day -eq 1 -or $ExistingMonthly.Count -eq 0) -and $Daily.Count -gt 0) {
    $MonthlyName = $Daily[0].Name -replace "kindle-drop-daily-", "kindle-drop-monthly-"
    $MonthlyPath = Join-Path $Output $MonthlyName
    if (-not (Test-Path $MonthlyPath)) {
        Copy-Item -Force $Daily[0].FullName $MonthlyPath
        $MonthlyHash = (Get-FileHash $MonthlyPath -Algorithm SHA256).Hash.ToLower()
        Set-Content -Encoding ascii -Path ($MonthlyPath + ".sha256") `
            -Value ($MonthlyHash + "  " + $MonthlyName)
    }
}

$Monthly = Get-ChildItem $Output -Filter "kindle-drop-monthly-*.zip" |
    Sort-Object LastWriteTimeUtc -Descending
$Monthly | Select-Object -Skip 18 | ForEach-Object {
    Remove-Item -Force $_.FullName
    Remove-Item -Force ($_.FullName + ".sha256") -ErrorAction SilentlyContinue
}
