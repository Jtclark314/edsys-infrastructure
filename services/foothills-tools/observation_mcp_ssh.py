#!/usr/bin/env python3
"""Run the deployed read-only Observation MCP over the existing Basecamp SSH path.

The production environment and database stay on Basecamp. This launcher creates
no listener, forwards no agent or port, and does not log protocol messages.
"""
from __future__ import annotations

import base64
import os
import sys


REMOTE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = New-Object Text.UTF8Encoding($false)
$root = 'C:\Foothills\ObservationTrackerV3'
$pointer = Join-Path $root 'config\active-release.txt'
$releaseId = (Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()
if ($releaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw 'Invalid active Observation release pointer'
}
$release = Join-Path $root ('releases\' + $releaseId)
$python = Join-Path $release '.venv\Scripts\python.exe'
$entry = Join-Path $release 'integrations\observation_mcp.py'
$environmentFile = Join-Path $root 'config\observation-tracker.env'
foreach ($path in @($python, $entry, $environmentFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'Observation MCP prerequisite is missing'
    }
}
# Import values as data, never PowerShell source, using the app's runtime format.
# No value or environment content is sent back to the hub.
foreach ($raw in Get-Content -LiteralPath $environmentFile -Encoding UTF8) {
    $line = $raw.Trim()
    if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
    $key, $value = $line.Split('=', 2)
    $key = $key.Trim()
    $value = $value.Trim().Trim('"').Trim("'")
    if ($key) { [Environment]::SetEnvironmentVariable($key, $value, 'Process') }
}
$env:OBSERVATION_ENV_FILE = $environmentFile
# A separately issued named-human read grant is optional. The application
# validates it on every call; its value never appears in launcher configuration.
$grantFile = Join-Path $root 'config\codex-jeremy-read-grant.txt'
if (Test-Path -LiteralPath $grantFile -PathType Leaf) {
    $env:OBSERVATION_MCP_GRANT_FILE = $grantFile
}
$env:BUILD_ID = $releaseId
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $release
& $python -u $entry
exit $LASTEXITCODE
""".strip()


def command() -> list[str]:
    encoded = base64.b64encode(REMOTE_SCRIPT.encode('utf-16le')).decode('ascii')
    return [
        '/usr/bin/ssh', '-T',
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ClearAllForwardings=yes',
        '-o', 'ForwardAgent=no',
        '-o', 'ForwardX11=no',
        '-o', 'PermitLocalCommand=no',
        '-o', 'ConnectionAttempts=1',
        '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=15',
        '-o', 'ServerAliveCountMax=2',
        'basecamp',
        'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded,
    ]


def main() -> int:
    if len(sys.argv) != 1:
        print('This stdio launcher accepts no arguments.', file=sys.stderr)
        return 2
    args = command()
    os.execv(args[0], args)
    return 1  # execv replaces this process or raises.


if __name__ == '__main__':
    raise SystemExit(main())
