# 사용량 한도 증거 공유폴더 복사 예약을 이 PC 에 등록한다 (REQ-QAINTEL-039).
# 예약 이름 QA_LimitEvidence_Sync, 10분마다. "사용자가 로그온할 때만 실행"으로 등록한다.
# 이유: 공유폴더는 로그인한 Windows 계정 권한으로 써야 한다. S4U·비밀번호 저장 방식은 공유폴더에 쓸 수 없거나 비밀번호를 남긴다.
#   powershell -ExecutionPolicy Bypass -File scripts\install_limit_evidence_sync.ps1 -CheckOnly   # 계획만 보기
#   powershell -ExecutionPolicy Bypass -File scripts\install_limit_evidence_sync.ps1              # 등록
param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [int]$IntervalMinutes = 10,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$script = Join-Path $ProjectRoot 'scripts\sync_limit_evidence.ps1'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "필요한 파일이 없습니다: $script" }
$taskName = 'QA_LimitEvidence_Sync'
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Output "${taskName}: ${IntervalMinutes}분마다 / 사용자 $user (로그온했을 때만) / powershell $arguments"
if ($CheckOnly) { Write-Output '계획 확인 완료. 예약은 변경하지 않았습니다.'; exit 0 }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "등록 완료: $taskName. 기록은 %LOCALAPPDATA%\qa-limit-evidence\sync.log 에 남습니다."
