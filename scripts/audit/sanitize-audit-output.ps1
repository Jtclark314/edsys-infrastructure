[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $InputPath)) {
    throw "InputPath does not exist: $InputPath"
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $InputPath "sanitized-review"
}

New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null

$redactions = @(
    @{ Pattern = '(?i)(password\s*[:=]\s*)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '(?i)(passwd\s*[:=]\s*)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '(?i)(token\s*[:=]\s*)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '(?i)(api[_-]?key\s*[:=]\s*)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '(?i)(secret\s*[:=]\s*)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '(?i)(authorization:\s*bearer\s+)\S+'; Replacement = '$1<REDACTED>' },
    @{ Pattern = '-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----'; Replacement = '<REDACTED PRIVATE KEY BLOCK>' }
)

$skipPatterns = @(
    '\.env$',
    '\.sqlite$',
    '\.sqlite3$',
    '\.db$',
    '\.log$'
)

Get-ChildItem -LiteralPath $InputPath -File -Recurse | ForEach-Object {
    $relative = Resolve-Path -LiteralPath $_.FullName -Relative
    foreach ($skip in $skipPatterns) {
        if ($_.FullName -match $skip) {
            return
        }
    }

    $target = Join-Path $OutputPath $_.Name
    $text = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
    foreach ($rule in $redactions) {
        $text = [regex]::Replace($text, $rule.Pattern, $rule.Replacement)
    }
    Set-Content -Encoding UTF8 -Path $target -Value $text
}

Write-Output "Sanitized review files written to: $OutputPath"
Write-Output "Manual review is still required before committing."
