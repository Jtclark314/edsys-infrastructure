#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$PlanOnly,
    [switch]$FinishOnly,
    [switch]$NoRestart,
    [ValidateRange(0, 120)][int]$CloseDelaySeconds = 20,
    [string]$RuntimeRoot
)

# No path-dependent parameter defaults: Windows PowerShell 5.1 binds them
# before script entry in some launcher contexts.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:Results = New-Object 'Collections.Generic.List[object]'
$script:StopMutations = $false
$script:CommandNumber = 0
$script:CoreReady = $false
$script:RunDirectory = $null
$script:Lock = $null

function Protect-RunDirectory {
    param([string]$Path)
    New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & "$env:SystemRoot\System32\icacls.exe" $Path '/inheritance:r' '/grant:r' "*$sid`:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '/Q' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect the local report directory.' }
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) { throw 'Report directory still inherits permissions.' }
    foreach ($rule in $acl.Access) {
        $ruleSid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($ruleSid -notin @($sid, 'S-1-5-18')) { throw 'Unexpected report directory permission.' }
    }
}

function Save-Results {
    if (-not $script:RunDirectory) { return }
    $script:Results.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $script:RunDirectory 'results.json') -Encoding UTF8
    $script:Results.ToArray() | Format-Table Component, Status, Before, After, Detail -Wrap | Out-String -Width 160 |
        Set-Content -LiteralPath (Join-Path $script:RunDirectory 'results.txt') -Encoding UTF8
}

function Add-Result {
    param([string]$Component, [string]$Status, [string]$Before = '', [string]$After = '', [string]$Detail = '')
    $script:Results.Add([pscustomobject]@{Component=$Component; Status=$Status; Before=$Before; After=$After; Detail=$Detail})
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Component, $Detail)
    Save-Results
}

function Quote-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-Tool {
    param([string]$File, [string[]]$Arguments = @(), [int]$TimeoutSeconds = 180, [switch]$Mutation, [string]$WorkingDirectory)
    if ($Mutation -and ($PlanOnly -or $script:StopMutations)) { throw 'Mutation disabled for this run.' }
    $command = Get-Command $File -CommandType Application, ExternalScript -ErrorAction Stop | Select-Object -First 1
    $source = $command.Source
    $info = New-Object Diagnostics.ProcessStartInfo
    if ([IO.Path]::GetExtension($source) -in @('.cmd', '.bat', '.ps1')) {
        $parts = @("& '" + $source.Replace("'", "''") + "'")
        foreach ($arg in $Arguments) { $parts += "'" + $arg.Replace("'", "''") + "'" }
        $text = '$ErrorActionPreference="Stop"; $global:LASTEXITCODE=0; try { ' + ($parts -join ' ') + '; exit $LASTEXITCODE } catch { Write-Error $_ -ErrorAction Continue; exit 1 }'
        $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($text))
    } else {
        $info.FileName = $source
        $info.Arguments = (@($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    }
    if ($WorkingDirectory) {
        $info.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory -ErrorAction Stop).ProviderPath
    }
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $script:CommandNumber++
    $log = Join-Path $script:RunDirectory ('command-{0:000}.txt' -f $script:CommandNumber)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        while (-not $process.WaitForExit(5000)) {
            Write-Host ("  Still running {0} ({1}s)..." -f [IO.Path]::GetFileName($source), [int]$watch.Elapsed.TotalSeconds)
            if ($watch.Elapsed.TotalSeconds -ge $TimeoutSeconds) {
                # Only terminate this command's process tree. Stop subsequent
                # mutations because a detached package service may still be busy.
                $script:StopMutations = $true
                & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F 2>$null | Out-Null
                throw "Command timed out after $TimeoutSeconds seconds. Further installs stopped; check Task Manager before retrying."
            }
        }
        if (-not $stdout.Wait(10000) -or -not $stderr.Wait(10000)) {
            $script:StopMutations = $true
            throw 'Command output remained open after exit. Further installs stopped.'
        }
        $output = $stdout.Result
        $errors = $stderr.Result
        [IO.File]::WriteAllText($log, $output + [Environment]::NewLine + $errors)
        return [pscustomobject]@{Code=$process.ExitCode; Text=$output.Trim(); ErrorText=$errors.Trim(); Log=$log}
    } finally { $process.Dispose() }
}

function Require-Success {
    param($Result)
    if ($Result.Code -ne 0) {
        throw "Command exited $($Result.Code). Diagnostic: $($Result.Log)"
    }
    return $Result.Text
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body, [switch]$Mutation)
    if ($Mutation -and $script:StopMutations) {
        Add-Result $Name 'Blocked' '' '' 'An earlier timeout stopped further installs.'
        return
    }
    Write-Host "`n--- $Name ---" -ForegroundColor Cyan
    try { & $Body } catch {
        Add-Result $Name 'Failed' '' '' $_.Exception.Message
        if ($script:RunDirectory) { $_ | Out-String | Add-Content -LiteralPath (Join-Path $script:RunDirectory 'errors.txt') }
    }
}

