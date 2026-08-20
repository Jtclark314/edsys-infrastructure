[CmdletBinding()]
param(
    [string]$HubHost = '9950x',
    [string]$HubUploadDirectory = '/home/jeremy/.codex/operator-checkpoints/work-laptop-codex-setup/incoming',
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$TargetCodexVersion = '0.148.0',
    [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'EdSys-Private\work-laptop-codex-setup'),
    [ValidateRange(10, 900)]
    [int]$CommandTimeoutSeconds = 120,
    [switch]$SkipToolPackages,
    [switch]$NoUpload
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$expectedComputer = 'THOMPSON-LC086'
$expectedIdentity = 'THOMPSON\jclark'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($env:COMPUTERNAME -ine $expectedComputer -or $currentIdentity -ine $expectedIdentity) {
    throw "This setup is restricted to $expectedIdentity on $expectedComputer; current endpoint is $currentIdentity on $env:COMPUTERNAME."
}

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this setup from the ordinary desktop session, not from an elevated PowerShell window.'
}

$runId = 'work-laptop-codex-setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$runDirectory = Join-Path $RuntimeRoot $runId
$backupDirectory = Join-Path $runDirectory 'backup'
$reportPath = Join-Path $runDirectory 'sanitized-result.json'
$codexHome = Join-Path $env:USERPROFILE '.codex'
$configPath = Join-Path $codexHome 'config.toml'
$userPathBefore = [Environment]::GetEnvironmentVariable('Path', 'User')
$configExistedBefore = Test-Path -LiteralPath $configPath -PathType Leaf
$managedProfiles = @(
    'fast-iteration.config.toml',
    'safe-docs.config.toml',
    'read-only.config.toml',
    'docs-edit.config.toml',
    'deep-orchestrator.config.toml',
    'max-power.config.toml'
)
$profileExistenceBefore = [ordered]@{}
$coreChanged = $false

function Protect-PrivateDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $systemSid = 'S-1-5-18'

    function Test-ExpectedAcl {
        $acl = Get-Acl -LiteralPath $Path
        if (-not $acl.AreAccessRulesProtected) { return $false }
        $seen = @{}
        foreach ($rule in $acl.Access) {
            try {
                $sid = $rule.IdentityReference.Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            }
            catch { return $false }
            if ($sid -notin @($userSid, $systemSid)) { return $false }
            if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) { return $false }
            if (($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl) {
                return $false
            }
            if ($rule.IsInherited) { return $false }
            $seen[$sid] = $true
        }
        return ($seen.ContainsKey($userSid) -and $seen.ContainsKey($systemSid))
    }

    # A retry after an interrupted setup should not rewrite an already-correct
    # protected ACL. PowerShell 5.1's Set-Acl can request SeSecurityPrivilege
    # when replacing an existing protected descriptor, even when no SACL is
    # being changed.
    if (Test-ExpectedAcl) { return }

    $icacls = Join-Path $env:SystemRoot 'System32\icacls.exe'
    & $icacls $Path '/inheritance:r' '/grant:r' "*$userSid`:(OI)(CI)F" "*$systemSid`:(OI)(CI)F" '/Q' | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-ExpectedAcl)) {
        throw "Could not establish the private user-and-SYSTEM ACL on $Path."
    }
}

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function ConvertTo-PowerShellLiteral {
    param([AllowEmptyString()][string]$Value)

    return "'" + ($Value -replace "'", "''") + "'"
}

