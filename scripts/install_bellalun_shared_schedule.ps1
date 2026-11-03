param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$SkillRoot,
    [Parameter(Mandatory=$true)][string]$KnowledgeRoot,
    [Parameter(Mandatory=$true)][string]$TargetUrl,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot 'sync_bellalun_shared.py'
foreach ($path in @($Python,$script,(Join-Path $SkillRoot 'SKILL.md'),$KnowledgeRoot)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "동기화 경로에 접근할 수 없습니다: $path" }
    if ($path.Contains('"')) { throw '경로에 따옴표를 사용할 수 없습니다.' }
}
$uri = [Uri]$TargetUrl
if (-not $uri.IsAbsoluteUri -or $uri.Scheme -notin @('http','https') -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $TargetUrl -match '["\r\n]') {
    throw '서버 base URL 형식을 확인하세요. 키나 등록 번호를 넣지 않습니다.'
}
$name = 'Bellalun_SharedKnowledge_Sync'
$old = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($old -and ($old.Actions.Count -ne 1 -or $old.Actions[0].Arguments -notlike '*sync_bellalun_shared.py*')) {
    throw '같은 이름의 다른 예약이 있습니다. 기존 예약을 덮어쓰지 않습니다.'
}
$arguments = '-X utf8 "{0}" --skill-root "{1}" --knowledge-root "{2}" --upload-to "{3}"' -f $script,$SkillRoot,$KnowledgeRoot,$TargetUrl
Write-Output 'Bellalun 지식 동기화: 매일 10:10 및 사용자 로그인 때 실행, 실패 시 15분 간격 3회 재시도. 로그인된 PC에서 실행합니다.'
if ($CheckOnly) { Write-Output '경로와 예약 구성 확인 완료. 예약은 변경하지 않았습니다.'; exit 0 }
if ($old) {
    $backup = Join-Path $projectRoot 'data/bellalun_sync/backups'
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $backup ('schedule-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff') + '.xml')) -Encoding Unicode
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $projectRoot
$triggers = @((New-ScheduledTaskTrigger -Daily -At '10:10'),(New-ScheduledTaskTrigger -AtLogOn -User $identity))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null
$installed = Get-ScheduledTask -TaskName $name
if ($installed.Actions[0].Arguments -ne $arguments -or $installed.Triggers.Count -ne 2) { throw '설치 후 예약 구성이 일치하지 않습니다.' }
Write-Output 'Bellalun 예약 설치 및 재조회 완료. 기존 VXvue 예약은 유지했습니다.'
