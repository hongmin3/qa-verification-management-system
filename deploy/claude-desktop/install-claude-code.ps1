# VXvue AI connection for Claude Code CLI (REQ-VXVUECHATBOT-014).
# The server fills the placeholders on every download: server address, a one-time ticket and the managed Skill.
# Runs on Windows PowerShell 5.1 through `irm <url> | iex`, so it never calls `exit` (that would close the window).
& {
    $Server = '__SERVER__'
    $Ticket = '__TICKET__'
    $SkillBase64 = '__SKILL_BASE64__'
    $BridgeVersion = '__VERSION__'
    $ErrorActionPreference = 'Continue'
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $utf8 = New-Object Text.UTF8Encoding $false

    function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
    function Fail($text) { Say "[중단] $text" 'Red'; Say "설치 안내: $Server/vxvue-desktop" 'Yellow' }
    function ServerError($err) {
        try { $body = $err.ErrorDetails.Message | ConvertFrom-Json; if ($body.detail) { return "$($body.detail) [$($body.code)]" } } catch {}
        return $err.Exception.Message
    }

    Say "VXvue AI를 Claude Code CLI에 연결합니다. 서버: $Server" 'Cyan'
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) { Fail 'claude 명령을 찾지 못했습니다. Claude Code CLI를 설치하고 새 PowerShell 창에서 다시 실행하세요.'; return }

    # 1. Reuse this PC's key (shared with the Claude Desktop extension) or enroll with the one-time ticket.
    $stateDir = if ($env:VXVUE_AI_HOME) { $env:VXVUE_AI_HOME } else { Join-Path $env:USERPROFILE '.vxvue-ai' }
    $store = Join-Path $stateDir 'credentials.json'
    $all = @{}
    if (Test-Path $store) {
        try {
            $raw = [IO.File]::ReadAllText($store, $utf8).TrimStart([char]0xFEFF)
            ($raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $all[$_.Name] = $_.Value }
        } catch { Say '저장된 연결 정보 파일을 읽지 못해 새로 등록합니다.' 'Yellow' }
    }
    $token = $null
    $saved = $all[$Server]
    if ($saved -and $saved.token -and $saved.token.Length -ge 32) {
        try {
            $ping = '{"jsonrpc":"2.0","id":1,"method":"ping"}'
            Invoke-RestMethod -Method Post -Uri "$Server/vxvue-desktop/mcp" -ContentType 'application/json' -Body $ping `
                -Headers @{ Authorization = "Bearer $($saved.token)" } -TimeoutSec 30 | Out-Null
            $token = $saved.token
            Say '이 PC에 이미 등록된 연결 키를 사용합니다.' 'Green'
        } catch {
            Say "저장된 연결 키를 쓸 수 없어 다시 등록합니다: $(ServerError $_)" 'Yellow'
        }
    }
    if (-not $token) {
        $body = @{ ticket = $Ticket; client = 'claude-code'; hostname = $env:COMPUTERNAME; username = $env:USERNAME; bridge_version = $BridgeVersion } | ConvertTo-Json -Compress
        try {
            $reply = Invoke-RestMethod -Method Post -Uri "$Server/vxvue-desktop/enroll" -ContentType 'application/json; charset=utf-8' `
                -Body ($utf8.GetBytes($body)) -TimeoutSec 30
        } catch { Fail "이 PC를 QA 서버에 등록하지 못했습니다: $(ServerError $_)"; return }
        $token = $reply.token
        $all[$Server] = [ordered]@{ device_id = $reply.device_id; token = $token; client = 'claude-code'; enrolled_at = (Get-Date).ToUniversalTime().ToString('o') }
        New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
        [IO.File]::WriteAllText($store, ($all | ConvertTo-Json -Depth 5), $utf8)
        Say "이 PC를 등록했습니다(device $($reply.device_id))." 'Green'
    }

    # 2. Register the HTTP MCP connection in the user's Claude Code settings (replacing an older one).
    & claude mcp remove vxvue-ai -s user *> $null
    $added = & claude mcp add --transport http --scope user vxvue-ai "$Server/vxvue-desktop/mcp" `
        --header "Authorization: Bearer $token" --header "X-VXvue-Bridge: $BridgeVersion" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $reason = (($added | ForEach-Object { "$_" }) -join ' ') -replace [regex]::Escape($token), '***'
        Fail "claude mcp add가 실패했습니다(종료 코드 $LASTEXITCODE): $reason"; return
    }
    Say "Claude Code에 MCP 연결 vxvue-ai(사용자 범위)를 등록했습니다." 'Green'

    # 3. Install the managed Skill unless a different Skill already uses the name.
    $configDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $env:USERPROFILE '.claude' }
    $skillDir = Join-Path $configDir 'skills\vxvue-ai'
    $skillFile = Join-Path $skillDir 'SKILL.md'
    $managed = $true
    if (Test-Path $skillFile) { $managed = ([IO.File]::ReadAllText($skillFile, $utf8)) -match 'vxvue-ai-managed' }
    if ($managed) {
        New-Item -ItemType Directory -Force -Path $skillDir | Out-Null
        [IO.File]::WriteAllBytes($skillFile, [Convert]::FromBase64String($SkillBase64))
        Say "Skill을 설치했습니다: $skillFile" 'Green'
    } else {
        Say "같은 이름의 다른 Skill이 있어 덮어쓰지 않았습니다: $skillFile" 'Yellow'
        Say 'MCP 도구는 그대로 쓸 수 있습니다. 기존 Skill을 바꾸려면 직접 백업한 뒤 이 명령을 다시 실행하세요.' 'Yellow'
    }

    Say ''
    Say '완료. Claude Code를 새로 시작한 뒤 예: "VXvue AI로 최신 지침을 읽고 VP-411을 분석해줘"' 'Cyan'
    Say '처음 도구를 쓸 때 Claude Code가 vxvue-ai 도구 사용 승인을 물으면 허용하세요(읽기 전용 도구입니다).' 'Cyan'
}