function Invoke-BoundedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = $CommandTimeoutSeconds
    )

    $command = Get-Command $FilePath -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return [pscustomobject]@{ available = $false; source = $null; exitCode = $null; timedOut = $false; output = @(); launchError = $null }
    }
    $resolvedSource = [string]$command.Source
    if ([string]::IsNullOrWhiteSpace($resolvedSource) -and $command.PSObject.Properties.Name -contains 'Path') {
        $resolvedSource = [string]$command.Path
    }
    $resolvedExtension = [IO.Path]::GetExtension($resolvedSource)
    $usePowerShellHost = (
        $command.CommandType -ne [System.Management.Automation.CommandTypes]::Application -or
        $resolvedExtension -in @('.ps1', '.psm1', '.cmd', '.bat')
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    if ($usePowerShellHost) {
        $powershell = Get-Command 'powershell.exe' -ErrorAction Stop | Select-Object -First 1
        $invocationParts = @('&', (ConvertTo-PowerShellLiteral -Value $resolvedSource)) + @(
            $Arguments | ForEach-Object { ConvertTo-PowerShellLiteral -Value ([string]$_) }
        )
        $encodedCommand = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes(($invocationParts -join ' '))
        )
        $startInfo.FileName = $powershell.Source
        $startInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + $encodedCommand
    }
    else {
        $startInfo.FileName = $resolvedSource
        $startInfo.Arguments = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument -Value ([string]$_) }) -join ' ')
    }
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try { $null = $process.Start() }
    catch {
        return [pscustomobject]@{
            available = $true
            source = $resolvedSource
            exitCode = $null
            timedOut = $false
            output = @()
            launchError = $_.Exception.Message
        }
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $completed = $process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        try { $process.Kill() } catch {}
        $process.WaitForExit()
    }
    $lines = @(
        (($stdoutTask.Result + [Environment]::NewLine + $stderrTask.Result) -split "`r?`n") |
            ForEach-Object { $_.TrimEnd() } |
            Where-Object { $_ -ne '' }
    )
    return [pscustomobject]@{
        available = $true
        source = $resolvedSource
        exitCode = if ($completed) { $process.ExitCode } else { $null }
        timedOut = (-not $completed)
        output = $lines
        launchError = $null
    }
}

function Get-CommandInventory {
    param([Parameter(Mandatory = $true)][string]$Name)

    return @(
        Get-Command $Name -All -ErrorAction SilentlyContinue |
            ForEach-Object {
                $path = if ($_.PSObject.Properties.Name -contains 'Path') { [string]$_.Path } else { $null }
                [pscustomobject]@{
                    commandType = [string]$_.CommandType
                    source = [string]$_.Source
                    path = $path
                }
            }
    )
}

function Write-AtomicText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.tmp-' + $PID)
    $swapBackup = Join-Path $parent ('.' + [IO.Path]::GetFileName($Path) + '.previous-' + $PID)
    [IO.File]::WriteAllText($temporary, $Text, (New-Object Text.UTF8Encoding($false)))
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        [IO.File]::Replace($temporary, $Path, $swapBackup, $true)
        Remove-Item -LiteralPath $swapBackup -Force -ErrorAction SilentlyContinue
    }
    else {
        [IO.File]::Move($temporary, $Path)
    }
}

function Set-TomlScalar {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [AllowEmptyCollection()]
        [Collections.Generic.List[string]]$Lines,
        [AllowEmptyString()][string]$Section,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Literal
    )

    $start = 0
    $end = $Lines.Count
    if ($Section) {
        $start = -1
        for ($index = 0; $index -lt $Lines.Count; $index++) {
            if ($Lines[$index] -match ('^\s*\[' + [regex]::Escape($Section) + '\]\s*(?:#.*)?$')) {
                $start = $index + 1
                break
            }
        }
        if ($start -lt 0) {
            if ($Lines.Count -gt 0 -and $Lines[$Lines.Count - 1] -ne '') { $Lines.Add('') }
            $Lines.Add("[$Section]")
            $Lines.Add("$Key = $Literal")
            return
        }
        $end = $Lines.Count
        for ($index = $start; $index -lt $Lines.Count; $index++) {
            if ($Lines[$index] -match '^\s*\[') { $end = $index; break }
        }
    }
    else {
        for ($index = 0; $index -lt $Lines.Count; $index++) {
            if ($Lines[$index] -match '^\s*\[') { $end = $index; break }
        }
    }

    for ($index = $start; $index -lt $end; $index++) {
        if ($Lines[$index] -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
            $Lines[$index] = "$Key = $Literal"
            return
        }
    }
    $Lines.Insert($end, "$Key = $Literal")
}

