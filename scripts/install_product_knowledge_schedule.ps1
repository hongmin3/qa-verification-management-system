param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$Python = '',
    [string]$TargetUrl = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$OutputEncoding = [Console]::OutputEncoding
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$taskName = 'QA_ProductKnowledge_Sync'
$old = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not $TargetUrl -and $old) {
    $match = [regex]::Match($old.Actions[0].Arguments, '--upload-to\s+"?([^"\s]+)')
    if ($match.Success) { $TargetUrl = $match.Groups[1].Value }
}
if (-not $TargetUrl) { throw '기존 예약에서 대상 주소를 찾지 못했습니다. -TargetUrl로 지정하세요.' }
$sourceRoot = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot 'retry_product_knowledge.py'
$pythonw = Join-Path (Split-Path $Python -Parent) 'pythonw.exe'
$hiddenRunner = Join-Path $PSScriptRoot 'run_scheduled_python.py'
foreach ($file in @($Python, $pythonw, $hiddenRunner, $script)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "필요한 파일이 없습니다: $file" }
}
& $Python -X utf8 $script --project-root $ProjectRoot --product VXvue --upload-to $TargetUrl --check
if ($LASTEXITCODE -ne 0) { throw '지식 폴더 설정 확인에 실패했습니다.' }
Write-Output 'QA_ProductKnowledge_Sync: 매일 10:00 KST · 서버 화면 등록본 내려받기 후 업로드 · 미완료 시 15분마다 재시도 · 5분마다 화면 수집 요청 확인 · PC 시작 때 만회'
if ($CheckOnly) { Write-Output '계획 확인 완료. 예약은 변경하지 않았습니다.'; exit 0 }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '예약 전환에는 관리자 PowerShell이 필요합니다. -CheckOnly로 확인한 뒤 관리자에서 실행하세요.'
}
$backup = Join-Path $ProjectRoot ('backups\knowledge-schedule-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
if ($old) {
    Export-ScheduledTask -TaskName $old.TaskName -TaskPath $old.TaskPath |
        Set-Content -LiteralPath (Join-Path $backup ($taskName + '.xml')) -Encoding Unicode
}
$workerSources = @($script, $hiddenRunner, (Join-Path $sourceRoot 'app\core\knowledge_retry.py'), (Join-Path $sourceRoot 'app\core\knowledge_push.py'), (Join-Path $sourceRoot 'app\core\skill_knowledge.py'))
$fingerprint = ($workerSources | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join ''
$hasher = [Security.Cryptography.SHA256]::Create()
$workerDigest = [BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($fingerprint))).Replace('-', '').ToLower()
$hasher.Dispose()
$worker = Join-Path $ProjectRoot ('data\knowledge_retry\worker-' + $workerDigest.Substring(0, 16))
New-Item -ItemType Directory -Path $worker -Force | Out-Null
foreach ($file in $workerSources) { Copy-Item -LiteralPath $file -Destination (Join-Path $worker (Split-Path $file -Leaf)) -Force }
$installedScript = Join-Path $worker 'retry_product_knowledge.py'
$hiddenRunner = Join-Path $worker 'run_scheduled_python.py'
$workerArguments = '-X utf8 "{0}" --project-root "{1}" --product VXvue --upload-to "{2}"' -f $installedScript,$ProjectRoot,$TargetUrl
$hiddenArguments = '-X utf8 "{0}" --error-log "{1}" -- "{2}" {3}' -f $hiddenRunner,(Join-Path $ProjectRoot 'output/logs/QA_ProductKnowledge_Sync-last-error.log'),$Python,$workerArguments
$action = New-ScheduledTaskAction -Execute $pythonw -Argument $hiddenArguments -WorkingDirectory $ProjectRoot
# No RepetitionDuration means indefinite repetition; the Python state gates daily due work.
# Every five minutes for screen "지금 수집" requests (REQ-KNOW-021). No --wait-retry:
# a waiting instance would make IgnoreNew skip the following minutes.
$repeat = New-ScheduledTaskTrigger -Once -At ([datetime]::Today.AddHours(10)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$startup = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$settings.Enabled = $true
try {
    if ($old) {
        Set-ScheduledTask -TaskName $old.TaskName -TaskPath $old.TaskPath -Action $action -Trigger @($repeat,$startup) -Settings $settings | Out-Null
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType S4U -RunLevel Limited
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($repeat,$startup) -Settings $settings -Principal $principal | Out-Null
    }
} catch {
    if ($old) {
        try { Set-ScheduledTask -InputObject $old | Out-Null }
        catch { Write-Warning "예약 복구 실패. 백업을 확인하세요: $backup" }
    }
    throw
}
$installed = Get-ScheduledTask -TaskName $taskName
Write-Output "예약 전환 완료. 원래 예약 백업: $backup"
Write-Output "예약 동작: $($installed.Actions[0].Arguments)"
