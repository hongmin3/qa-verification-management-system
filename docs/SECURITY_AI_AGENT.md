# AI 점검 보안 통제 (QA Agent 점검)

> 상위 문서: [문서 지도](README.md) · 기능: [QA Agent 점검](modules/daily-qa.md) · 사양: [SPEC](../SPEC.md) `NFR-SEC-001`, `REQ-DAILY-002`, `REQ-DAILY-007`, [QA Intelligence](../specs/qa-intelligence.md) `REQ-QAINTEL-017`

사내 사양서·이슈를 외부 AI(Claude)로 보내는 자동 점검의 보안 통제 목록이다. 항목마다
**무엇으로 막는지**와 **어떻게 확인하는지**를 함께 적는다. 정보보안 검토를 받을 때 이 문서와
확인 결과를 함께 낸다.

## 1. 통제 목록

| # | 통제 | 구현 | 확인 방법 | 확인 결과 |
|---|---|---|---|---|
| 1 | 계약·약관 | 회사 Claude **Team 계정**(상용 약관)으로 발급한 토큰만 쓴다. 개인 구독 금지 | 계약서·약관 확인, 정보보안팀 승인 | 확인 필요 (담당: QA) |
| 2 | 데이터 범위 | 작업 입력에는 변경분과 후보 TC 만 넣는다. 다만 연관 사양·후보 밖 TC 를 찾도록 작업 폴더에 오늘 SRS 전체·TC 전체 색인·매뉴얼 텍스트(마스킹본)를 두고, Claude 가 읽은 부분이 나간다. 첨부 이미지·PDF/Excel 원본 파일은 두지 않는다 | `sent/` 에서 작업 입력을, `claude_logs/` 에서 Claude 가 읽고 검색한 파일·검색어를 확인 | 자동 테스트. 실제 CLI 의 도구 기록은 서버 첫 실행에서 확인 필요 |
| 3 | 마스킹 | 작업 폴더에 쓰기 전 `app/core/security_filter.py` 로 메일·사내 경로 등을 가린다. SRS/Issue ID·버전은 남긴다 | `tests/test_daily_qa_runner.py` `test_task_input_is_masked_before_it_reaches_the_workspace` | 통과, 마스킹 제거 시 실패 확인 |
| 4 | 도구 제한 | Claude 에게 읽기·검색·결과 폴더 쓰기만 허용. 명령 실행(Bash)·웹 조회·MCP 금지. 허용 목록 밖은 묻지 않고 거부(`dontAsk`) | `test_command_restricts_tools_settings_and_mcp` | 통과, Bash 추가 시 실패 확인 |
| 5 | 설정 격리 | `--setting-sources project`: 서버 사용자 홈의 설정·hook 을 읽지 않는다. `--strict-mcp-config`, `--no-session-persistence` | 같은 테스트 | 통과 |
| 6 | 작업 폴더 격리 | 저장소 밖, 상위 폴더에 `CLAUDE.md`/`AGENTS.md` 가 없는 곳만 허용 (개발 지침이 섞이지 않게). 제품마다 그 아래 `<slug>/` 를 따로 써서 제품 자료가 섞이지 않는다 | `test_workspace_inside_repo_is_refused`, `test_workspace_under_folder_with_agent_doc_is_refused` | 통과, 검사 제거 시 실패 확인 |
| 7 | 비밀값 격리 | Claude 프로세스에는 `CLAUDE_CODE_OAUTH_TOKEN` 과 원격 측정 차단 값만 넘긴다. Gemini 키·SMTP·Polarion 토큰은 넘기지 않는다. 오류 메시지에서 토큰 문자열을 지운다 | `test_env_passes_only_token_and_quiet_flags`, `test_runner_hides_token_in_errors_and_records_usage` | 통과 |
| 8 | 외부 통신 최소화 | `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_AUTOUPDATER` | 같은 테스트 | 통과 |
| 9 | Polarion 읽기 전용 | 클라이언트에 GET 외의 요청 함수가 없다. 토큰도 읽기 권한 계정으로 발급 | `test_client_module_only_issues_get_requests` (소스 검사) | 통과 |
| 10 | 사람 판단 | 모든 결과는 참고 초안이다. Polarion·원본 TC 에 옮기는 일은 사람이 한다. 자동 Close·TC 덮어쓰기 경로가 없고, 그런 조치를 담은 Finding 은 버린다 (QA 규칙 §55) | `test_rule_breaking_findings_are_rejected_with_reason` | 통과 |
| 11 | 근거 없는 판정 차단 | 근거 위치(SRS ID·시트/행·절)가 없는 Finding 은 저장하지 않는다. 결과의 번호·근거가 실제 자료(스냅샷·작업 입력)에 없으면 그 번호를 뺀다 (REQ-QAINTEL-017, `tests/test_qa_intel_validation.py`) | 같은 테스트 | 통과, 검사 제거 시 실패 확인 |
| 12 | 감사 기록 | 실행마다 보낸 입력(`sent/`), 받은 결과, 작업별 소요 시간·사용량(`audit.json`), Claude 가 부른 도구와 대상(`claude_logs/`, 쓴 내용은 길이만), 실패 이유 문장을 남긴다 | `test_full_run_uses_legacy_snapshot_and_creates_findings_draft_and_email`, `test_claude_tool_logs_are_collected_into_the_run_folder`, `test_stream_output_records_tool_calls_without_written_content`, `test_usage_limit_is_a_failure_with_its_reason_recorded` | 통과, 도구 기록을 빼면 실패 확인 |
| 13 | 공개 저장소 보호 | 산출물(`data/daily_qa/`, `output/daily_qa/`)은 `.gitignore`. Polarion 주소·토큰은 `secrets.txt`. QA 규칙 원문은 커밋하지 않는다 | `git check-ignore` | 확인 |
| 14 | 서버 권한 | 점검은 앱과 같은 일반 사용자로 돈다(sudo 없음). (선택) `/etc/claude-code/managed-settings.json` 으로 서버의 모든 Claude 사용에 명령 실행·웹 조회 금지를 강제 | 서버에서 `id`, `cat /etc/claude-code/managed-settings.json` | 서버 설치 후 확인 필요 |
| 15 | 네트워크 | 서버 방화벽·프록시에서 Claude 용 나가는 주소를 Anthropic API 로 한정 | 서버 네트워크 설정 | 확인 필요 (담당: 서버 관리자) |
| 16 | 토큰 수명 | 토큰 유효기간 1년. 발급일을 기록하고 만료 30일 전 재발급 | 운영 달력 | 확인 필요 |

