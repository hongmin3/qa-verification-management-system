# VXvue 일일 QA 점검 (`daily_qa`)

> 상위 문서: [README](../../README.md) · [문서 지도](../README.md) · 사양: [SPEC](../../SPEC.md) `REQ-DAILY-*`, `NFR-SEC-001` · 보안: [AI 점검 보안 통제](../SECURITY_AI_AGENT.md)

운영 서버가 평일 아침마다 스스로 돌리는 VXvue QA 점검이다. Polarion 에서 사양(SRS)과 이슈를 읽고,
수집된 TC·매뉴얼과 비교해 사람이 검토할 초안을 만든다. 사람은 요약 메일을 받고 `/daily-qa`
화면에서 승인·거절한다. 화면 사용법은 앱 안 `/daily-qa/guide` 에 있다.

## 구조

```flow
앱 내장 스케줄러(평일 07:30) -> 분리 프로세스 scripts/run_daily_qa.py -> Polarion 읽기(GET) -> SRS 스냅샷·비교
SRS 스냅샷·비교 -> 작업 묶음(B·C·F) -> Claude CLI(격리 작업 폴더, Skill) -> 결과 JSON 검증 -> SQLite(daily_qa_*)
SRS 스냅샷·비교 -> 추적 공백 계산(E, AI 없음) -> SQLite(daily_qa_*)
SQLite(daily_qa_*) -> 요약 메일
SQLite(daily_qa_*) -> /daily-qa 검토 화면 -> 사람의 승인·거절
```

| 파일 | 역할 |
|---|---|
| `app/modules/daily_qa/pipeline.py` | 실행 순서 (REQ-DAILY-001) |
| `app/modules/daily_qa/polarion.py` | 읽기 전용 Polarion 클라이언트 — GET 만 있다 |
| `app/modules/daily_qa/srs_snapshot.py` | 스냅샷 저장·비교 (`data/daily_qa/snapshots/`) |
| `app/modules/daily_qa/tc_index.py` | TC Excel 의 SRS 번호 열 색인 |
| `app/modules/daily_qa/packages.py` | B·C·F 작업 묶음, 삭제 SRS 참조, 추적 공백(E) |
| `app/modules/daily_qa/workspace.py` | 격리 작업 폴더 준비, 입력 마스킹 |
| `app/modules/daily_qa/agent_runner.py` | `claude -p` 실행 (도구·설정 제한) |
| `app/modules/daily_qa/schema.py` | 결과 JSON 검증 (근거 위치 필수, 금지 조치 차단) |
| `app/modules/daily_qa/checklist_xlsx.py` | 영향성평가 Checklist 형식 초안 Excel |
| `app/modules/daily_qa/skills/` | Claude Skill 5개 (작업 폴더로 매번 복사된다) |
| `app/modules/daily_qa/router.py` | `/daily-qa` 검토 화면 |
| `app/modules/daily_qa/scheduled_jobs.py` | 예약 실행 (앱 내장 스케줄러가 분리 프로세스로 띄운다) |

## Skill

| Skill | 단계 | 무인 실행 |
|---|---|---|
| `vxvue-qa-rules` | 공통 규칙·Gate(§76 기준 G1~G7)·결과 형식 | 다른 Skill 이 먼저 읽는다 |
| `vxvue-spec-change-impact` | B 사양 변경 → TC 영향 | O |
| `vxvue-issue-verification` | C 이슈 → 수정확인·Regression 초안 | O |
| `vxvue-manual-completeness` | F 매뉴얼 누락 후보 | O |
| `vxvue-trace-gap` | E 후속 확인 (Legacy 번호가 달라 못 찾은 TC) | X (대화형) |

QA 규칙 원문은 저장소에 넣지 않는다. 지식 폴더에서 수집된 사본(`data/product_knowledge/vxvue/`)을
실행마다 작업 폴더 `rules/` 로 복사한다. Skill 은 원문의 절 번호만 가리킨다.

규칙이 새 판(Rev1.18 등)으로 바뀌면 AI 단계가 멈추고 메일 첫 줄에 알린다 (REQ-DAILY-010).
Skill 을 새 판에 맞춰 검토·수정한 뒤 `app/modules/daily_qa/rules.py` 의 `SUPPORTED_RULES_REV` 를 올린다.

## 서버 설치

한 번만 한다. 명령은 서버의 앱 사용자로 실행한다(표시한 곳만 `sudo`).

