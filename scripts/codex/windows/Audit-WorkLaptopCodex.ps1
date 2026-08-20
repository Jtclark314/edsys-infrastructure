[CmdletBinding()]
param(
    [string]$HubHost = '9950x',
    [string]$HubUploadDirectory = '/home/jeremy/.codex/operator-checkpoints/work-laptop-codex-audit/incoming',
    [string]$OutputRoot = (Join-Path $env:LOCALAPPDATA 'EdSys-Private\work-laptop-codex-audits'),
    [ValidateRange(5, 120)]
    [int]$CommandTimeoutSeconds = 30,
    [switch]$NoUpload
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$expectedComputer = 'THOMPSON-LC086'
$expectedIdentity = 'THOMPSON\jclark'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($env:COMPUTERNAME -ine $expectedComputer -or $currentIdentity -ine $expectedIdentity) {
    throw "This audit is restricted to $expectedIdentity on $expectedComputer; current endpoint is $currentIdentity on $env:COMPUTERNAME."
}

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$auditId = 'work-laptop-codex-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$auditDirectory = Join-Path $OutputRoot $auditId
New-Item -ItemType Directory -Path $auditDirectory -Force | Out-Null
$reportPath = Join-Path $auditDirectory 'sanitized-report.json'

function ConvertTo-ProcessArgument {
    param([AllowEmptyString()][string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
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
        return [pscustomobject]@{
            available = $false
            exitCode = $null
            timedOut = $false
            output = @()
        }
    }

    $resolvedSource = [string]$command.Source
    if ([string]::IsNullOrWhiteSpace($resolvedSource) -and $command.PSObject.Properties.Name -contains 'Path') {
        $resolvedSource = [string]$command.Path
    }
    $resolvedExtension = [IO.Path]::GetExtension($resolvedSource)
    $usePowerShellHost = (
        $command.CommandType -ne [Management.Automation.CommandTypes]::Application -or
        $resolvedExtension -in @('.ps1', '.psm1', '.cmd', '.bat')
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    if ($usePowerShellHost) {
        $powershell = Get-Command 'powershell.exe' -ErrorAction Stop | Select-Object -First 1
        $invocationParts = @(
            '&',
            (ConvertTo-PowerShellLiteral -Value $resolvedSource)
        ) + @($Arguments | ForEach-Object { ConvertTo-PowerShellLiteral -Value ([string]$_) })
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
    try {
        $null = $process.Start()
    }
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
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $lines = @(
        (($stdout + [Environment]::NewLine + $stderr) -split "`r?`n") |
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

function Get-FirstLine {
    param([Parameter(Mandatory = $true)]$CommandResult)

    if ($CommandResult.output.Count -gt 0) {
        return [string]$CommandResult.output[0]
    }
    return $null
}

function Get-ToolRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$VersionArguments = @('--version')
    )

    $result = Invoke-BoundedCommand -FilePath $Name -Arguments $VersionArguments -TimeoutSeconds 12
    [pscustomobject]@{
        name = $Name
        available = $result.available
        source = if ($result.available) { $result.source } else { $null }
        version = if ($result.available) { Get-FirstLine -CommandResult $result } else { $null }
        exitCode = $result.exitCode
        timedOut = $result.timedOut
    }
}

function Get-SafeCodexConfig {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ exists = $false; values = @{} }
    }

    $allowed = @{
        '' = @('model', 'approval_policy', 'sandbox_mode', 'web_search', 'check_for_update_on_startup', 'allow_login_shell')
        'agents' = @('enabled', 'max_concurrent_threads_per_session', 'max_depth')
        'features' = @('apps', 'fast_mode', 'goals', 'multi_agent', 'remote_plugin', 'shell_snapshot')
        'windows' = @('sandbox', 'sandbox_private_desktop')
    }
    $section = ''
    $values = [ordered]@{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -ErrorAction Stop) {
        $line = ($rawLine -replace '\s+#.*$', '').Trim()
        if ($line -match '^\[([^\]]+)\]$') {
            $section = $Matches[1]
            continue
        }
        if ($line -notmatch '^([A-Za-z0-9_.-]+)\s*=\s*(.+)$') {
            continue
        }
        $key = $Matches[1]
        if (-not $allowed.ContainsKey($section) -or $allowed[$section] -notcontains $key) {
            continue
        }
        $value = $Matches[2].Trim()
        if ($value -match '^["''](.*)["'']$') {
            $value = $Matches[1]
        }
        $qualifiedKey = if ($section) { "$section.$key" } else { $key }
        $values[$qualifiedKey] = $value
    }

    return [pscustomobject]@{ exists = $true; values = $values }
}

function Get-PathAclSummary {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ exists = $false }
    }
    try {
        $acl = Get-Acl -LiteralPath $Path
        return [pscustomobject]@{
            exists = $true
            owner = [string]$acl.Owner
            protected = [bool]$acl.AreAccessRulesProtected
            access = @(
                $acl.Access |
                    Select-Object @{n = 'identity'; e = { [string]$_.IdentityReference }},
                        @{n = 'rights'; e = { [string]$_.FileSystemRights }},
                        @{n = 'type'; e = { [string]$_.AccessControlType }},
                        IsInherited
            )
        }
    }
    catch {
        return [pscustomobject]@{ exists = $true; error = $_.Exception.Message }
    }
}