function Update-CodexConfig {
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    $lines = New-Object 'Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $configPath) { $lines.Add([string]$line) }
    }

    foreach ($entry in @(
        @('', 'model', '"gpt-5.6-sol"'),
        @('', 'web_search', '"live"'),
        @('', 'sandbox_mode', '"workspace-write"'),
        @('', 'approval_policy', '"on-request"'),
        @('', 'allow_login_shell', 'true'),
        @('', 'check_for_update_on_startup', 'true'),
        @('features', 'apps', 'true'),
        @('features', 'fast_mode', 'true'),
        @('features', 'goals', 'true'),
        @('features', 'multi_agent', 'true'),
        @('features', 'remote_plugin', 'false'),
        @('features', 'shell_snapshot', 'true'),
        @('agents', 'enabled', 'true'),
        @('agents', 'max_concurrent_threads_per_session', '4'),
        @('agents', 'max_depth', '2'),
        @('windows', 'sandbox', '"elevated"'),
        @('windows', 'sandbox_private_desktop', 'true'),
        @('mcp_servers.openaiDeveloperDocs', 'url', '"https://developers.openai.com/mcp"'),
        @('mcp_servers.openaiDeveloperDocs', 'enabled', 'true')
    )) {
        Set-TomlScalar -Lines $lines -Section $entry[0] -Key $entry[1] -Literal $entry[2]
    }
    Write-AtomicText -Path $configPath -Text (($lines -join "`r`n") + "`r`n")
}

function Write-Profiles {
    $profiles = [ordered]@{
        'fast-iteration.config.toml' = @'
# Fast, low-risk scans and small edits.
model = "gpt-5.6-luna"
model_reasoning_effort = "low"
sandbox_mode = "workspace-write"
approval_policy = "on-request"
'@
        'safe-docs.config.toml' = @'
# Read-only documentation and source-of-truth review.
model = "gpt-5.6-terra"
model_reasoning_effort = "medium"
sandbox_mode = "read-only"
approval_policy = "on-request"
'@
        'read-only.config.toml' = @'
# Routine inspection with no workspace writes.
model = "gpt-5.6-terra"
model_reasoning_effort = "medium"
sandbox_mode = "read-only"
approval_policy = "on-request"
'@
        'docs-edit.config.toml' = @'
# Write-enabled documentation work; use safe-docs for review-only work.
model = "gpt-5.6-terra"
model_reasoning_effort = "medium"
sandbox_mode = "workspace-write"
approval_policy = "on-request"
'@
        'deep-orchestrator.config.toml' = @'
# Deliberate multi-agent profile for the hardest bounded interactive work.
model = "gpt-5.6-sol"
model_reasoning_effort = "ultra"
sandbox_mode = "workspace-write"
approval_policy = "on-request"

[agents]
enabled = true
max_concurrent_threads_per_session = 4
max_depth = 2
'@
        'max-power.config.toml' = @'
# Explicit interactive administration only; never an unattended default.
model = "gpt-5.6-sol"
model_reasoning_effort = "max"
sandbox_mode = "danger-full-access"
approval_policy = "on-request"
'@
    }
    foreach ($name in $profiles.Keys) {
        Write-AtomicText -Path (Join-Path $codexHome $name) -Text ($profiles[$name].Trim() + "`r`n")
    }
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ';'
}