function Get-CoreVersion {
    param([string]$Binary)
    if (-not $Binary -or -not (Get-Command $Binary -ErrorAction SilentlyContinue)) { return '' }
    $r = Invoke-Tool $Binary @('--version') 30
    if ($r.Code -eq 0 -and $r.Text -match '^codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[-+][\w.-]+)?)$') { return $Matches[1] }
    return ''
}

function Get-StableRelease {
    $lastFailure = ''
    foreach ($uri in @('https://releases.openai.com/codex/channels/latest', 'https://api.github.com/repos/openai/codex/releases/latest')) {
        try {
            $r = Invoke-RestMethod -Uri $uri -Headers @{'User-Agent'='EdSys-WorkLaptop-Updater'} -TimeoutSec 45
            $version = ([string]$r.tag_name) -replace '^rust-v', ''
            if ($version -notmatch '^\d+\.\d+\.\d+$' -or $r.prerelease -eq $true -or $r.draft -eq $true) { throw 'Source did not identify a stable release.' }
            return $version
        } catch { $lastFailure = $_.Exception.Message }
    }
    throw "Could not resolve the official stable CLI release: $lastFailure"
}

function Get-AppVersion {
    param([string]$Name)
    $app = Get-AppxPackage -Name $Name | Sort-Object Version -Descending | Select-Object -First 1
    if ($app) { return [string]$app.Version }
    return ''
}

function Get-MsixIdentity {
    param([string]$Path)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Path)
    try {
        $entry = $zip.GetEntry('AppxManifest.xml')
        if (-not $entry) { throw 'MSIX has no AppxManifest.xml.' }
        $reader = New-Object IO.StreamReader($entry.Open())
        try { [xml]$xml = $reader.ReadToEnd() } finally { $reader.Dispose() }
        return $xml.Package.Identity
    } finally { $zip.Dispose() }
}

function Assert-MsixIdentity {
    param($Identity, [string]$Architecture, [string]$ExistingPublisher)
    if ([string]$Identity.Name -ne 'OpenAI.Codex' -or [string]$Identity.ProcessorArchitecture -ine $Architecture) {
        throw 'Downloaded MSIX identity or architecture does not match ChatGPT.'
    }
    $null = [version]([string]$Identity.Version)
    if ($ExistingPublisher -and [string]$Identity.Publisher -cne $ExistingPublisher) { throw 'MSIX publisher differs from the installed app.' }
    # Windows Add-AppxPackage performs signature, certificate and policy checks.
}

function Get-ExtensionVersion {
    param([string]$Editor)
    $r = Invoke-Tool $Editor @('--list-extensions', '--show-versions') 120
    $text = Require-Success $r
    $line = @($text -split '\r?\n' | Where-Object { $_ -match '^openai\.chatgpt@' })
    if ($line.Count -eq 1) { return ($line[0] -split '@', 2)[1] }
    return ''
}

function Select-StableExtension {
    param($Extension, [string]$Platform)
    if ($Extension.publisher.publisherName -cne 'openai' -or $Extension.extensionName -cne 'chatgpt') { throw 'Unexpected extension publisher or name.' }
    $versions = @($Extension.versions | Where-Object {
        $_.targetPlatform -eq $Platform -and
        $_.version -match '^\d+\.\d+\.\d+$' -and
        @($_.properties | Where-Object { $_.key -eq 'Microsoft.VisualStudio.Code.PreRelease' -and $_.value -eq 'true' }).Count -eq 0
    } | Sort-Object { [version]$_.version } -Descending)
    if (-not $versions.Count) { throw 'No stable Windows extension package in the publisher catalog.' }
    return $versions[0]
}

function Stage-Extension {
    param([string]$Architecture)
    $body = @{filters=@(@{criteria=@(@{filterType=7;value='openai.chatgpt'})});flags=402} | ConvertTo-Json -Depth 6
    $catalog = Invoke-RestMethod -Method Post -Uri 'https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery' -ContentType 'application/json' -Headers @{Accept='application/json;api-version=7.2-preview.1'} -Body $body -TimeoutSec 90
    $extension = @($catalog.results[0].extensions)[0]
    $release = Select-StableExtension $extension ('win32-' + $Architecture)
    $assets = @($release.files | Where-Object { $_.assetType -eq 'Microsoft.VisualStudio.Services.VSIXPackage' })
    $digests = @($release.properties | Where-Object { $_.key -eq 'Microsoft.VisualStudio.Services.VsixSha256' })
    if ($assets.Count -ne 1 -or $digests.Count -ne 1 -or $digests[0].value -notmatch '^[a-fA-F0-9]{64}$') { throw 'Extension package or SHA-256 metadata missing.' }
    $uri = [uri]$assets[0].source
    if ($uri.Scheme -cne 'https' -or $uri.Host -cne 'openai.gallerycdn.vsassets.io') { throw 'Unexpected extension download origin.' }
    $path = Join-Path $script:RunDirectory 'openai.chatgpt.vsix'
    Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $path -TimeoutSec 900
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $digests[0].value) { throw 'Extension SHA-256 mismatch.' }
    return [pscustomobject]@{Version=[string]$release.version; Path=$path}
}

