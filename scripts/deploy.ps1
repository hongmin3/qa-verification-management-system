param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

function Invoke-SudoOverSsh {
    <#
      sudo 비밀번호를 SSH 너머 stdin 으로 넘긴다. 값은 화면·로그·명령행에 남지 않는다.

      틀리기 쉬운 곳이 두 군데다. 둘 다 실측으로 확인했다 (원격에서 `cat | od -An -tx1`
      로 실제 도착한 바이트를 찍어 비교).

        1. `$pw | ssh ...` — PowerShell 파이프가 문자열 뒤에 CRLF 를 붙인다. sudo 는 LF
           까지를 한 줄로 읽으므로 비밀번호 끝에 CR 이 남아 거부된다.
        2. `$proc.StandardInput` 은 [Console]::InputEncoding 으로 StreamWriter 를 만드는데,
           그 인코딩이 **BOM 있는 UTF-8** 이면 접근하는 순간 preamble 이 먼저 나간다.
           도착 바이트가 `efbbbf 31 0a` 가 되어 거부된다. BaseStream 에 직접 써도 소용없다
           — preamble 은 StreamWriter 가 만들어질 때 이미 나간다.

      그래서 프로세스를 띄우기 **전에** InputEncoding 을 BOM 없는 UTF-8 로 바꾸고 끝나면
      되돌린다. 같은 비밀번호가 bash 파이프로는 통과하는데 PowerShell 로만 실패한 원인이
      이것이었다 (Windows 기본 콘솔은 preamble 이 없어 환경에 따라 재현되지 않는다).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$HostTarget,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$Password
    )
    $savedEncoding = $null
    try { $savedEncoding = [Console]::InputEncoding } catch { }
    try {
        try { [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false } catch { }
        if ([Console]::InputEncoding.GetPreamble().Length -ne 0) {
            throw "stdin 인코딩에서 BOM 을 제거하지 못했습니다. 서버에서 직접 재기동하세요: sudo systemctl restart qa-verification"
        }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "ssh"
        $psi.Arguments = "$HostTarget `"sudo -S -p '' $Command`""
        $psi.RedirectStandardInput = $true
        $psi.UseShellExecute = $false
        $proc = [System.Diagnostics.Process]::Start($psi)
        # [char]10 = LF. PowerShell 에서 "\n" 은 개행이 아니라 백슬래시+n 이다.
        $proc.StandardInput.Write($Password + [char]10)
        $proc.StandardInput.Close()
        $proc.WaitForExit()
        return $proc.ExitCode
    }
    finally {
        if ($null -ne $savedEncoding) { try { [Console]::InputEncoding = $savedEncoding } catch { } }
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$deployFile = Join-Path $projectRoot ".deploy.env"
if (-not (Test-Path -LiteralPath $deployFile)) { throw ".deploy.env가 없습니다. .deploy.env.example을 복사해 설정하세요." }
$deploy = @{}
Get-Content -LiteralPath $deployFile | Where-Object { $_ -match '^[A-Z_]+=' } | ForEach-Object {
    $key, $value = $_ -split '=', 2
    $deploy[$key] = $value
}
$hostTarget = "$($deploy.DEPLOY_SSH_USER)@$($deploy.DEPLOY_SSH_HOST)"
$directory = $deploy.DEPLOY_TARGET_DIRECTORY
$target = "${hostTarget}:${directory}/"
Set-Location $projectRoot
& "$projectRoot\.venv\Scripts\python.exe" -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "테스트 실패로 배포를 중단합니다." }
ssh $hostTarget "test ! -e '$directory' || test -d '$directory'"
ssh $hostTarget "mkdir -p '$directory'"
foreach ($item in @("app", "scripts", "tests", "config", "docs", "deploy", "requirements.txt", "config.yaml", "pytest.ini", ".env.example", "secrets.example.txt", "secrets.example.json", "README.md", "SECURITY.md")) { scp -r -- "$item" $target }

Write-Host "파일 전송 완료. 서버에서 의존성 동기화 중..."
ssh $hostTarget "cd '$directory' && .venv/bin/pip install -q -r requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "서버 의존성 설치 실패." }
Write-Host "의존성 동기화 완료."

if (-not $Restart) {
    Write-Host "파일/의존성만 반영했습니다. 서비스는 재기동하지 않았습니다."
    Write-Host "서버에서 직접 'sudo systemctl restart qa-verification'을 실행하거나, '.\scripts\deploy.ps1 -Restart'로 다시 실행하세요."
    return
}

$secretsFile = Join-Path $projectRoot "secrets.txt"
if (-not (Test-Path -LiteralPath $secretsFile)) { throw "secrets.txt가 없어 -Restart를 쓸 수 없습니다 (SERVER_SUDO_PASSWORD 필요)." }
$sudoLine = Get-Content -LiteralPath $secretsFile | Where-Object { $_ -match '^SERVER_SUDO_PASSWORD=' } | Select-Object -First 1
if (-not $sudoLine) { throw "secrets.txt에 SERVER_SUDO_PASSWORD가 없어 -Restart를 쓸 수 없습니다." }
$sudoPassword = $sudoLine.Substring("SERVER_SUDO_PASSWORD=".Length)

Write-Host "qa-verification 서비스 재기동 중..."
$exitCode = Invoke-SudoOverSsh -HostTarget $hostTarget -Command "systemctl restart qa-verification" -Password $sudoPassword
if ($exitCode -ne 0) { throw "서비스 재기동 실패 (sudo 비밀번호 또는 유닛 이름을 확인하세요)." }

Write-Host "헬스체크 중..."
$healthy = $false
$lastResult = ""
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    $lastResult = ssh $hostTarget "curl -fsS http://127.0.0.1:12000/health" 2>$null
    if ($LASTEXITCODE -eq 0 -and $lastResult -match '"status"\s*:\s*"ok"') {
        $healthy = $true
        break
    }
}
if (-not $healthy) { throw "헬스체크 실패. 서버에서 확인: journalctl -u qa-verification -n 50 --no-pager" }
Write-Host "배포 및 재기동 완료: $lastResult"
