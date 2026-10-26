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
Write-Output "ALM_QA_Automation_Daily: 평일 11:00, 이슈 수집만 실행 / $issueArgs"
Write-Output "AIRegressionAnalyzer_VXvueSpecSync: 끔 (QA_SRS_Collect 두 번째 동작으로 옮김)"
Write-Output "QA_SRS_Collect: 평일 10:20, 로그온 15분 뒤 / $collectArgs -> $syncArgs"
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
    # 이슈 수집도 PC 가 켜진 뒤로 옮긴다. SRS 수집(10:20)과 Polarion 조회가 겹치지 않게 11:00 에 돈다.
    $almTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '11:00'
    Set-ScheduledTask -TaskName $oldAlm.TaskName -TaskPath $oldAlm.TaskPath -Action $almAction -Trigger $almTrigger | Out-Null
    $changed += $oldAlm
    $syncAction = New-ScheduledTaskAction -Execute $Python -Argument $syncArgs -WorkingDirectory $ProjectRoot
    $collectAction = New-ScheduledTaskAction -Execute $Python -Argument $collectArgs -WorkingDirectory $ProjectRoot
    $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Limited
    # 담당자 PC 는 평일 10:00 에 켜진다(2026-10-26 사용자 결정). 켜진 뒤 20분에 수집하고, 끝나면 바로
    # 사양서를 서버에 올린다(REQ-SYNC-001). 부팅 직후 만회 실행은 도메인 로그온에 실패할 수 있어
    # (2026-10-26 10:01 0x8007051F) 로그온 15분 뒤에도 한 번 돈다. 같은 날 완료본은 다시 수집하지 않는다.
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '10:20'
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    $logon.Delay = 'PT15M'
    $trigger = @($weekly, $logon)
    # 시작 실패(PC 재시작 중 등)는 10분 뒤 최대 3번 다시 시도한다 (REQ-SYNC-003). 2026-10-23 09:00 이 그렇게 빠졌다.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)
    $settings.Enabled = $true
    if ($oldCollect) {
        Set-ScheduledTask -TaskName $oldCollect.TaskName -TaskPath $oldCollect.TaskPath -Action @($collectAction, $syncAction) -Principal $principal -Trigger $trigger -Settings $settings | Out-Null
        $changed += $oldCollect
    } else {
        Register-ScheduledTask -TaskName 'QA_SRS_Collect' -Action @($collectAction, $syncAction) -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    }
    # 옛 03:30 동기화 예약은 지우지 않고 끈다. 되돌릴 때는 백업 XML 이나 Enable-ScheduledTask 를 쓴다.
    Disable-ScheduledTask -TaskName $oldSync.TaskName -TaskPath $oldSync.TaskPath | Out-Null
    $changed += $oldSync
} catch {
    $failure = $_
    foreach ($task in $changed) {
        try { Set-ScheduledTask -InputObject $task | Out-Null }
        catch { Write-Warning "예약 복구 실패: $($task.TaskName). 백업: $backup" }
    }
    throw $failure
}
Get-ScheduledTask -TaskName 'ALM_QA_Automation_Daily','AIRegressionAnalyzer_VXvueSpecSync','QA_SRS_Collect' |
    Select-Object TaskName,State,@{Name='LogonType';Expression={$_.Principal.LogonType}},@{Name='Arguments';Expression={$_.Actions.Arguments}} |
    Format-List
Write-Output "예약 전환 완료. 원래 예약 백업: $backup"