1. **Claude CLI 설치** (Linux 네이티브 설치). 설치 후 `claude --version` 으로 확인한다.
2. **토큰 발급** — 브라우저가 있는 PC 에서 회사 Claude **Team 계정**으로 로그인한 뒤
   `claude setup-token` 을 실행한다. 출력된 토큰(유효기간 1년)을 서버 `secrets.txt` 의
   `CLAUDE_CODE_OAUTH_TOKEN=` 에 넣는다. 서버에서 `claude login` 을 하지 않는다.
3. **Polarion** — **읽기 권한만 있는 계정**으로 PAT 를 발급해 `POLARION_TOKEN=` 에, 사내
   Polarion 주소를 `POLARION_HOST=` 에 넣는다.
4. **메일 수신자** — `DAILY_QA_EMAIL_TO=` (비우면 `NOTIFY_EMAIL_TO`). SMTP 는 기존 알림 설정을 쓴다.
5. **작업 폴더** — 기본값 `~/.qa-daily-workspace`. 저장소 밖이고 상위 폴더에 `CLAUDE.md` 가
   없어야 한다. 다른 곳을 쓰려면 `config.yaml` 의 `daily_qa.workspace_dir`.
6. **메일 링크** — `config.yaml` 의 `daily_qa.review_base_url` 에 사용자가 여는 주소(예:
   `http://<서버주소>`)를 넣는다.
7. **점검**:

```bash
.venv/bin/python scripts/run_daily_qa.py --check
```

모든 줄이 `[OK]` 여야 한다. QA 규칙 판이 `확인 필요` 면 담당자 PC 에서 지식 폴더 동기화
(`scripts/sync_product_knowledge.py --upload-to ...`)로 최신 규칙(Rev1.17)을 서버에 올린다.

8. **시험 실행** — Claude 를 부르지 않는 dry-run, 그다음 메일 없이 정식 1회:

```bash
.venv/bin/python scripts/run_daily_qa.py --dry-run --no-email
```

```bash
.venv/bin/python scripts/run_daily_qa.py --weekly --no-email
```

첫 실행은 SRS 기준 스냅샷만 저장하므로 B 가 `건너뜀` 인 것이 정상이다. `/daily-qa` 에서 결과를 본다.

9. **예약** — 따로 등록할 것이 없다. 앱(`qa-verification.service`)을 재시작하면 내장 스케줄러가
   평일 07:30(한국 시간)에 점검을 분리된 프로세스로 띄운다. 요일·시각은 `config.yaml` 의
   `daily_qa.schedule`. Polarion·Claude 자격증명이 없으면 그 시각에 건너뛴다. 앱 로그
   `output/logs/app.log` 에 `scheduled_job_registered id=daily_qa_vxvue` 가 보이면 등록된 것이다.

10. (선택) 서버의 Claude 를 이 점검 전용으로만 쓴다면 `deploy/claude/managed-settings.json` 을
    `/etc/claude-code/managed-settings.json` 으로 복사한다. 서버의 모든 Claude 사용에 명령 실행·웹
    조회 금지가 강제된다.

## 운영

| 확인할 것 | 방법 |
|---|---|
| 예약 등록 여부 | `output/logs/app.log` 의 `scheduled_job_registered id=daily_qa_vxvue` |
| 마지막 실행 로그 | `output/logs/daily_qa.out`, `app.log` 의 `daily_qa_launched` / `daily_qa_skipped` |
| 실행별 상세 | `/daily-qa/runs/<실행ID>` · `output/daily_qa/<실행ID>/audit.json` |
| AI 에 보낸 입력 | `output/daily_qa/<실행ID>/sent/*.json` (마스킹 후 원본 그대로) |
| 토큰 만료 | 발급일 + 1년. 만료 30일 전에 2단계를 다시 한다 |

종료 코드: `0` 성공, `1` 일부·전체 실패, `2` 설정 오류, `3` 다른 실행이 진행 중.

## 알려진 제한

- TC 의 옛 Legacy SRS 번호와 현재 `oldId` 가 대부분 맞지 않아(2026-09-28 기준 317종 중 239종),
  E 의 `TC 없음` 에 실제로는 TC 가 있는 SRS 가 섞인다. `vxvue-trace-gap` 으로 대화형 확인한다.
- 새 이슈 조회식(`daily_qa.polarion.issue_query`)은 잠정값이다 (SPEC §13).
- Codex 교차 검증(G7)은 이후 고도화 범위다.