function Prepend-UserPath {
    param([Parameter(Mandatory = $true)][string]$Entry)

    $existing = [Environment]::GetEnvironmentVariable('Path', 'User')
    $segments = @($Entry)
    if (-not [string]::IsNullOrWhiteSpace($existing)) {
        $segments += @(
            $existing.Split(';', [StringSplitOptions]::RemoveEmptyEntries) |
                Where-Object { $_.TrimEnd('\') -ine $Entry.TrimEnd('\') }
        )
    }
    [Environment]::SetEnvironmentVariable('Path', ($segments -join ';'), 'User')
    Refresh-ProcessPath
}

function Install-OrUpgradeWingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [ValidateSet('install', 'upgrade')][string]$Mode = 'install',
        [switch]$UserScope
    )

    $arguments = @(
        $Mode, '--id', $Id, '--exact', '--source', 'winget', '--silent',
        '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'
    )
    if ($UserScope) { $arguments += @('--scope', 'user') }
    $result = Invoke-BoundedCommand -FilePath 'winget' -Arguments $arguments -TimeoutSeconds 360
    $text = $result.output -join "`n"
    $success = (
        $result.exitCode -eq 0 -or
        $text -match '(?i)No available upgrade found|No newer package versions|already installed'
    )
    return [pscustomobject]@{
        id = $Id
        mode = $Mode
        userScope = [bool]$UserScope
        success = $success
        exitCode = $result.exitCode
        timedOut = $result.timedOut
        summary = @($result.output | Select-Object -Last 12)
    }
}

function Upload-Result {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($NoUpload) { return 'skipped' }
    $remotePath = "$HubHost`:$HubUploadDirectory/$runId.json"
    $scp = Invoke-BoundedCommand -FilePath 'scp' -Arguments @(
        '-q', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
        '-o', 'PasswordAuthentication=no', '-o', 'ConnectTimeout=10', $Path, $remotePath
    ) -TimeoutSeconds 45
    if ($scp.exitCode -ne 0) { throw "Sanitized result upload failed: $($scp.output -join '; ')" }
    return 'uploaded'
}

Protect-PrivateDirectory -Path $RuntimeRoot
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null

$sshPreflight = Invoke-BoundedCommand -FilePath 'ssh' -Arguments @(
    '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=publickey',
    '-o', 'PasswordAuthentication=no', '-o', 'ConnectTimeout=10',
    $HubHost, 'hostname; id -un; codex --version'
) -TimeoutSeconds 30
if ($sshPreflight.exitCode -ne 0 -or $sshPreflight.output.Count -lt 3 -or $sshPreflight.output[0] -ne '9950x' -or $sshPreflight.output[1] -ne 'jeremy') {
    throw 'The key-only controller-to-9950x preflight failed; no local change was made.'
}

$codexCommandsBefore = Get-CommandInventory -Name 'codex'
$codexBefore = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('--version') -TimeoutSeconds 15
$appxBefore = @(
    Get-AppxPackage |
        Where-Object { $_.Name -in @('OpenAI.ChatGPT-Desktop', 'OpenAI.Codex') } |
        Select-Object Name, Version, PackageFamilyName, Status
)

if ($configExistedBefore) {
    Copy-Item -LiteralPath $configPath -Destination (Join-Path $backupDirectory 'config.toml')
}
foreach ($name in $managedProfiles) {
    $path = Join-Path $codexHome $name
    $profileExistenceBefore[$name] = Test-Path -LiteralPath $path -PathType Leaf
    if ($profileExistenceBefore[$name]) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $backupDirectory $name)
    }
}
[IO.File]::WriteAllText(
    (Join-Path $backupDirectory 'user-path.txt'),
    [string]$userPathBefore,
    (New-Object Text.UTF8Encoding($false))
)