$os = Get-CimInstance Win32_OperatingSystem
$computerSystem = Get-CimInstance Win32_ComputerSystem
$systemDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'"
$windowsVersion = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$pendingReboot = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired',
    'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
) | ForEach-Object {
    if ($_ -like '*Session Manager') {
        $value = (Get-ItemProperty -LiteralPath $_ -Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations
        return ($null -ne $value)
    }
    return (Test-Path -LiteralPath $_)
}

$appPackages = @()
try {
    $appPackages = @(
        Get-AppxPackage |
            Where-Object { $_.Name -match '(?i)OpenAI|ChatGPT|Codex' -or $_.PackageFamilyName -match '(?i)OpenAI|ChatGPT|Codex' } |
            Select-Object Name, Version, Architecture, PackageFamilyName, Status, SignatureKind
    )
}
catch {}

$uninstallRoots = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$desktopPackages = @()
try {
    $desktopPackages = @(
        Get-ItemProperty -Path $uninstallRoots -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match '(?i)OpenAI|ChatGPT|Codex' } |
            Select-Object DisplayName, DisplayVersion, Publisher
    )
}
catch {}

$processes = @()
try {
    $processes = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -match '(?i)ChatGPT|Codex|OpenAI' } |
            ForEach-Object {
                $fileVersion = $null
                $startTime = $null
                try { $fileVersion = $_.MainModule.FileVersionInfo.FileVersion } catch {}
                try { $startTime = $_.StartTime.ToString('o') } catch {}
                [pscustomobject]@{
                    name = $_.ProcessName
                    id = $_.Id
                    sessionId = $_.SessionId
                    startTime = $startTime
                    fileVersion = $fileVersion
                }
            }
    )
}
catch {}

$scheduledTasks = @()
try {
    $scheduledTasks = @(
        Get-ScheduledTask -ErrorAction SilentlyContinue |
            Where-Object { ($_.TaskName + ' ' + $_.TaskPath) -match '(?i)OpenAI|ChatGPT|Codex' } |
            Select-Object TaskName, TaskPath, State
    )
}
catch {}

$toolDefinitions = @(
    @{ Name = 'git'; Arguments = @('--version') },
    @{ Name = 'gh'; Arguments = @('--version') },
    @{ Name = 'node'; Arguments = @('--version') },
    @{ Name = 'npm.cmd'; Arguments = @('--version') },
    @{ Name = 'python'; Arguments = @('--version') },
    @{ Name = 'py'; Arguments = @('-V') },
    @{ Name = 'pwsh'; Arguments = @('-NoLogo', '-NoProfile', '-Command', '$PSVersionTable.PSVersion.ToString()') },
    @{ Name = 'rg'; Arguments = @('--version') },
    @{ Name = 'jq'; Arguments = @('--version') },
    @{ Name = 'uv'; Arguments = @('--version') },
    @{ Name = 'pipx'; Arguments = @('--version') },
    @{ Name = 'code'; Arguments = @('--version') },
    @{ Name = 'docker'; Arguments = @('--version') },
    @{ Name = 'wsl'; Arguments = @('--version') },
    @{ Name = 'winget'; Arguments = @('--version') }
)
$tools = @(
    foreach ($definition in $toolDefinitions) {
        Get-ToolRecord -Name $definition.Name -VersionArguments $definition.Arguments
    }
)

