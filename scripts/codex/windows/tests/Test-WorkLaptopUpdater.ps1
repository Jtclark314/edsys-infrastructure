param([Parameter(Mandatory=$true)][string]$SourcePath)
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($SourcePath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($f in $ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)){
    . ([scriptblock]::Create($f.Extent.Text))
}
$paramProbe=[scriptblock]::Create($ast.ParamBlock.Extent.Text + '; "PARAMETERS_OK"')
if((& $paramProbe -PlanOnly) -ne 'PARAMETERS_OK'){throw 'Parameter binding failed'}
function Assert([bool]$Value,[string]$Message){if(-not $Value){throw $Message}}
function Rejects([scriptblock]$Body){try{& $Body|Out-Null}catch{return $true};return $false}
$script:RunDirectory=Join-Path ([IO.Path]::GetTempPath()) ('EdSys-updater-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $script:RunDirectory|Out-Null
$script:Results=New-Object 'Collections.Generic.List[object]'
$script:CommandNumber=0
$script:StopMutations=$false
$PlanOnly=$false
try {
    Assert ('' -eq (Quote-NativeArgument '' ).Trim('"')) 'Empty argument quoting'
    Assert ((Quote-NativeArgument 'a b') -ceq '"a b"') 'Space argument quoting'
    $identity=[pscustomobject]@{Name='OpenAI.Codex';ProcessorArchitecture='x64';Version='26.903.8094.0';Publisher='CN=fixture'}
    Assert-MsixIdentity $identity 'x64' 'CN=fixture'
    Assert (Rejects {Assert-MsixIdentity $identity 'arm64' 'CN=fixture'}) 'Wrong architecture accepted'
    Assert (Rejects {Assert-MsixIdentity $identity 'x64' 'CN=other'}) 'Wrong publisher accepted'
    $extension=[pscustomobject]@{publisher=@{publisherName='openai'};extensionName='chatgpt';versions=@(
        @{version='26.5903.99999';targetPlatform='win32-x64';properties=@(@{key='Microsoft.VisualStudio.Code.PreRelease';value='true'})},
        @{version='26.903.71938';targetPlatform='win32-x64';properties=@()},
        @{version='26.903.61454';targetPlatform='win32-x64';properties=@()},
        @{version='27.1.1';targetPlatform='win32-arm64';properties=@()}
    )}
    Assert ((Select-StableExtension $extension 'win32-x64').version -ceq '26.903.71938') 'Stable extension selection failed'
    $extension.publisher.publisherName='other'
    Assert (Rejects {Select-StableExtension $extension 'win32-x64'}) 'Wrong extension publisher accepted'
    $installed=@(
        [pscustomobject]@{pluginId='a@openai-curated';marketplaceName='openai-curated';version='1.0.0';enabled=$true},
        [pscustomobject]@{pluginId='off@openai-curated';marketplaceName='openai-curated';version='1.0.0';enabled=$false},
        [pscustomobject]@{pluginId='local@other';marketplaceName='other';version='1.0.0';enabled=$true},
        [pscustomobject]@{pluginId='new@openai-curated';marketplaceName='openai-curated';version='3.0.0';enabled=$true}
    )
    $available=@($installed|ForEach-Object{[pscustomobject]@{pluginId=$_.pluginId;version='2.0.0'}})
    $selected=@(Get-RefreshablePlugins $installed $available)
    Assert ($selected.Count -eq 1 -and $selected[0].pluginId -ceq 'a@openai-curated') 'Disabled/channel/downgrade plugin guard failed'
    $a=Get-PluginSignature $installed
    $installed[1].enabled=$true
    Assert ($a -cne (Get-PluginSignature $installed)) 'Enabled state drift was invisible'
    function Test-ReleaseFallback {
        $script:ProbeCount=0
        function Invoke-RestMethod {param($Uri,$Headers,$TimeoutSec);$script:ProbeCount++;if($script:ProbeCount -eq 1){return @{tag_name='rust-v9.0.0-beta.1';prerelease=$true}};return @{tag_name='rust-v0.154.0';prerelease=$false}}
        Assert ((Get-StableRelease) -ceq '0.154.0') 'Stable release fallback failed'
    }
    Test-ReleaseFallback
    function Test-StoreNoOffer {
        function Get-AppVersion {param($Name);return '1.0.0.0'}
        function Invoke-Tool {return @{Code=-1978335189;Text='localized vendor message';Log='fixture'}}
        Update-StoreApp 'Fixture Classic' '9NT1R1C2HH7J'
        Assert ($script:Results[-1].Status -ceq 'NoOffer') 'Store no-offer misclassified as current or failure'
    }
    Test-StoreNoOffer
    function Test-CuratedSource {
        param([string]$SourceType,[bool]$UpgradeFails=$false,[bool]$ReportedError=$false)
        $script:CoreReady=$true
        $script:PluginAdded=$false
        $script:GitCalls=0
        $script:FixtureSource=$SourceType
        $script:FixtureUpgradeFails=$UpgradeFails
        $script:FixtureReportedError=$ReportedError
        function Invoke-Tool {
            param($File,$Arguments,$TimeoutSeconds,[switch]$Mutation)
            if($Arguments[1] -eq 'marketplace' -and $Arguments[2] -eq 'list'){
                $market=[pscustomobject]@{name='openai-curated'}
                if($script:FixtureSource){$market|Add-Member marketplaceSource @{sourceType=$script:FixtureSource;source='fixture-source'}}
                return @{Code=0;Text=(@{marketplaces=@($market)}|ConvertTo-Json -Depth 8)}
            }
            if($Arguments[1] -eq 'marketplace' -and $Arguments[2] -eq 'upgrade'){
                $script:GitCalls++
                return @{Code=$(if($script:FixtureUpgradeFails){1}else{0});Text=$(if($script:FixtureReportedError){'{"errors":["fixture refresh error"]}'}else{'{}'});Log='fixture-git-error'}
            }
            if($Arguments[1] -eq 'add'){
                Assert ($Arguments[2] -ceq 'example@openai-curated') 'Unexpected plugin identity selected'
                $script:PluginAdded=$true
                return @{Code=0;Text='{"pluginId":"example@openai-curated","version":"2.0.0"}'}
            }
            if($Arguments[1] -eq 'list'){
                $version=if($script:PluginAdded){'2.0.0'}else{'1.0.0'}
                $p=@{pluginId='example@openai-curated';marketplaceName='openai-curated';version=$version;enabled=$true}
                $off=@{pluginId='disabled@openai-curated';marketplaceName='openai-curated';version='1.0.0';enabled=$false}
                $a=@{pluginId='example@openai-curated';version='2.0.0'}
                return @{Code=0;Text=(@{installed=@($p,$off);available=@($a)}|ConvertTo-Json -Depth 8)}
            }
            throw 'Unexpected fixture command'
        }
        if($UpgradeFails -or $ReportedError){
            Assert (Rejects {Update-CuratedPlugins 'fixture'}) 'Real Git upgrade failure was swallowed'
            Assert (-not $script:PluginAdded) 'Plugin install followed a failed Git refresh'
        }else{
            Update-CuratedPlugins 'fixture'
            Assert $script:PluginAdded 'Available update was skipped for built-in/local catalog'
            Assert ($script:Results[-1].Status -eq 'Verified') 'Plugin verification did not complete'
        }
        Assert ($script:GitCalls -eq $(if($SourceType -eq 'git'){1}else{0})) 'Non-Git marketplace received Git upgrade command'
    }
    Test-CuratedSource ''
    Test-CuratedSource 'local'
    Test-CuratedSource 'git'
    Test-CuratedSource 'git' $true
    Test-CuratedSource 'git' $false $true
    $doctor=@{schemaVersion=1;overallStatus='fail';codexVersion='0.154.0';checks=@{
        'installation'=@{id='installation';status='ok';summary='fixture installed'}
        'sandbox.helpers'=@{id='sandbox.helpers';status='fail';summary='fixture setup failure'}
        'security.endpoint'=@{id='security.endpoint';status='warning';summary='fixture endpoint warning'}
    }}
    Write-DoctorResults @{Code=1;Text=($doctor|ConvertTo-Json -Depth 8);Log='fixture-doctor'}
    Assert ($script:Results[-1].Status -eq 'Failed' -and $script:Results[-1].Detail -match '1 passing, 1 failing, 1 warning') 'Nonzero JSON doctor findings lost'
    Assert (@($script:Results|Where-Object {$_.Component -eq 'Doctor: sandbox.helpers' -and $_.Status -eq 'Failed'}).Count -eq 1) 'Sandbox failure was hidden'
    Assert (@($script:Results|Where-Object {$_.Component -eq 'Doctor: security.endpoint' -and $_.Status -eq 'Warning'}).Count -eq 1) 'Endpoint warning was promoted to a failure'
    Assert (Rejects {Write-DoctorResults @{Code=1;Text='not-json';Log='fixture'}}) 'Invalid doctor JSON was accepted'
    $doctor.overallStatus='ok';$doctor.checks=@{installation=@{id='installation';status='ok';summary='fixture'}}
    Write-DoctorResults @{Code=1;Text=($doctor|ConvertTo-Json -Depth 8);Log='fixture'}
    Assert ($script:Results[-1].Status -eq 'Failed') 'Unexplained exit code was hidden'
    function Test-FinishMode {
        $script:FinishCalls=New-Object 'Collections.Generic.List[string]'
        function Get-CoreVersion {return '0.154.0'}
        function Backup-CodexSettings {$script:FinishCalls.Add('backup')}
        function Update-CuratedPlugins {$script:FinishCalls.Add('plugins')}
        function Invoke-SandboxProbe {$script:FinishCalls.Add('sandbox')}
        function Invoke-CodexHealth {$script:FinishCalls.Add('health')}
        function Invoke-Tool {throw 'FinishOnly unexpectedly invoked an installer'}
        Invoke-FinishOnly 'fixture-core' 'fixture-root' 'fixture-backup'
        Assert (($script:FinishCalls -join ',') -ceq 'backup,plugins,sandbox,health') 'FinishOnly did not isolate follow-up work'
        $PlanOnly=$true
        $script:FinishCalls.Clear()
        Invoke-FinishOnly 'fixture-core' 'fixture-root' 'fixture-backup'
        Assert ($script:FinishCalls.Count -eq 0) 'FinishOnly PlanOnly performed work'
    }
    Test-FinishMode
    function Test-SandboxProbe {
        $oldLocalAppData=$env:LOCALAPPDATA
        $oldSystemRoot=$env:SystemRoot
        $env:LOCALAPPDATA=$script:RunDirectory
        $env:SystemRoot=$script:RunDirectory
        $script:ProbeSucceeds=$true
        function Invoke-Tool {
            param($File,$Arguments,$TimeoutSeconds,[switch]$Mutation,$WorkingDirectory)
            Assert ($Arguments[0] -ceq 'sandbox' -and $Arguments[1] -ceq '--') 'Probe introduced an OS subcommand or explicit-profile CLI options'
            Assert ($Arguments -notcontains '-C' -and $Arguments -notcontains '-P') 'Probe must inherit existing permissions through process cwd'
            Assert ($WorkingDirectory.StartsWith((Join-Path $script:RunDirectory 'EdSys-Sandbox-Checks'))) 'Probe used an unsafe working directory'
            Assert (Test-Path -LiteralPath $WorkingDirectory -PathType Container) 'Probe workspace missing'
            Assert ($Arguments[-1] -ceq '[Console]::WriteLine("EDSYS_SANDBOX_OK")') 'Probe command is not the harmless marker'
            Assert $Mutation.IsPresent 'Probe did not honor mutation/timeout gate'
            return @{Code=$(if($script:ProbeSucceeds){0}else{1});Text='EDSYS_SANDBOX_OK';Log='fixture-probe'}
        }
        try {
            Invoke-SandboxProbe 'fixture-core'
            Assert ($script:Results[-1].Status -eq 'Verified') 'Successful sandbox probe not recorded'
            $script:ProbeSucceeds=$false
            Assert (Rejects {Invoke-SandboxProbe 'fixture-core'}) 'Failed sandbox probe was hidden'
        } finally { $env:LOCALAPPDATA=$oldLocalAppData; $env:SystemRoot=$oldSystemRoot }
    }
    Test-SandboxProbe
    function Test-FinishDispatch {
        $FinishOnly=$true
        $script:DispatchSeen=$false
        function Invoke-FinishOnly {$script:DispatchSeen=$true}
        function Invoke-Step {throw 'FinishOnly entered the complete installer flow'}
        $dispatch=$ast.Find({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -ceq '$FinishOnly' -and $n.Extent.Text.Contains('Invoke-FinishOnly')},$true)
        Assert ($null -ne $dispatch) 'FinishOnly dispatch missing'
        & ([scriptblock]::Create($dispatch.Extent.Text))
        Assert $script:DispatchSeen 'FinishOnly dispatch did not select the targeted flow'
    }
    Test-FinishDispatch
    Invoke-Step 'expected failure' {throw 'fixture failure'}
    $script:Continued=$false
    Invoke-Step 'independent step' {$script:Continued=$true}
    Assert $script:Continued 'Independent step failed to continue'
    Assert ($script:Results[-1].Status -eq 'Failed') 'Failure not retained in report'
    $script:StopMutations=$true
    Invoke-Step 'blocked mutation' -Mutation {throw 'must not execute'}
    Assert ($script:Results[-1].Status -eq 'Blocked') 'Mutation circuit breaker failed'
    $script:StopMutations=$false
    $PlanOnly=$true
    Assert (Rejects {Invoke-Tool 'not-a-command' @() -Mutation}) 'PlanOnly allowed a mutation'
    $PlanOnly=$false
    $native=if($env:OS -eq 'Windows_NT'){Join-Path $PSHOME 'powershell.exe'}else{Join-Path $PSHOME 'pwsh'}
    $r=Invoke-Tool $native @('-NoProfile','-NonInteractive','-Command','[Console]::WriteLine("fixture output"); exit 17') 30
    Assert ($r.Code -eq 17 -and $r.Text -match 'fixture output') 'Native exit code or output lost'
    $r=Invoke-Tool $native @('-NoProfile','-NonInteractive','-Command','[Console]::WriteLine([IO.Directory]::GetCurrentDirectory())') 30 -WorkingDirectory $script:RunDirectory
    Assert ($r.Code -eq 0 -and $r.Text.TrimEnd('\','/') -eq $script:RunDirectory.TrimEnd('\','/')) 'Native child did not inherit the requested working directory'
    if($env:OS -eq 'Windows_NT'){
        # This intentionally invalid invocation stops at argument parsing and
        # does not run sandbox setup. It reproduces the 0.154.0 CLI constraint.
        $cli=Join-Path $env:USERPROFILE '.codex\packages\standalone\current\bin\codex.exe'
        if(Test-Path -LiteralPath $cli){
            $v=Invoke-Tool $cli @('--version') 30
            if($v.Text -ceq 'codex-cli 0.154.0'){
                $bad=Invoke-Tool $cli @('sandbox','-C',$script:RunDirectory,'--','powershell.exe','-NoProfile','-Command','exit 0') 30
                Assert ($bad.Code -eq 2 -and $bad.ErrorText -match 'permission-profile') 'The exact 0.154.0 argument-parser regression was not reproduced'
            }
        }
        $original=Join-Path $script:RunDirectory 'release'; New-Item -ItemType Directory -Path $original|Out-Null
        'payload'|Set-Content (Join-Path $original 'fixture.txt')
        $junction=Join-Path $script:RunDirectory 'current'; New-Item -ItemType Junction -Path $junction -Target $original|Out-Null
        $copy=Join-Path $script:RunDirectory 'previous-core'; Copy-Item -LiteralPath $junction -Destination $copy -Recurse
        Assert (-not ((Get-Item $copy).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Core backup copied a junction instead of independent files'
        Assert ((Get-Content (Join-Path $copy 'fixture.txt')) -eq 'payload') 'Core backup content missing'
        [IO.Directory]::Delete($junction)
        $cmd=Join-Path $script:RunDirectory 'with spaces.cmd'
        @('@echo off','echo %~1','exit /b 23')|Set-Content $cmd -Encoding ASCII
        $r=Invoke-Tool $cmd @('argument with spaces') 30
        Assert ($r.Code -eq 23 -and $r.Text -eq 'argument with spaces') 'CMD wrapper lost arguments or child failure'
    }
    Write-Output ('UPDATER_TESTS_OK PowerShell '+$PSVersionTable.PSVersion)
}finally{Remove-Item -LiteralPath $script:RunDirectory -Recurse -Force}
