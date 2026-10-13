param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$Python = '',
    [string]$AlmRoot = 'C:\Users\2024980\Documents\자동화\projects\ALM-QA-Automation',
    [string]$TargetUrl = 'http://10.13.0.222:24357',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if (-not $Python) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
foreach ($file in @($Python, (Join-Path $AlmRoot 'run.py'), (Join-Path $ProjectRoot 'scripts\collect_srs_spec.py'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "필요한 파일이 없습니다: $file" }
}
& $Python (Join-Path $ProjectRoot 'scripts\collect_srs_spec.py') --check
if ($LASTEXITCODE -ne 0) { throw '수집 설정 확인에 실패했습니다.' }
$oldAlm = Get-ScheduledTask -TaskName 'ALM_QA_Automation_Daily'
$oldSync = Get-ScheduledTask -TaskName 'AIRegressionAnalyzer_VXvueSpecSync'
$oldCollect = Get-ScheduledTask -TaskName 'QA_SRS_Collect' -ErrorAction SilentlyContinue
$issueArgs = '"{0}\run.py" issues --config "{0}\apps\issue-export\config.yaml" --out "{0}\apps\issue-export\polarion_backup" --timestamp' -f $AlmRoot
$syncArgs = '"{0}\scripts\sync_vxvue_spec.py" --target-url "{1}"' -f $ProjectRoot, $TargetUrl
$collectArgs = '"{0}\scripts\collect_srs_spec.py"' -f $ProjectRoot
Write-Output "ALM_QA_Automation_Daily: 이슈 수집만 실행 / $issueArgs"
Write-Output "AIRegressionAnalyzer_VXvueSpecSync: $syncArgs"
Write-Output "QA_SRS_Collect: 평일 09:00 / $collectArgs"
if ($CheckOnly) { Write-Output '계획 확인 완료. 예약은 변경하지 않았습니다.'; exit 0 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '기존 S4U 예약을 변경하려면 관리자 PowerShell에서 실행해야 합니다.'
}
$backup = Join-Path $ProjectRoot ('backups\srs-schedule-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($task in @($oldAlm, $oldSync, $oldCollect)) {
    if ($null -ne $task) {
        Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath |
            Set-Content -LiteralPath (Join-Path $backup ($task.TaskName + '.xml')) -Encoding Unicode
    }
}
$changed = @()
try {
    $almAction = New-ScheduledTaskAction -Execute $oldAlm.Actions[0].Execute -Argument $issueArgs -WorkingDirectory $AlmRoot
    Set-ScheduledTask -TaskName $oldAlm.TaskName -TaskPath $oldAlm.TaskPath -Action $almAction | Out-Null
    $changed += $oldAlm
    $syncAction = New-ScheduledTaskAction -Execute $Python -Argument $syncArgs -WorkingDirectory $ProjectRoot
    Set-ScheduledTask -TaskName $oldSync.TaskName -TaskPath $oldSync.TaskPath -Action $syncAction | Out-Null
    $changed += $oldSync
    $collectAction = New-ScheduledTaskAction -Execute $Python -Argument $collectArgs -WorkingDirectory $ProjectRoot
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Limited
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:00'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    $settings.Enabled = $true
    if ($oldCollect) {
        Set-ScheduledTask -TaskName $oldCollect.TaskName -TaskPath $oldCollect.TaskPath -Action $collectAction -Principal $principal -Trigger $trigger -Settings $settings | Out-Null
        $changed += $oldCollect
    } else {
        Register-ScheduledTask -TaskName 'QA_SRS_Collect' -Action $collectAction -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    }
} catch {
    $failure = $_
    foreach ($task in $changed) {
        try { Set-ScheduledTask -InputObject $task | Out-Null }
        catch { Write-Warning "예약 복구 실패: $($task.TaskName). 백업: $backup" }
    }
    throw $failure
}
Get-ScheduledTask -TaskName 'ALM_QA_Automation_Daily','AIRegressionAnalyzer_VXvueSpecSync','QA_SRS_Collect' |
    Select-Object TaskName,@{Name='LogonType';Expression={$_.Principal.LogonType}},@{Name='Arguments';Expression={$_.Actions.Arguments}} |
    Format-List
Write-Output "예약 전환 완료. 원래 예약 백업: $backup"
