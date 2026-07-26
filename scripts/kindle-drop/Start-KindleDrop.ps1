$ErrorActionPreference = "Stop"

$InstallRoot = "C:\EdSys\KindleDrop"
$ReleasePath = (Get-Content (Join-Path $InstallRoot "current.txt") -Raw).Trim()
$Python = Join-Path $ReleasePath ".venv\Scripts\python.exe"
$Application = Join-Path $ReleasePath "kindle_drop.py"
$Config = Join-Path $InstallRoot "settings.json"

if (-not (Test-Path $Python) -or -not (Test-Path $Application) -or -not (Test-Path $Config)) {
    throw "Kindle Drop release, interpreter, or settings are missing."
}

& $Python $Application run --config $Config
exit $LASTEXITCODE