try {
    Write-Host "Installing the exact official Codex standalone release $TargetCodexVersion..."
    $installerPath = Join-Path $runDirectory 'official-codex-install.ps1'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://chatgpt.com/codex/install.ps1' -OutFile $installerPath -TimeoutSec 60
    $installerText = Get-Content -LiteralPath $installerPath -Raw
    foreach ($required in @('https://releases.openai.com/codex', 'Test-ArchiveDigest', 'Get-PackageArchiveDigest')) {
        if ($installerText -notmatch [regex]::Escape($required)) {
            throw "The downloaded official installer is missing required integrity marker: $required"
        }
    }
    $installerSha256 = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $oldNonInteractive = $env:CODEX_NON_INTERACTIVE
    $env:CODEX_NON_INTERACTIVE = '1'
    try {
        $install = Invoke-BoundedCommand -FilePath 'powershell.exe' -Arguments @(
            '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', $installerPath, '-Release', $TargetCodexVersion
        ) -TimeoutSeconds 600
    }
    finally { $env:CODEX_NON_INTERACTIVE = $oldNonInteractive }
    if ($install.exitCode -ne 0 -or $install.timedOut) {
        throw "The official Codex installer failed: $($install.output -join '; ')"
    }

    $standaloneBinary = Join-Path $codexHome 'packages\standalone\current\bin\codex.exe'
    if (-not (Test-Path -LiteralPath $standaloneBinary -PathType Leaf)) {
        throw "The official standalone binary is missing after installation: $standaloneBinary"
    }
    $standaloneVersion = (& $standaloneBinary --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $standaloneVersion -ne "codex-cli $TargetCodexVersion") {
        throw "The installed standalone binary failed exact version verification: $standaloneVersion"
    }

    Prepend-UserPath -Entry (Join-Path $env:USERPROFILE '.local\bin')
    Update-CodexConfig
    Write-Profiles
    $coreChanged = $true

    $activeCodex = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('--version') -TimeoutSeconds 15
    if (($activeCodex.output | Select-Object -First 1) -ne "codex-cli $TargetCodexVersion") {
        throw "The active command path did not resolve to Codex $TargetCodexVersion after activation."
    }
}
catch {
    $failure = $_
    if ($configExistedBefore -and (Test-Path -LiteralPath (Join-Path $backupDirectory 'config.toml'))) {
        Copy-Item -LiteralPath (Join-Path $backupDirectory 'config.toml') -Destination $configPath -Force
    }
    elseif (-not $configExistedBefore -and (Test-Path -LiteralPath $configPath)) {
        Remove-Item -LiteralPath $configPath -Force
    }
    foreach ($name in $managedProfiles) {
        $path = Join-Path $codexHome $name
        $backup = Join-Path $backupDirectory $name
        if ($profileExistenceBefore[$name] -and (Test-Path -LiteralPath $backup)) {
            Copy-Item -LiteralPath $backup -Destination $path -Force
        }
        elseif (-not $profileExistenceBefore[$name] -and (Test-Path -LiteralPath $path)) {
            Remove-Item -LiteralPath $path -Force
        }
    }
    [Environment]::SetEnvironmentVariable('Path', $userPathBefore, 'User')
    Refresh-ProcessPath
    throw $failure
}

$packageResults = @()
if (-not $SkipToolPackages) {
    Write-Host 'Installing or updating the narrow Windows-local Codex support toolset...'
    $packageResults += Install-OrUpgradeWingetPackage -Id 'GitHub.cli' -UserScope
    $packageResults += Install-OrUpgradeWingetPackage -Id 'Microsoft.PowerShell' -UserScope
    $packageResults += Install-OrUpgradeWingetPackage -Id 'BurntSushi.ripgrep.MSVC' -UserScope
    $packageResults += Install-OrUpgradeWingetPackage -Id 'jqlang.jq' -UserScope
    $packageResults += Install-OrUpgradeWingetPackage -Id 'astral-sh.uv' -UserScope
    $packageResults += Install-OrUpgradeWingetPackage -Id 'Microsoft.VisualStudioCode' -Mode 'upgrade' -UserScope
}
Refresh-ProcessPath