$codexVersion = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('--version') -TimeoutSeconds 12
$codexLogin = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('login', 'status') -TimeoutSeconds 20
$codexDoctor = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('doctor', '--summary', '--ascii') -TimeoutSeconds 45
$codexDaemonVersion = Invoke-BoundedCommand -FilePath 'codex' -Arguments @('app-server', 'daemon', 'version') -TimeoutSeconds 20
$safeDoctorLines = @(
    $codexDoctor.output |
        Where-Object {
            $_ -match '^(Codex Doctor|\s*\[(ok|!!|XX)\]|\d+ ok \||\s*(Environment|Configuration|Updates|Connectivity|Background Server))'
        } |
        Select-Object -First 80
)
$safeLoginLines = @(
    $codexLogin.output |
        Where-Object { $_ -match '(?i)logged in|chatgpt|api key|not logged in' } |
        Select-Object -First 5
)

$wingetList = Invoke-BoundedCommand -FilePath 'winget' -Arguments @('list', '--id', '9PLM9XGG6VKS', '--exact', '--source', 'msstore', '--disable-interactivity') -TimeoutSeconds 45
$wingetUpgrade = Invoke-BoundedCommand -FilePath 'winget' -Arguments @('upgrade', '--id', '9PLM9XGG6VKS', '--exact', '--source', 'msstore', '--include-unknown', '--disable-interactivity') -TimeoutSeconds 60

$tailscaleVersion = Invoke-BoundedCommand -FilePath 'tailscale' -Arguments @('version') -TimeoutSeconds 12
$tailscaleIp = Invoke-BoundedCommand -FilePath 'tailscale' -Arguments @('ip', '-4') -TimeoutSeconds 12

$sshResult = Invoke-BoundedCommand -FilePath 'ssh' -Arguments @(
    '-o', 'BatchMode=yes',
    '-o', 'PreferredAuthentications=publickey',
    '-o', 'PasswordAuthentication=no',
    '-o', 'ConnectTimeout=10',
    $HubHost,
    "hostname; id -un; codex --version; codex app-server daemon version"
) -TimeoutSeconds 30
$remote = [ordered]@{
    exitCode = $sshResult.exitCode
    timedOut = $sshResult.timedOut
    host = if ($sshResult.output.Count -ge 1) { $sshResult.output[0] } else { $null }
    user = if ($sshResult.output.Count -ge 2) { $sshResult.output[1] } else { $null }
    codexVersion = if ($sshResult.output.Count -ge 3) { $sshResult.output[2] } else { $null }
    daemon = $null
}
if ($sshResult.output.Count -ge 4) {
    try {
        $daemon = $sshResult.output[3] | ConvertFrom-Json -ErrorAction Stop
        $remote.daemon = [ordered]@{
            status = $daemon.status
            managedCodexVersion = $daemon.managedCodexVersion
            cliVersion = $daemon.cliVersion
            appServerVersion = $daemon.appServerVersion
            backend = if ($daemon.PSObject.Properties.Name -contains 'backend') { $daemon.backend } else { $null }
        }
    }
    catch {
        $remote.daemon = [ordered]@{ parseError = $true }
    }
}

$codexHome = Join-Path $env:USERPROFILE '.codex'
$configPath = Join-Path $codexHome 'config.toml'
$profiles = @(
    if (Test-Path -LiteralPath $codexHome) {
        Get-ChildItem -LiteralPath $codexHome -Filter '*.config.toml' -File -ErrorAction SilentlyContinue |
            ForEach-Object {
                [pscustomobject]@{
                    name = $_.Name
                    config = Get-SafeCodexConfig -Path $_.FullName
                }
            }
    }
)

$executionPolicy = @(
    Get-ExecutionPolicy -List |
        Select-Object Scope, ExecutionPolicy
)
$wslInventory = Invoke-BoundedCommand -FilePath 'wsl' -Arguments @('--list', '--verbose') -TimeoutSeconds 20
$ghAccount = Invoke-BoundedCommand -FilePath 'gh' -Arguments @('api', 'user', '--jq', '.login') -TimeoutSeconds 20