function Get-PluginSignature {
    param([object[]]$Plugins)
    return (@($Plugins | ForEach-Object { '{0}|{1}' -f $_.pluginId, $_.enabled } | Sort-Object) -join "`n")
}

function Get-RefreshablePlugins {
    param([object[]]$Installed, [object[]]$Available)
    foreach ($p in $Installed) {
        # Preserve disabled entries and channel identity. Hosted/local runtime
        # bundles follow their provider, not a forced migration to a Git catalog.
        if (-not $p.enabled -or $p.marketplaceName -ne 'openai-curated') { continue }
        $candidate = @($Available | Where-Object { $_.pluginId -ceq $p.pluginId })
        if ($candidate.Count -eq 1 -and $candidate[0].version -match '^\d+(?:\.\d+){1,3}$' -and $p.version -match '^\d+(?:\.\d+){1,3}$' -and [version]$candidate[0].version -gt [version]$p.version) { $p }
    }
}

function Stop-DesktopApps {
    $session = (Get-Process -Id $PID).SessionId
    $apps = @(Get-AppxPackage | Where-Object { $_.Name -in @('OpenAI.Codex', 'OpenAI.ChatGPT-Desktop') })
    $roots = @($apps | ForEach-Object { $_.InstallLocation.TrimEnd('\') + '\' })
    $targets = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $p = $_
        $p.SessionId -eq $session -and $p.Path -and @($roots | Where-Object { $p.Path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
    })
    foreach ($p in $targets) { if ($p.MainWindowHandle -ne 0) { $null = $p.CloseMainWindow() } }
    if ($targets.Count) { Start-Sleep -Seconds 5 }
    foreach ($p in $targets) {
        $p.Refresh()
        if (-not $p.HasExited) { $p.Kill() }
    }
    foreach ($p in $targets) { if (-not $p.WaitForExit(15000)) { throw 'An app process did not exit. Close ChatGPT and retry.' } }
}

function Update-StoreApp {
    param([string]$Name, [string]$Id)
    $before = Get-AppVersion $Name
    if (-not $before) { Add-Result $Name 'Skipped' '' '' 'Not installed.'; return }
    if ($PlanOnly) { Add-Result $Name 'Planned' $before 'Store channel' 'Update through Microsoft Store using winget.'; return }
    $r = Invoke-Tool 'winget.exe' @('upgrade', '--id', $Id, '--exact', '--source', 'msstore', '--silent', '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity') 900 -Mutation
    $after = Get-AppVersion $Name
    if ($after -and [version]$after -gt [version]$before) { Add-Result $Name 'Updated' $before $after 'Installed Appx version increased.' }
    elseif ($r.Code -eq -1978335189) { Add-Result $Name 'NoOffer' $before $after 'Store reports no applicable upgrade; this is not proof of global latest.' }
    elseif ($r.Code -eq 0) { Add-Result $Name 'Check' $before $after 'Store command succeeded; version unchanged. Check Store Downloads. ' }
    else { $null = Require-Success $r }
}

function Open-UnifiedApp {
    $app = Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1
    if (-not $app) { throw 'Unified ChatGPT app is not installed.' }
    $manifest = Get-AppxPackageManifest -Package $app.PackageFullName
    $id = [string]@($manifest.Package.Applications.Application)[0].Id
    if (-not $id) { throw 'Installed app has no application ID.' }
    Start-Process explorer.exe -ArgumentList ('shell:AppsFolder\' + $app.PackageFamilyName + '!' + $id)
    Start-Sleep -Seconds 15
    $session = (Get-Process -Id $PID).SessionId
    $root = $app.InstallLocation.TrimEnd('\') + '\'
    $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $session -and $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) })
    if (-not $running.Count) { throw 'App launch requested, but its process was not observed. Open ChatGPT from Start.' }
    Add-Result 'Desktop restart' 'Verified' '' ([string]$app.Version) 'Fresh installed-app process observed; sign-in and a new task still need a user check.'
    # Report running engine versions separately; never infer them from CLI parity.
    foreach ($binary in @($running | Where-Object { $_.ProcessName -eq 'codex' } | Select-Object -ExpandProperty Path -Unique)) {
        $v = Get-CoreVersion $binary
        Add-Result 'Desktop engine' 'Observed' '' $v 'Version of the executable backing an observed desktop process.'
    }
}

function Update-CuratedPlugins {
    param([string]$Core)
    if (-not $script:CoreReady) { throw 'CLI did not pass version verification; plugin changes skipped.' }
    $before = (Require-Success (Invoke-Tool $core @('plugin', 'list', '--json') 120)) | ConvertFrom-Json
    $signature = Get-PluginSignature @($before.installed)
    $before.installed | Select-Object pluginId, version, enabled | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $script:RunDirectory 'plugins-before.json') -Encoding UTF8
    $markets = (Require-Success (Invoke-Tool $core @('plugin', 'marketplace', 'list', '--json') 120)) | ConvertFrom-Json
    $curated = @($markets.marketplaces | Where-Object { $_.name -eq 'openai-curated' })
    if ($curated.Count -eq 0) {
        Add-Result 'Curated plugins' 'Skipped' '' '' 'Curated marketplace is not configured; no new source added.'
    } else {
        if ($curated.Count -ne 1) { throw 'Curated marketplace identity is ambiguous.' }
        # Listing includes implicit defaults and local sources. Only an explicit
        # Git source supports the marketplace upgrade subcommand.
        if ($curated[0].marketplaceSource.sourceType -eq 'git') {
            $refresh = (Require-Success (Invoke-Tool $core @('plugin', 'marketplace', 'upgrade', 'openai-curated', '--json') 300 -Mutation)) | ConvertFrom-Json
            if (@($refresh.errors | Where-Object { $null -ne $_ }).Count) { throw 'Configured Git marketplace refresh reported errors; inspect the command diagnostic.' }
            Add-Result 'Curated catalog' 'Refreshed' '' '' 'Configured Git marketplace refreshed.'
        } else {
            Add-Result 'Curated catalog' 'Managed' '' '' 'Built-in or local catalog; no Git upgrade attempted. Checking versions exposed by Codex on the existing channel.'
        }
        $catalog = (Require-Success (Invoke-Tool $core @('plugin', 'list', '--available', '--json') 180)) | ConvertFrom-Json
        $selected = @(Get-RefreshablePlugins @($before.installed) @($catalog.available))
        $failuresBefore = @($script:Results | Where-Object { $_.Status -in @('Failed','Blocked') }).Count
        foreach ($p in $selected) {
            Invoke-Step ('Plugin ' + $p.pluginId) -Mutation {
                $expected = @($catalog.available | Where-Object { $_.pluginId -ceq $p.pluginId })[0]
                $r = (Require-Success (Invoke-Tool $core @('plugin', 'add', $p.pluginId, '--json') 300 -Mutation)) | ConvertFrom-Json
                if ($r.pluginId -cne $p.pluginId -or $r.version -ne $expected.version) { throw 'Plugin install result did not match the selected catalog entry.' }
                Add-Result ('Plugin ' + $p.pluginId) 'Updated' $p.version $r.version 'Existing enabled plugin updated; same identity and channel.'
            }
        }
        $after = (Require-Success (Invoke-Tool $core @('plugin', 'list', '--json') 120)) | ConvertFrom-Json
        if ((Get-PluginSignature @($after.installed)) -cne $signature) { $script:StopMutations = $true; throw 'Plugin identity or enabled-state drift detected. Review the settings backup before further plugin changes.' }
        $after.installed | Select-Object pluginId, version, enabled | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $script:RunDirectory 'plugins-after.json') -Encoding UTF8
        $failed = @($script:Results | Where-Object { $_.Status -in @('Failed','Blocked') }).Count -gt $failuresBefore
        $status = if ($failed) { 'Check' } elseif ($selected.Count) { 'Verified' } else { 'NoOffer' }
        Add-Result 'Curated plugins' $status '' '' 'Installed identities and enabled flags verified. Results reflect the catalog exposed by Codex, not a forced source refresh or channel migration.'
    }
}

