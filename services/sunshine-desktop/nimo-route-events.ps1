[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\ProgramData\EdSys\Sunshine'
$taskName = 'EdSys Nimo 9950x LAN Route'
$triggerId = 'EdSysResumeAndNetwork'
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$task = $folder.GetTask($taskName)
$definition = $task.Definition
if ($definition.Principal.UserId -notin @('SYSTEM', 'S-1-5-18') -or
    $definition.Principal.LogonType -ne 5) { throw 'Expected the existing SYSTEM route task' }
if ($definition.Actions.Count -ne 1 -or
    $definition.Actions.Item(1).Arguments -notlike '*\EdSys\Sunshine\nimo-route.ps1*') {
    throw 'Unexpected route task action; refusing to modify it'
}
$backup = Join-Path $root ('route-task-before-events-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.xml')
$task.Xml | Set-Content -LiteralPath $backup -Encoding Unicode
for ($i = $definition.Triggers.Count; $i -ge 1; $i--) {
    if ($definition.Triggers.Item($i).Id -eq $triggerId) { $definition.Triggers.Remove($i) }
}
$trigger = $definition.Triggers.Create(0) # TASK_TRIGGER_EVENT
$trigger.Id = $triggerId
$trigger.Enabled = $true
$trigger.Subscription = @'
<QueryList>
  <Query Id="0" Path="System">
    <Select Path="System">*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select>
  </Query>
  <Query Id="1" Path="Microsoft-Windows-NetworkProfile/Operational">
    <Select Path="Microsoft-Windows-NetworkProfile/Operational">*[System[EventID=10000]]</Select>
  </Query>
</QueryList>
'@
$trigger.Delay = 'PT5S'
# Recheck while the adapter/DHCP/profile settles, retaining the minute fallback.
$trigger.Repetition.Interval = 'PT1M'
$trigger.Repetition.Duration = 'PT3M'
$trigger.Repetition.StopAtDurationEnd = $false
$folder.RegisterTaskDefinition($taskName, $definition, 6, 'SYSTEM', $null, 5) | Out-Null
Write-Output 'Wake and network-connect triggers installed; existing action and triggers retained'