$codexAfter = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('--version') -TimeoutSeconds 15
$loginAfter = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('login', 'status') -TimeoutSeconds 25
$doctorAfter = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('doctor', '--summary', '--ascii') -TimeoutSeconds 60
$daemonAfter = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('app-server', 'daemon', 'version') -TimeoutSeconds 30
$toolsAfter = foreach ($name in @('git', 'gh', 'node', 'npm.cmd', 'python', 'pwsh', 'rg', 'jq', 'uv', 'code', 'winget')) {
    $arguments = switch ($name) {
        'git' { @('--version') }
        'gh' { @('--version') }
        'npm.cmd' { @('--version') }
        'python' { @('--version') }
        'pwsh' { @('-NoLogo', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()') }
        default { @('--version') }
    }
    $result = Invoke-BoundedCommand -FilePath $name -Arguments $arguments -TimeoutSeconds 20
    [pscustomobject]@{
        name = $name
        available = $result.available
        version = if ($result.output.Count -gt 0) { $result.output[0] } else { $null }
        exitCode = $result.exitCode
    }
}

$githubAuth = Invoke-BoundedCommand -FilePath 'gh' -Arguments @('api', 'user', '--jq', '.login') -TimeoutSeconds 20
$resultReport = [ordered]@{
    schemaVersion = 1
    runId = $runId
    generatedAt = (Get-Date).ToString('o')
    status = 'applied-restart-required'
    safety = 'Sanitized setup result; private backups and installer output remain outside Git and RAG.'
    endpoint = [ordered]@{
        computer = $env:COMPUTERNAME
        identity = $currentIdentity
        elevated = $false
    }
    before = [ordered]@{
        codexVersion = if ($codexBefore.output.Count -gt 0) { $codexBefore.output[0] } else { $null }
        codexCommands = $codexCommandsBefore
        appx = $appxBefore
        userPathSha256 = (Get-FileHash -LiteralPath (Join-Path $backupDirectory 'user-path.txt') -Algorithm SHA256).Hash.ToLowerInvariant()
        configExisted = $configExistedBefore
    }
    applied = [ordered]@{
        targetCodexVersion = $TargetCodexVersion
        officialInstallerSha256 = $installerSha256
        coreChanged = $coreChanged
        defaultSandbox = 'workspace-write'
        defaultApproval = 'on-request'
        windowsSandbox = 'elevated'
        model = 'gpt-5.6-sol'
        webSearch = 'live'
        openaiDeveloperDocs = 'enabled'
        profiles = $managedProfiles
        packageResults = $packageResults
    }
    after = [ordered]@{
        codexVersion = if ($codexAfter.output.Count -gt 0) { $codexAfter.output[0] } else { $null }
        codexCommands = Get-CommandInventory -Name 'codex'
        loginStatus = @($loginAfter.output | Where-Object { $_ -match '(?i)logged in|chatgpt|api key|not logged in' } | Select-Object -First 5)
        doctorExitCode = $doctorAfter.exitCode
        doctorSummary = @($doctorAfter.output | Where-Object { $_ -match '^(Codex Doctor|\s*\[(ok|!!|XX)\]|\d+ ok \|)' } | Select-Object -First 80)
        daemonVersion = @($daemonAfter.output | Select-Object -First 10)
        tools = $toolsAfter
        githubCliAuthenticated = ($githubAuth.exitCode -eq 0 -and $githubAuth.output.Count -gt 0)
        githubCliAccount = if ($githubAuth.exitCode -eq 0 -and $githubAuth.output.Count -gt 0) { $githubAuth.output[0] } else { $null }
    }
    followUp = @(
        'Fully restart the ChatGPT/Codex app so the active Windows app server re-reads the new standalone release and config.',
        'Authenticate GitHub CLI interactively if githubCliAuthenticated is false.',
        'Reboot Windows after saving work because the preflight reported a pending reboot.',
        'Use employer-managed Windows servicing for the still-unsupported Windows 11 Pro 23H2 release.'
    )
}
$resultReport | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$uploadStatus = Upload-Result -Path $reportPath

Write-Host ''
Write-Host "PASS: Codex $TargetCodexVersion and the reviewed work-laptop configuration were applied."
Write-Host "Private result: $reportPath"
Write-Host "Hub upload: $uploadStatus"
if (@($packageResults | Where-Object { -not $_.success }).Count -gt 0) {
    Write-Warning 'One or more optional support-tool packages could not install without elevation; the result report identifies them.'
}
if (-not ($githubAuth.exitCode -eq 0 -and $githubAuth.output.Count -gt 0)) {
    Write-Warning 'GitHub CLI still needs an interactive gh auth login after app restart.'
}
Write-Warning 'A full ChatGPT/Codex app restart is required for final live acceptance. Windows also reports a pending reboot.'