function Write-DoctorResults {
    param($Result)
    # A nonzero doctor exit may still contain a complete, useful JSON report.
    # Preserve actual failing checks instead of treating the report as unreadable.
    $report = $Result.Text | ConvertFrom-Json -ErrorAction Stop
    if ($report.schemaVersion -ne 1 -or -not $report.checks -or $report.checks -is [array]) {
        throw "Unrecognized doctor report. Diagnostic: $($Result.Log)"
    }
    $checks = @($report.checks.PSObject.Properties | ForEach-Object { $_.Value })
    if (-not $checks.Count) { throw "Doctor report contains no checks. Diagnostic: $($Result.Log)" }
    $passing = @($checks | Where-Object { $_.status -eq 'ok' }).Count
    $failing = @($checks | Where-Object { $_.status -eq 'fail' }).Count
    $warnings = @($checks | Where-Object { $_.status -eq 'warning' }).Count
    foreach ($check in $checks) {
        if ($check.status -eq 'ok') { continue }
        $status = switch ($check.status) { 'fail' { 'Failed' } 'warning' { 'Warning' } default { 'Check' } }
        $detail = [string]$check.summary
        if ($check.id -eq 'sandbox.helpers' -and $check.status -eq 'fail') {
            $detail += ' Use the supported Windows sandbox setup flow; inspect .sandbox\sandbox.log if setup fails. No sandbox settings were changed.'
        } elseif ($check.id -eq 'security.endpoint') {
            $detail += ' This warning does not establish the cause of another failure; security policy remains unchanged.'
        }
        Add-Result ('Doctor: ' + $check.id) $status '' '' $detail
    }
    $consistent = (($report.overallStatus -eq 'ok' -and $passing -eq $checks.Count -and $Result.Code -eq 0) -or
        ($report.overallStatus -eq 'warning' -and $warnings -gt 0 -and $failing -eq 0) -or
        ($report.overallStatus -eq 'fail' -and $failing -gt 0))
    if (-not $consistent) {
        Add-Result 'Doctor report' 'Failed' '' '' "Unclassified or inconsistent doctor status/exit; inspect $($Result.Log)."
    }
    $status = if ($failing -or -not $consistent) { 'Failed' } elseif ($warnings) { 'Warning' } else { 'Checked' }
    Add-Result 'Codex doctor' $status '' $report.codexVersion "$passing passing, $failing failing, $warnings warning checks. Full redacted diagnostic: $($Result.Log)"
}

