[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Discover','ResolveCandidate','Preflight','Checkpoint','Apply','RestartOrReboot','Verify','Accept','Rollback','Cleanup')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$RunId,

    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$CandidateVersion = '24.19.0',

    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$RollbackVersion = '24.15.0',

    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$ExpectedNpmVersion = '12.0.2',

    [string]$RuntimeRoot = (Join-Path $env:LOCALAPPDATA 'EdSys-Private\Fleet\node-toolchain'),
    [string]$HubSshAlias = '9950x',
    [switch]$QualificationRehearsal
)

$ErrorActionPreference = 'Stop'
$PackageId = 'OpenJS.NodeJS.LTS'
$RunRoot = Join-Path $RuntimeRoot $RunId
$StatePath = Join-Path $RunRoot 'state.json'
$CheckpointRoot = Join-Path $RunRoot 'checkpoint'
$InstallerRoot = Join-Path $RunRoot 'installers'

function Protect-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $none = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inheritance, $none, $allow)))
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system, 'FullControl', $inheritance, $none, $allow)))
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-PrivateJson {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][object]$Value)
    Protect-Directory -Path (Split-Path -Parent $Path)
    $temporary = "$Path.tmp-$PID"
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-State {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { throw "Missing transaction state: $StatePath" }
    return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
}

function Save-State {
    param([Parameter(Mandatory = $true)][object]$State)
    $State.updatedAt = (Get-Date).ToString('o')
    Write-PrivateJson -Path $StatePath -Value $State
}

function Get-NodeVersion {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $command) { return $null }
    return ((& $command.Source --version 2>$null) -replace '^v','').Trim()
}

function Get-NpmVersion {
    $command = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $command) { return $null }
    return ((& $command.Source --version 2>$null) | Select-Object -First 1).Trim()
}

function Get-NodeProduct {
    $roots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    return Get-ItemProperty -Path $roots -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'Node.js' } |
        Sort-Object DisplayVersion -Descending |
        Select-Object -First 1 DisplayName, DisplayVersion, PSChildName, Publisher, InstallLocation
}

function Get-SafeGlobalManifest {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { return @() }
    $json = (& $npm.Source ls -g --depth=0 --json 2>$null) -join "`n"
    if (-not $json) { return @() }
    $value = $json | ConvertFrom-Json
    $items = @()
    foreach ($property in @($value.dependencies.PSObject.Properties)) {
        if ($property.Name -in @('npm','corepack')) { continue }
        $items += [pscustomobject]@{ name = $property.Name; version = [string]$property.Value.version }
    }
    return @($items | Sort-Object name)
}

function Get-PathSnapshot {
    return [pscustomobject]@{
        user = [Environment]::GetEnvironmentVariable('Path', 'User')
        machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    }
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Get-PendingReboot {
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
    )
    if ($keys | Where-Object { Test-Path -LiteralPath $_ }) { return $true }
    $session = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
    return [bool]$session.PendingFileRenameOperations
}

function Get-PowerEvidence {
    $battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $battery) { return [pscustomobject]@{ acPower = $true; batteryPercent = $null; batteryPresent = $false } }
    $onAC = [int]$battery.BatteryStatus -in @(2,3,6,7,8,9,11)
    return [pscustomobject]@{ acPower = $onAC; batteryPercent = [int]$battery.EstimatedChargeRemaining; batteryPresent = $true }
}

function Assert-Installer {
    param([Parameter(Mandatory = $true)][object]$Installer)
    if (-not (Test-Path -LiteralPath ([string]$Installer.path) -PathType Leaf)) { throw "Missing cached installer: $($Installer.path)" }
    $hash = (Get-FileHash -LiteralPath ([string]$Installer.path) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -cne [string]$Installer.sha256) { throw "Installer hash changed: $($Installer.path)" }
    $signature = Get-AuthenticodeSignature -LiteralPath ([string]$Installer.path)
    if ($signature.Status -ne 'Valid') { throw "Installer signature is not valid: $($signature.Status)" }
    return [pscustomobject]@{ path = [string]$Installer.path; sha256 = $hash; signer = [string]$signature.SignerCertificate.Subject }
}