$report = [ordered]@{
    schemaVersion = 1
    auditId = $auditId
    generatedAt = (Get-Date).ToString('o')
    safety = 'Sanitized controller metadata only; no credential values, raw Codex config, SSH config, browser data, logs, mail, or file contents.'
    endpoint = [ordered]@{
        computer = $env:COMPUTERNAME
        identity = $currentIdentity
        elevated = $isAdministrator
        sessionId = (Get-Process -Id $PID).SessionId
        powershell = $PSVersionTable.PSVersion.ToString()
        languageMode = [string]$ExecutionContext.SessionState.LanguageMode
        osCaption = $os.Caption
        osVersion = $os.Version
        osBuild = $os.BuildNumber
        displayVersion = $windowsVersion.DisplayVersion
        editionId = $windowsVersion.EditionID
        architecture = $os.OSArchitecture
        partOfDomain = [bool]$computerSystem.PartOfDomain
        domainSecureChannel = 'not-tested-without-a-reported-domain-symptom'
        rebootPending = ($pendingReboot -contains $true)
        freeSystemDriveGiB = if ($null -ne $systemDrive) { [math]::Round(($systemDrive.FreeSpace / 1GB), 2) } else { $null }
    }
    executionPolicy = $executionPolicy
    apps = [ordered]@{
        appx = $appPackages
        desktop = $desktopPackages
        processes = $processes
        scheduledTasks = $scheduledTasks
        storePackageList = [ordered]@{
            exitCode = $wingetList.exitCode
            timedOut = $wingetList.timedOut
            output = @($wingetList.output | Select-Object -First 20)
        }
        storeUpgradeCheck = [ordered]@{
            exitCode = $wingetUpgrade.exitCode
            timedOut = $wingetUpgrade.timedOut
            output = @($wingetUpgrade.output | Select-Object -First 20)
        }
    }
    codex = [ordered]@{
        version = Get-FirstLine -CommandResult $codexVersion
        loginStatus = $safeLoginLines
        doctorExitCode = $codexDoctor.exitCode
        doctorTimedOut = $codexDoctor.timedOut
        doctorSummary = $safeDoctorLines
        daemonVersion = @($codexDaemonVersion.output | Select-Object -First 5)
        config = Get-SafeCodexConfig -Path $configPath
        profiles = $profiles
        homeAcl = Get-PathAclSummary -Path $codexHome
        configAcl = Get-PathAclSummary -Path $configPath
        openAiApiKeyPresent = [ordered]@{
            process = -not [string]::IsNullOrEmpty($env:OPENAI_API_KEY)
            user = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User'))
            machine = -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'Machine'))
        }
    }
    controllerPath = [ordered]@{
        tailscaleVersion = Get-FirstLine -CommandResult $tailscaleVersion
        tailscaleIpv4 = Get-FirstLine -CommandResult $tailscaleIp
        ssh = $remote
    }
    tools = $tools
    githubCli = [ordered]@{
        authenticated = ($ghAccount.exitCode -eq 0 -and $ghAccount.output.Count -gt 0)
        account = if ($ghAccount.exitCode -eq 0 -and $ghAccount.output.Count -gt 0) { $ghAccount.output[0] } else { $null }
    }
    wsl = [ordered]@{
        exitCode = $wslInventory.exitCode
        timedOut = $wslInventory.timedOut
        output = @($wslInventory.output | Select-Object -First 30)
    }
}

$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8

$uploadStatus = 'skipped'
if (-not $NoUpload) {
    $remotePath = "$HubHost`:$HubUploadDirectory/$auditId.json"
    $scpResult = Invoke-BoundedCommand -FilePath 'scp' -Arguments @(
        '-q',
        '-o', 'BatchMode=yes',
        '-o', 'PreferredAuthentications=publickey',
        '-o', 'PasswordAuthentication=no',
        '-o', 'ConnectTimeout=10',
        $reportPath,
        $remotePath
    ) -TimeoutSeconds 45
    if ($scpResult.exitCode -ne 0) {
        throw "The sanitized audit completed, but upload to the private hub failed. Local report: $reportPath"
    }
    $uploadStatus = 'uploaded'
}

Write-Host ''
Write-Host 'PASS: the read-only work-laptop Codex audit completed.'
Write-Host "Local private report: $reportPath"
Write-Host "Hub upload: $uploadStatus"
Write-Host 'No application, package, Codex setting, credential, scheduled task, firewall rule, or remote-access setting was changed.'