function Invoke-CodexHealth {
    param([string]$Core)
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $actual = Get-CoreVersion 'codex.exe'
    $native = Get-CoreVersion $Core
    Add-Result 'CLI PATH' $(if ($actual -and $actual -eq $native) { 'Verified' } else { 'Check' }) $native $actual 'Compares the default command with the standalone executable; close old terminals to refresh PATH.'
    if (-not $native) { throw 'Standalone CLI version could not be verified.' }
    Write-DoctorResults (Invoke-Tool $Core @('doctor', '--json') 180)
}

function Backup-CodexSettings {
    param([string]$CodexRoot, [string]$Backup)
    # Private local settings only; do not read authentication or session stores.
    foreach ($file in @(Get-ChildItem -LiteralPath $CodexRoot -Filter '*.toml' -File -ErrorAction SilentlyContinue)) { Copy-Item -LiteralPath $file.FullName -Destination $Backup }
    foreach ($relative in @('plugins\config.json', 'plugins\installed_plugins.json', 'plugins\marketplaces.json')) {
        $path = Join-Path $CodexRoot $relative
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $Backup ([IO.Path]::GetFileName($path))) }
    }
    [IO.File]::WriteAllText((Join-Path $Backup 'user-path.txt'), [string][Environment]::GetEnvironmentVariable('Path', 'User'))
}

function Invoke-SandboxProbe {
    param([string]$Core)
    # Test in a new empty user directory, never System32 or a business workspace.
    # Use the installed runtime and its existing sandbox policy. A successful
    # native refresh may update its own provisioning status; do not erase it.
    $parent = Join-Path $env:LOCALAPPDATA 'EdSys-Sandbox-Checks'
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $workspace = Join-Path $parent ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workspace | Out-Null
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # Codex 0.154.0 couples sandbox -C/--cd to -P/--permission-profile.
    # Set the native process cwd so the existing config/managed policy applies.
    $r = Invoke-Tool $Core @('sandbox', '--', $shell, '-NoProfile', '-NonInteractive', '-Command', '[Console]::WriteLine("EDSYS_SANDBOX_OK")') 120 -Mutation -WorkingDirectory $workspace
    if ($r.Code -ne 0) {
        $detail = if ($r.ErrorText) { $r.ErrorText } else { $r.Text }
        $detail = (@($detail -split '\r?\n' | Where-Object { $_.Trim() } | Select-Object -First 5) -join ' ')
        if ($detail.Length -gt 700) { $detail = $detail.Substring(0, 700) }
        throw "Native sandbox probe failed (exit $($r.Code)). $detail Diagnostic: $($r.Log)"
    }
    if (@($r.Text -split '\r?\n' | Where-Object { $_.Trim() -ceq 'EDSYS_SANDBOX_OK' }).Count -ne 1) { throw "Native sandbox probe did not return its expected marker. Diagnostic: $($r.Log)" }
    Add-Result 'Native sandbox probe' 'Verified' '' '' 'A harmless PowerShell command ran from a fresh user folder under the installed sandbox configuration. No System32 write access was requested.'
}

function Invoke-FinishOnly {
    param([string]$Core, [string]$CodexRoot, [string]$Backup)
    if ($PlanOnly) {
        Add-Result 'Follow-up plan' 'PlanOnly' '' '' 'Would check available curated plugin updates, run a harmless native sandbox probe in a fresh user folder, and check CLI health. No package installers or app restarts.'
        return
    }
    $script:CoreReady = [bool](Get-CoreVersion $Core)
    Backup-CodexSettings $CodexRoot $Backup
    Invoke-Step 'Plugin follow-up' -Mutation { Update-CuratedPlugins $Core }
    Invoke-Step 'Native sandbox probe' -Mutation { Invoke-SandboxProbe $Core }
    Invoke-Step 'CLI health and launcher check' { Invoke-CodexHealth $Core }
    Add-Result 'Follow-up run' 'Check' '' '' 'Applications were left running. Start a fresh session to activate any plugin changes. Sandbox configuration and endpoint policy were not changed; the native sandbox probe may refresh its own workspace setup.'
}