function Download-Installer {
    param([Parameter(Mandatory = $true)][string]$Version)
    $destination = Join-Path $InstallerRoot $Version
    Protect-Directory -Path $destination
    $existing = Get-ChildItem -LiteralPath $destination -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @('.msi','.exe') } | Select-Object -First 1
    if (-not $existing) {
        $winget = Get-Command winget.exe -ErrorAction Stop
        & $winget.Source download --id $PackageId --exact --version $Version --architecture x64 `
            --download-directory $destination --accept-package-agreements --accept-source-agreements `
            --disable-interactivity | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "winget download failed for $PackageId $Version" }
        $existing = Get-ChildItem -LiteralPath $destination -File -Recurse |
            Where-Object { $_.Extension -in @('.msi','.exe') } | Select-Object -First 1
    }
    if (-not $existing) { throw "No installer was downloaded for $PackageId $Version" }
    $signature = Get-AuthenticodeSignature -LiteralPath $existing.FullName
    if ($signature.Status -ne 'Valid') { throw "Downloaded installer signature is not valid: $($signature.Status)" }
    return [pscustomobject]@{
        version = $Version
        path = $existing.FullName
        sha256 = (Get-FileHash -LiteralPath $existing.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        signer = [string]$signature.SignerCertificate.Subject
    }
}

