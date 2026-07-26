param(
    [string]$TaskName = "EdSys Kindle Drop",
    [string]$HealthUrl = "http://100.120.155.81:8094/healthz",
    [string]$InstallRoot = "C:\EdSys\KindleDrop"
)

$ErrorActionPreference = "Continue"
$LogRoot = "C:\ProgramData\EdSys\KindleDrop\logs"
$LogPath = Join-Path $LogRoot "health-repair.log"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

function Test-Dispatcher {
    try {
        $response = Invoke-WebRequest $HealthUrl -UseBasicParsing -TimeoutSec 15
        return ($response.StatusCode -eq 200)
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 503) {
            # Commissioning/degraded proves the expected process is listening.
            return $true
        }
        return $false
    }
}

if (Test-Dispatcher) {
    exit 0
}

Add-Content $LogPath ((Get-Date).ToUniversalTime().ToString("s") + "Z repairing dispatcher: health listener unavailable")
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like ("*" + $InstallRoot + "*kindle_drop.py*") } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$Deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Seconds 3
    if (Test-Dispatcher) {
        exit 0
    }
} while ((Get-Date) -lt $Deadline)

Add-Content $LogPath ((Get-Date).ToUniversalTime().ToString("s") + "Z repair failed")
exit 1