$exitCode = 0
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Run this script on the Windows work laptop.' }
    if ($PSVersionTable.PSEdition -eq 'Core' -or -not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell (powershell.exe), as shown in the launch command.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($env:COMPUTERNAME -ine 'THOMPSON-LC086' -or $identity.Name -ine 'THOMPSON\jclark') { throw 'This updater is for THOMPSON\jclark on THOMPSON-LC086.' }
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Open ordinary PowerShell, not Run as administrator. Apps and extensions belong to your desktop account.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if (-not $RuntimeRoot) { $RuntimeRoot = Join-Path $env:LOCALAPPDATA 'EdSys-Private\work-laptop-updates' }
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
    $script:Lock = [IO.File]::Open((Join-Path $RuntimeRoot 'updater.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $script:RunDirectory = Join-Path $RuntimeRoot ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID)
    Protect-RunDirectory $script:RunDirectory
    $backup = Join-Path $script:RunDirectory 'backup'
    New-Item -ItemType Directory -Path $backup | Out-Null
    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $core = Join-Path $codexRoot 'packages\standalone\current\bin\codex.exe'
    $beforeCore = Get-CoreVersion $core
    $targetCore = ''
    $installer = Join-Path $script:RunDirectory 'official-codex-install.ps1'
    $msix = Join-Path $script:RunDirectory 'ChatGPT.msix'
    $msixVersion = ''
    $architecture = if ($env:PROCESSOR_ARCHITEW6432 -eq 'ARM64' -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    $editors = New-Object 'Collections.Generic.List[object]'
    $npmPackages = @()
    $pluginsBefore = @()
    Write-Host "Work laptop Codex / ChatGPT updater`nLocal reports: $script:RunDirectory" -ForegroundColor Green
    if ($FinishOnly) { Write-Host 'Focused follow-up: plugins, a sandbox probe in a fresh user folder, and health checks; applications stay open.' }
    else { Write-Host 'Uses stable official releases. Save drafts and finish local Codex tasks before installation.' }
    Write-Host 'Existing configuration, sign-ins, corporate policy, and prior release directories are retained.'

    if ($FinishOnly) {
        Invoke-FinishOnly $core $codexRoot $backup
    } else {
    Invoke-Step 'CLI preflight' {
        $targetCore = Get-StableRelease
        Invoke-WebRequest -UseBasicParsing -Headers @{'User-Agent'='EdSys-WorkLaptop-Updater'} -Uri 'https://releases.openai.com/codex/install.ps1' -OutFile $installer -TimeoutSec 90
        $source = Get-Content -LiteralPath $installer -Raw
        foreach ($marker in @('Test-ArchiveDigest', 'Get-PackageArchiveDigest', 'https://releases.openai.com/codex')) {
            if (-not $source.Contains($marker)) { throw "Official installer changed: missing $marker. Review required." }
        }
        # These assignments must reach the caller of Invoke-Step.
        $script:TargetCore = $targetCore
        Add-Result 'Codex CLI plan' 'Planned' $beforeCore $targetCore 'Official stable channel; digest-verified vendor installer; prior release retained.'
    }
    Invoke-Step 'Desktop preflight' {
        $existing = Get-AppxPackage -Name 'OpenAI.Codex' | Sort-Object Version -Descending | Select-Object -First 1
        Invoke-WebRequest -UseBasicParsing -Uri "https://persistent.oaistatic.com/codex-app-prod/ChatGPT-$architecture.msix" -OutFile $msix -TimeoutSec 900
        $packageIdentity = Get-MsixIdentity $msix
        Assert-MsixIdentity $packageIdentity $architecture ([string]$existing.Publisher)
        $script:MsixVersion = [string]$packageIdentity.Version
        Add-Result 'ChatGPT desktop plan' 'Planned' ([string]$existing.Version) $script:MsixVersion 'Official signed MSIX; Windows verifies signature and deployment policy.'
    }
    Invoke-Step 'Editor preflight' {
        foreach ($name in @('code.cmd', 'code-insiders.cmd', 'cursor.cmd', 'windsurf.cmd')) {
            $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $cmd) { continue }
            $v = Get-ExtensionVersion $cmd.Source
            if ($v) { $editors.Add([pscustomobject]@{Command=$cmd.Source; Name=$name; Before=$v}) }
        }
        if (-not $editors.Count) { Add-Result 'Codex editor extensions' 'Skipped' '' '' 'No official openai.chatgpt extension found in default profiles of detected editors.' }
        if ($editors.Count) { $script:ExtensionStage = Stage-Extension $architecture }
        foreach ($e in $editors) { Add-Result $e.Name 'Planned' $e.Before $script:ExtensionStage.Version 'SHA-256 verified stable publisher VSIX; editor restart required.' }
    }
    Invoke-Step 'npm preflight' {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { Add-Result 'npm tools' 'Skipped' '' '' 'npm is not installed.'; return }
        $inventory = (Require-Success (Invoke-Tool 'npm.cmd' @('list', '--global', '--depth=0', '--json') 120)) | ConvertFrom-Json
        foreach ($name in @('@openai/codex', '@playwright/mcp', 'chrome-devtools-mcp')) {
            $property = $inventory.dependencies.PSObject.Properties[$name]
            if (-not $property) { continue }
            $version = (Require-Success (Invoke-Tool 'npm.cmd' @('view', "$name@latest", 'version', '--json') 90)) | ConvertFrom-Json
            if ([string]$version -notmatch '^\d+\.\d+\.\d+$') { throw "No stable npm version resolved for $name." }
            $script:NpmPackages += [pscustomobject]@{Name=$name; Before=[string]$property.Value.version; Target=[string]$version}
            Add-Result $name 'Planned' ([string]$property.Value.version) ([string]$version) 'Existing global package only; exact resolved stable version.'
        }
    }
    Invoke-Step 'Plugin preflight' {
        if (-not $beforeCore) { Add-Result 'Plugin inventory' 'Check' '' '' 'Will retry after CLI installation.'; return }
        $p = (Require-Success (Invoke-Tool $core @('plugin', 'list', '--json') 120)) | ConvertFrom-Json
        $script:PluginsBefore = @($p.installed)
        $script:PluginsBefore | Select-Object pluginId, version, enabled | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $script:RunDirectory 'plugins-before.json') -Encoding UTF8
        Add-Result 'Plugin inventory' 'Observed' '' ([string]$script:PluginsBefore.Count) 'Installed identities and enabled flags recorded locally.'
    }
    $script:Results.ToArray() | Export-Csv -NoTypeInformation -LiteralPath (Join-Path $script:RunDirectory 'preflight.csv')
    if ($PlanOnly) {
        Add-Result 'Run' 'PlanOnly' '' '' 'No apps closed, packages installed, or settings changed. Official installers were downloaded for inspection.'
    } else {
        Backup-CodexSettings $codexRoot $backup
        if ($beforeCore) {
            # A complete local copy survives installer repair of the source tree.
            $oldRoot = Split-Path -Parent (Split-Path -Parent $core)
            $previousCopy = Join-Path $backup 'previous-core'
            Copy-Item -LiteralPath $oldRoot -Destination $previousCopy -Recurse
            $copiedBinary = Join-Path $previousCopy 'bin\codex.exe'
            if ((Get-Item -LiteralPath $previousCopy).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Core backup must be an independent directory, not a link.' }
            if ((Get-FileHash -LiteralPath $copiedBinary -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $core -Algorithm SHA256).Hash) { throw 'Previous CLI backup failed verification.' }
            @("# Restore only the preceding CLI through the official installer.", '$env:CODEX_NON_INTERACTIVE = ''1''', "& powershell.exe -NoProfile -ExecutionPolicy Bypass -File '$($installer.Replace("'", "''"))' -Release '$beforeCore'", 'if ($LASTEXITCODE -ne 0) { throw ''CLI recovery failed; see retained previous-core package.'' }') |
                Set-Content -LiteralPath (Join-Path $script:RunDirectory 'Restore-PreviousCodex.ps1') -Encoding UTF8
        }
        Write-Host "`nChatGPT will close in $CloseDelaySeconds seconds. Save drafts now; Ctrl+C cancels." -ForegroundColor Yellow
        for ($n=$CloseDelaySeconds; $n -gt 0; $n--) { Start-Sleep -Seconds 1 }
        Stop-DesktopApps
        Invoke-Step 'Codex CLI update' -Mutation {
            if (-not $script:TargetCore -or -not (Test-Path $installer)) { throw 'CLI source preflight failed; see earlier diagnostic.' }
            if ($beforeCore -and [version]($beforeCore -replace '[-+].*$', '') -gt [version]$script:TargetCore) {
                Add-Result 'Codex CLI' 'Check' $beforeCore $beforeCore 'Installed stable version is newer than source; no downgrade.'
                $script:CoreReady = $true
                return
            }
            $oldNonInteractive = $env:CODEX_NON_INTERACTIVE
            $env:CODEX_NON_INTERACTIVE = '1'
            try {
                $null = Require-Success (Invoke-Tool 'powershell.exe' @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-Release', $script:TargetCore) 900 -Mutation)
                $after = Get-CoreVersion $core
                if ($after -ne $script:TargetCore) { throw 'Standalone version did not match the selected release.' }
                $script:CoreReady = $true
                Add-Result 'Codex CLI' $(if ($beforeCore -eq $after) { 'Current' } else { 'Updated' }) $beforeCore $after 'Exact standalone executable verified; prior package and settings backed up.'
            } catch {
                $failure = $_
                if ($beforeCore -and -not $script:StopMutations) {
                    $r = Invoke-Tool 'powershell.exe' @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-Release', $beforeCore) 900 -Mutation
                    $restored = Get-CoreVersion $core
                    if ($r.Code -eq 0 -and $restored -eq $beforeCore) { Add-Result 'CLI recovery' 'Restored' '' $restored 'Previous CLI restored after update failure.' }
                    else { Add-Result 'CLI recovery' 'Failed' '' $restored 'Use the retained previous-core backup and recovery script.' }
                }
                throw $failure
            } finally { $env:CODEX_NON_INTERACTIVE = $oldNonInteractive }
        }
        Invoke-Step 'ChatGPT desktop update' -Mutation {
            $before = Get-AppVersion 'OpenAI.Codex'
            if (-not $script:MsixVersion) { Update-StoreApp 'OpenAI.Codex' '9PLM9XGG6VKS'; return }
            if ($before -and [version]$before -ge [version]$script:MsixVersion) { Add-Result 'ChatGPT desktop' 'Current' $before $before 'Installed version meets or exceeds the official package.'; return }
            # No policy changes, certificate imports, sideload flags, or app reset.
            Add-AppxPackage -Path $msix -ErrorAction Stop
            $after = Get-AppVersion 'OpenAI.Codex'
            if ($after -ne $script:MsixVersion) { throw 'Appx registration did not reach the downloaded version.' }
            Add-Result 'ChatGPT desktop' 'Updated' $before $after 'Official MSIX registered successfully for the current user.'
        }
        Invoke-Step 'ChatGPT Classic update' -Mutation { Update-StoreApp 'OpenAI.ChatGPT-Desktop' '9NT1R1C2HH7J' }
        foreach ($editor in $editors) {
            Invoke-Step ("Codex extension: " + $editor.Name) -Mutation {
                if (-not $script:ExtensionStage) { throw 'Extension package preflight failed.' }
                if ([version]$editor.Before -ge [version]$script:ExtensionStage.Version) { Add-Result $editor.Name 'Current' $editor.Before $editor.Before 'Installed extension meets or exceeds stable publisher version; no downgrade.'; return }
                $null = Require-Success (Invoke-Tool $editor.Command @('--install-extension', $script:ExtensionStage.Path, '--force') 600 -Mutation)
                $after = Get-ExtensionVersion $editor.Command
                if ($after -ne $script:ExtensionStage.Version) { throw 'Extension version does not match the staged package.' }
                Add-Result $editor.Name $(if ($after -eq $editor.Before) { 'NoOffer' } else { 'Updated' }) $editor.Before $after 'Default editor profile checked; reload editor to activate.'
            }
        }
        foreach ($package in $script:NpmPackages) {
            Invoke-Step $package.Name -Mutation {
                if ($package.Before -eq $package.Target) { Add-Result $package.Name 'Current' $package.Before $package.Target 'Matches resolved stable npm version.'; return }
                if ($package.Before -match '^\d+\.\d+\.\d+$' -and [version]$package.Before -gt [version]$package.Target) { Add-Result $package.Name 'Check' $package.Before $package.Before 'Installed version is newer; no downgrade.'; return }
                $null = Require-Success (Invoke-Tool 'npm.cmd' @('install', '--global', ($package.Name + '@' + $package.Target), '--no-audit', '--no-fund') 900 -Mutation)
                $installed = (Require-Success (Invoke-Tool 'npm.cmd' @('list', '--global', '--depth=0', '--json') 120)) | ConvertFrom-Json
                $after = [string]$installed.dependencies.PSObject.Properties[$package.Name].Value.version
                if ($after -ne $package.Target) { throw 'Installed npm version does not match the planned target.' }
                Add-Result $package.Name 'Updated' $package.Before $after 'Global package verified. Previous exact version is recorded in preflight.csv.'
            }
        }
        Invoke-Step 'Plugin updates' -Mutation { Update-CuratedPlugins $core }
        Invoke-Step 'CLI health and launcher check' { Invoke-CodexHealth $core }
        if (-not $NoRestart -and -not $script:StopMutations) { Invoke-Step 'Reopen ChatGPT' { Open-UnifiedApp } }
        else { Add-Result 'Desktop restart' 'Check' '' '' 'Open ChatGPT from Start after installers have finished.' }
        Add-Result 'Provider-managed components' 'Check' '' '' 'App-bundled tools follow app/runtime updates. Browser extensions, hosted connectors, custom/pinned MCPs and named editor profiles require their own update UI or owner; no forced channel changes.'
        Add-Result 'Runtime acceptance' 'Check' '' '' 'Open a new task and verify your usual tools. Package checks do not certify runtime/OAuth parity or authorize old-version cleanup.'
    }
    }
    if (@($script:Results | Where-Object { $_.Status -in @('Failed', 'Blocked') }).Count) { $exitCode = 2 }
} catch {
    $exitCode = 1
    Write-Host ("Updater stopped: " + $_.Exception.Message) -ForegroundColor Red
    if ($script:RunDirectory -and (Test-Path $script:RunDirectory)) { Add-Result 'Updater' 'Failed' '' '' $_.Exception.Message }
} finally {
    if ($script:Lock) { $script:Lock.Dispose() }
    if ($script:RunDirectory -and (Test-Path $script:RunDirectory)) {
        Save-Results
        Write-Host "`nFinished. Review: $script:RunDirectory\results.txt" -ForegroundColor Green
        Write-Host 'Keep this folder: it contains diagnostics and recovery material. No Windows reboot was requested.'
        Start-Process notepad.exe -ArgumentList ('"' + (Join-Path $script:RunDirectory 'results.txt') + '"') -ErrorAction SilentlyContinue
    }
}
exit $exitCode