"실패 확인"은 해당 코드를 일부러 망가뜨렸을 때 테스트가 실제로 실패하는 것을 본 항목이다
(2026-09-28, 변이 검사 5건).

## 2. 보안 검토에 낼 때

"보안을 최대한 고려했다"고 말하려면 아래 네 가지가 모두 있어야 한다.

1. 1번(회사 Team 계정·상용 약관)과 정보보안팀 승인 기록.
2. 위 표의 자동 테스트 실행 결과:

```bash
python -m pytest tests/test_daily_qa_runner.py tests/test_daily_qa_polarion.py tests/test_daily_qa_outputs.py -q
```

3. 실제 한 번 실행한 뒤의 `output/daily_qa/<실행ID>/sent/` 와 `claude_logs/` 표본. 무엇을 넘겼고 Claude 가
   무엇을 더 읽었는지 직접 보여 준다.
4. 14~16번(서버·네트워크·토큰)의 서버 쪽 확인 결과.

## 3. 남은 위험

- 마스킹은 규칙 기반이라 새 형태의 개인정보는 걸러지지 않을 수 있다. 사양·이슈 본문 자체는 사내
  기술 정보로서 그대로 나간다. 1번 계약 조건이 이 위험을 다룬다.
- 작업 폴더에 SRS·TC 전체 색인이 있어, 작업 하나가 보내는 양은 Claude 가 얼마나 검색하느냐에 따라
  달라진다. 양을 고정하려면 작업 폴더에 후보만 두면 되지만, 그만큼 후보 밖 TC 를 찾는 능력이 약해진다.
  어느 쪽으로 할지는 결정 대기다.
- Claude 가 허용된 결과 폴더 안에서 틀린 판정을 쓰는 것은 막지 못한다. 형식 검증(근거 위치·판정 값)과
  사람 검토가 이를 다룬다.
