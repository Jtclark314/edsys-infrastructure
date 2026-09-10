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
    if($env:OS -eq 'Windows_NT'){
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
