# Claude 사용량 한도 증거를 서버에서 받아 팀 공유폴더에 복사한다 (REQ-QAINTEL-039).
# 담당자 PC 의 Windows 예약 작업이 10분마다 실행한다(scripts/install_limit_evidence_sync.ps1).
# 공유폴더는 지금 로그인한 Windows 계정 권한으로 쓴다. 비밀번호를 어디에도 저장하지 않는다.
# 공유폴더에 같은 이름의 파일이 이미 있으면 건너뛴다. 여러 번 실행해도 결과가 같다.
param(
    [string]$TargetUrl = 'http://10.13.0.222:24357',
    [string]$ShareDir = '\\10.1.1.100\부서 내 공유폴더\2. 부서\의료영상개발품질팀\99. AI 사용량 한도 초과',
    [string]$LogFile = (Join-Path $env:LOCALAPPDATA 'qa-limit-evidence\sync.log')
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path (Split-Path $LogFile -Parent) | Out-Null
function Write-Log([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}
try {
    if (-not (Test-Path -LiteralPath $ShareDir -PathType Container)) { throw "공유폴더에 접근할 수 없습니다: $ShareDir" }
    $list = Invoke-RestMethod -Uri "$TargetUrl/claude/limit-evidence.json" -TimeoutSec 20
    $copied = 0
    foreach ($item in @($list.items)) {
        foreach ($name in @($item.name, $item.text)) {
            if (-not $name) { continue }
            $target = Join-Path $ShareDir $name
            $temp = Join-Path $env:TEMP ('limit-evidence-' + [guid]::NewGuid().ToString('N'))
            $partial = Join-Path $ShareDir ('.limit-evidence-' + [guid]::NewGuid().ToString('N') + '.tmp')
            try {
                Invoke-WebRequest -Uri ("$TargetUrl/claude/limit-evidence/files/" + [uri]::EscapeDataString($name)) -OutFile $temp -UseBasicParsing -TimeoutSec 60
                $expectedHash = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
                if ((Test-Path -LiteralPath $target -PathType Leaf) -and (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $expectedHash) { continue }
                Copy-Item -LiteralPath $temp -Destination $partial
                if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $expectedHash) { throw "복사한 파일의 해시가 다릅니다: $name" }
                Move-Item -LiteralPath $partial -Destination $target -Force
                $copied += 1
                Write-Log "복사: $name"
            } finally {
                Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if ($copied -eq 0) { Write-Log "새 증거 없음 (서버 목록 $(@($list.items).Count)건)" }
    exit 0
} catch {
    Write-Log "[실패] $($_.Exception.Message)"
    exit 1
}