function Invoke-Installer {
    param([Parameter(Mandatory = $true)][object]$Installer)
    Assert-Installer -Installer $Installer | Out-Null
    $path = [string]$Installer.path
    if ([IO.Path]::GetExtension($path) -ieq '.msi') {
        $process = Start-Process msiexec.exe -ArgumentList @('/i', "`"$path`"", '/qn', '/norestart') -Wait -PassThru
    }
    else {
        $process = Start-Process $path -ArgumentList @('/S') -Wait -PassThru
    }
    if ($process.ExitCode -notin @(0,1641,3010)) { throw "Node installer returned exit code $($process.ExitCode)" }
    Refresh-ProcessPath
    return $process.ExitCode
}

function Remove-CurrentNodeProduct {
    $product = Get-NodeProduct
    if (-not $product) { return }
    if (-not [string]$product.PSChildName -or [string]$product.PSChildName -notmatch '^\{[0-9A-Fa-f-]+\}$') {
        throw 'Cannot identify the installed Node.js MSI product code for controlled rollback.'
    }
    $process = Start-Process msiexec.exe -ArgumentList @('/x', [string]$product.PSChildName, '/qn', '/norestart') -Wait -PassThru
    if ($process.ExitCode -notin @(0,1605,1641,3010)) { throw "Node uninstall returned exit code $($process.ExitCode)" }
}

function Restore-GlobalPackages {
    param([object[]]$Manifest)
    $current = @{}
    foreach ($item in @(Get-SafeGlobalManifest)) { $current[[string]$item.name] = [string]$item.version }
    foreach ($item in @($Manifest)) {
        if ($current[[string]$item.name] -ceq [string]$item.version) { continue }
        & npm.cmd install --global "$($item.name)@$($item.version)" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to restore global package $($item.name)@$($item.version)" }
    }
}

function Assert-Version {
    param([Parameter(Mandatory = $true)][string]$Node, [string]$Npm)
    Refresh-ProcessPath
    $actualNode = Get-NodeVersion
    $actualNpm = Get-NpmVersion
    if ($actualNode -cne $Node) { throw "Node mismatch: expected $Node, found $actualNode" }
    if ($Npm -and $actualNpm -cne $Npm) { throw "npm mismatch: expected $Npm, found $actualNpm" }
    & node.exe -e "process.stdout.write(process.versions.node)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Node execution smoke failed.' }
    return [pscustomobject]@{ node = $actualNode; npm = $actualNpm }
}

function Invoke-AcceptanceSubset {
    $controls = [ordered]@{}

    $doctor = & codex.exe doctor --summary --ascii 2>&1
    $controls.codexDoctor = [pscustomobject]@{ passed = ($LASTEXITCODE -eq 0); lines = @($doctor).Count }
    if (-not $controls.codexDoctor.passed) { throw 'Codex Doctor failed after the Node toolchain transaction.' }

    $mcpRaw = (& codex.exe mcp list --json 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0 -or -not $mcpRaw) { throw 'Codex MCP inventory failed after the Node toolchain transaction.' }
    $mcp = @($mcpRaw | ConvertFrom-Json)
    $controls.mcpInventory = [pscustomobject]@{ passed = $true; entries = $mcp.Count; enabled = @($mcp | Where-Object { $_.enabled }).Count }

    $chrome = Get-Command chrome.exe -ErrorAction SilentlyContinue
    if (-not $chrome) { $chrome = Get-Command 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ErrorAction SilentlyContinue }
    if (-not $chrome) { throw 'Chrome is unavailable for the Node toolchain browser smoke.' }
    $playwrightRoot = (& npm.cmd root --global 2>$null | Select-Object -First 1).Trim()
    $playwrightScript = Join-Path $RunRoot 'playwright-smoke.cjs'
    @"
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  const page = await browser.newPage();
  await page.goto('https://example.com', {waitUntil: 'domcontentloaded', timeout: 30000});
  if (!(await page.title()).includes('Example')) throw new Error('browser canary title mismatch');
  await browser.close();
  process.stdout.write('EDSYS_PLAYWRIGHT_OK');
})().catch((error) => { console.error(error.message); process.exit(1); });
"@ | Set-Content -LiteralPath $playwrightScript -Encoding UTF8
    $priorNodePath = $env:NODE_PATH
    $env:NODE_PATH = $playwrightRoot
    try { $browserSmoke = & node.exe $playwrightScript 2>&1; $browserExit = $LASTEXITCODE }
    finally { $env:NODE_PATH = $priorNodePath; Remove-Item -LiteralPath $playwrightScript -Force -ErrorAction SilentlyContinue }
    $controls.playwrightChrome = [pscustomobject]@{ passed = ($browserExit -eq 0 -and (@($browserSmoke) -join "`n") -match 'EDSYS_PLAYWRIGHT_OK') }
    if (-not $controls.playwrightChrome.passed) { throw 'Playwright/Chrome smoke failed after the Node toolchain transaction.' }

    $docker = & docker.exe version --format '{{.Server.Version}}' 2>$null
    $controls.docker = [pscustomobject]@{ passed = ($LASTEXITCODE -eq 0 -and [bool]$docker); version = (@($docker) | Select-Object -First 1) }
    if (-not $controls.docker.passed) { throw 'Docker smoke failed after the Node toolchain transaction.' }

    $remote = & ssh.exe -o BatchMode=yes -o ConnectTimeout=10 $HubSshAlias "test -d /home/jeremy/code/EdSys-Master && printf EDSYS_REMOTE_PROJECT_OK" 2>$null
    $controls.remoteProject = [pscustomobject]@{ passed = ($LASTEXITCODE -eq 0 -and (@($remote) -join '') -eq 'EDSYS_REMOTE_PROJECT_OK'); target = $HubSshAlias }
    if (-not $controls.remoteProject.passed) { throw 'Nimo-to-9950x remote project acceptance failed.' }

    return [pscustomobject]$controls
}

Protect-Directory -Path $RuntimeRoot

switch ($Action) {
    'Discover' {
        $product = Get-NodeProduct
        [pscustomobject]@{
            status = 'passed'; phase = 'discover'; packageId = $PackageId
            node = Get-NodeVersion; npm = Get-NpmVersion; product = $product
            winget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
        } | ConvertTo-Json -Depth 8
    }
    'ResolveCandidate' {
        Protect-Directory -Path $RunRoot
        if (Test-Path -LiteralPath $StatePath) {
            $state = Read-State
            Assert-Installer -Installer $state.candidateInstaller | Out-Null
            Assert-Installer -Installer $state.rollbackInstaller | Out-Null
        }
        else {
            $candidate = Download-Installer -Version $CandidateVersion
            $rollback = Download-Installer -Version $RollbackVersion
            $state = [pscustomobject]@{
                schemaVersion = 1; runId = $RunId; phase = 'candidate-resolved'; packageId = $PackageId
                candidateVersion = $CandidateVersion; rollbackVersion = $RollbackVersion
                expectedNpmVersion = $ExpectedNpmVersion; qualificationRehearsal = [bool]$QualificationRehearsal
                candidateInstaller = $candidate; rollbackInstaller = $rollback
                preflight = $null; checkpoint = $null; applyExitCode = $null
                appliedAt = $null; processRefreshAt = $null; lastVerifiedAt = $null
                rolledBackAt = $null; acceptedAt = $null; cleanedAt = $null
                createdAt = (Get-Date).ToString('o'); updatedAt = (Get-Date).ToString('o')
            }
            Save-State -State $state
        }
        [pscustomobject]@{ status = 'passed'; phase = 'resolve_candidate'; candidate = $state.candidateInstaller; rollback = $state.rollbackInstaller } | ConvertTo-Json -Depth 8
    }
    'Preflight' {
        $state = Read-State
        Assert-Installer -Installer $state.candidateInstaller | Out-Null
        Assert-Installer -Installer $state.rollbackInstaller | Out-Null
        $power = Get-PowerEvidence
        if (-not $power.acPower) { throw 'Nimo must be on AC power before the Node toolchain transaction.' }
        if ($power.batteryPresent -and $power.batteryPercent -lt 40) { throw 'Nimo battery must be at least 40 percent.' }
        if (Get-PendingReboot) { throw 'A Windows reboot is already pending.' }
        $drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($RuntimeRoot).Substring(0,1))
        if ($drive.Free -lt 2GB) { throw 'At least 2 GiB free disk space is required.' }
        $state.phase = 'preflight-passed'; $state.preflight = [pscustomobject]@{ power = $power; freeBytes = [int64]$drive.Free; pendingReboot = $false }
        Save-State -State $state
        [pscustomobject]@{ status = 'passed'; phase = 'preflight'; evidence = $state.preflight } | ConvertTo-Json -Depth 8
    }
    'Checkpoint' {
        $state = Read-State
        if ($state.phase -notin @('preflight-passed','checkpointed')) { throw "Checkpoint is invalid from phase $($state.phase)" }
        Protect-Directory -Path $CheckpointRoot
        $nodeCommand = Get-Command node.exe -ErrorAction Stop
        $npmCommand = Get-Command npm.cmd -ErrorAction Stop
        $global = @(Get-SafeGlobalManifest)
        $checkpoint = [pscustomobject]@{
            node = Get-NodeVersion; npm = Get-NpmVersion
            nodePath = $nodeCommand.Source; nodeSha256 = (Get-FileHash $nodeCommand.Source -Algorithm SHA256).Hash.ToLowerInvariant()
            npmPath = $npmCommand.Source; npmSha256 = (Get-FileHash $npmCommand.Source -Algorithm SHA256).Hash.ToLowerInvariant()
            product = Get-NodeProduct; path = Get-PathSnapshot; globalPackages = $global
            npmConfig = [pscustomobject]@{ prefix = (& npm.cmd config get prefix); registry = (& npm.cmd config get registry) }
            npmrcSha256 = if (Test-Path "$env:USERPROFILE\.npmrc") { (Get-FileHash "$env:USERPROFILE\.npmrc" -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
            nodeProcesses = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue | Select-Object ProcessId, Name, ExecutablePath)
            capturedAt = (Get-Date).ToString('o')
        }
        Write-PrivateJson -Path (Join-Path $CheckpointRoot 'manifest.json') -Value $checkpoint
        $state.checkpoint = $checkpoint; $state.phase = 'checkpointed'
        Save-State -State $state
        $checkpointPath = Join-Path $CheckpointRoot 'manifest.json'
        [pscustomobject]@{ status = 'passed'; phase = 'checkpoint'; version = $checkpoint.node; checkpointPath = $checkpointPath; checkpointSha256 = (Get-FileHash $checkpointPath -Algorithm SHA256).Hash.ToLowerInvariant(); globalPackageCount = $global.Count } | ConvertTo-Json -Depth 8
    }
    'Apply' {
        $state = Read-State
        if ($state.phase -notin @('checkpointed','rolled-back','applied')) { throw "Apply is invalid from phase $($state.phase)" }
        if ($state.phase -ne 'applied') {
            $exitCode = Invoke-Installer -Installer $state.candidateInstaller
            Assert-Version -Node ([string]$state.candidateVersion) -Npm ([string]$state.expectedNpmVersion) | Out-Null
            Restore-GlobalPackages -Manifest @($state.checkpoint.globalPackages)
            $state.applyExitCode = $exitCode; $state.phase = 'applied'; $state.appliedAt = (Get-Date).ToString('o')
            Save-State -State $state
        }
        [pscustomobject]@{ status = 'passed'; phase = 'apply'; node = Get-NodeVersion; npm = Get-NpmVersion } | ConvertTo-Json -Depth 8
    }
    'RestartOrReboot' {
        $state = Read-State
        if ($state.phase -notin @('applied','processes-refreshed')) { throw "Process refresh is invalid from phase $($state.phase)" }
        Refresh-ProcessPath
        $state.phase = 'processes-refreshed'; $state.processRefreshAt = (Get-Date).ToString('o')
        Save-State -State $state
        [pscustomobject]@{ status = 'passed'; phase = 'restart_or_reboot'; rebootRequired = $false; note = 'New processes inherit the refreshed machine and user PATH.' } | ConvertTo-Json
    }
    'Verify' {
        $state = Read-State
        $expectedNode = if ($state.phase -eq 'rolled-back') { [string]$state.checkpoint.node } else { [string]$state.candidateVersion }
        $expectedNpm = if ($state.phase -eq 'rolled-back') { [string]$state.checkpoint.npm } else { [string]$state.expectedNpmVersion }
        $versions = Assert-Version -Node $expectedNode -Npm $expectedNpm
        $expected = @{}; foreach ($item in @($state.checkpoint.globalPackages)) { $expected[[string]$item.name] = [string]$item.version }
        $actual = @{}; foreach ($item in @(Get-SafeGlobalManifest)) { $actual[[string]$item.name] = [string]$item.version }
        $missing = @($expected.Keys | Where-Object { $actual[$_] -cne $expected[$_] })
        if ($missing.Count) { throw "Global package verification failed: $($missing -join ', ')" }
        $acceptance = Invoke-AcceptanceSubset
        $state.lastVerifiedAt = (Get-Date).ToString('o'); Save-State -State $state
        [pscustomobject]@{ status = 'passed'; phase = 'verify'; node = $versions.node; npm = $versions.npm; globalPackageCount = $actual.Count; globalPackagesMatched = $true; acceptance = $acceptance } | ConvertTo-Json -Depth 8
    }
    'Rollback' {
        $state = Read-State
        if ($state.phase -in @('accepted','cleaned')) { throw "Rollback is invalid from terminal phase $($state.phase)" }
        if ($state.phase -ne 'rolled-back') {
            Assert-Installer -Installer $state.rollbackInstaller | Out-Null
            Remove-CurrentNodeProduct
            Invoke-Installer -Installer $state.rollbackInstaller | Out-Null
            Assert-Version -Node ([string]$state.checkpoint.node) -Npm ([string]$state.checkpoint.npm) | Out-Null
            Restore-GlobalPackages -Manifest @($state.checkpoint.globalPackages)
            $state.phase = 'rolled-back'; $state.rolledBackAt = (Get-Date).ToString('o')
            Save-State -State $state
        }
        [pscustomobject]@{ status = 'passed'; phase = 'rollback'; node = Get-NodeVersion; npm = Get-NpmVersion } | ConvertTo-Json
    }
    'Accept' {
        $state = Read-State
        if ($state.phase -notin @('processes-refreshed','applied')) { throw "Accept is invalid from phase $($state.phase)" }
        Assert-Version -Node ([string]$state.candidateVersion) -Npm ([string]$state.expectedNpmVersion) | Out-Null
        $state.phase = 'accepted'; $state.acceptedAt = (Get-Date).ToString('o')
        Save-State -State $state
        [pscustomobject]@{ status = 'passed'; phase = 'accept'; node = Get-NodeVersion; npm = Get-NpmVersion; rollbackInstallerRetained = $true } | ConvertTo-Json
    }
    'Cleanup' {
        $state = Read-State
        if ($state.phase -ne 'accepted') { throw "Cleanup requires accepted state, found $($state.phase)" }
        Get-ChildItem -LiteralPath $RuntimeRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne $RunId } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip 1 |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        $state.phase = 'cleaned'; $state.cleanedAt = (Get-Date).ToString('o')
        Save-State -State $state
        [pscustomobject]@{ status = 'passed'; phase = 'cleanup'; retainedRun = $RunId; retentionFloor = 2 } | ConvertTo-Json
    }
}
