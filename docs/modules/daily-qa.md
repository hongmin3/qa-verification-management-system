# QA Intelligence Agent 엔진 (`daily_qa`)

> 상위 문서: [README](../../README.md) · [문서 지도](../README.md) · 사양: [QA Intelligence 사양](../../specs/qa-intelligence.md) `REQ-QAINTEL-*`, [SPEC](../../SPEC.md) `REQ-DAILY-*`, `NFR-SEC-001` · 보안: [AI 점검 보안 통제](../SECURITY_AI_AGENT.md) · 새 제품: [제품 추가](../PRODUCT_ONBOARDING.md)

운영 서버가 평일 아침마다 스스로 돌리는 **변경 탐지 기반 QA 점검**이다. Polarion 에서 SRS 와 이슈를
**전부** 읽어 어제 스냅샷과 비교하고, 바뀐 것(Change Event)만 골라 Claude Skill 로 분석한다.
바뀐 것이 없으면 Claude 를 한 번도 부르지 않는다. 결과는 `/qa-agent` 대시보드와 요약 메일로 본다.
사람의 승인·거절 화면은 없다. 결과는 참고 초안이고, Polarion·원본 TC 에 옮기는 일은 사람이 한다.

엔진 코드는 `app/modules/daily_qa/` 에, 화면은 `app/modules/qa_agent/` 에 있다. 제품 이름으로 갈라지는
코드는 없다. 제품 차이는 `config/products/<slug>.yaml` 의 `alm:`·`qa_intelligence:` 와 제품 규칙 Skill 에만 있다.

## 구조

```flow
앱 내장 스케줄러(제품마다 평일 07:30, 공휴일 건너뜀) -> 분리 프로세스 scripts/run_daily_qa.py --product <제품>
대시보드 [지금 실행] -> 분리 프로세스 scripts/run_daily_qa.py --product <제품>
분리 프로세스 -> 제품 잠금 -> Polarion 읽기(GET) -> SRS·이슈 스냅샷 -> 어제 스냅샷과 비교 -> Change Event 저장
Change Event 저장 -(분석이 필요한 이벤트 없음)-> NO_CHANGE(Claude 0회)
Change Event 저장 -> 분석 5종 작업 묶음 -> Claude CLI(제품별 격리 작업 폴더, Skill) -> 근거 검증 -> Finding 저장
Finding 저장 -> 초안 Excel(FIXED 이슈·사양 변경) -> 요약 메일
Finding 저장 -> /qa-agent 대시보드·기간 조회·Finding 상세
```

| 파일 | 역할 |
|---|---|
| `app/modules/daily_qa/pipeline.py` | 실행 순서, 기간 실행, 한도 중단, 단계 상태 (REQ-QAINTEL-006·027) |
| `app/modules/daily_qa/issue_audit.py` | 이슈 기록이 없는 기간의 현재 상태 점검: 묶음 나누기, 요약 카드, SRS 단위 작업 묶기 (REQ-QAINTEL-030) |
| `app/modules/daily_qa/product_adapter.py` | 제품 설정 → 공통 모델(`ProductProfile`), 필드 이름·연구소 결과 값 변환 (REQ-QAINTEL-002) |
| `app/modules/daily_qa/polarion.py` | 읽기 전용 Polarion 클라이언트 — GET 만 있다 |
| `app/modules/daily_qa/collector.py` | SRS·이슈 전체 수집, 이슈 댓글 읽기·미룸 (REQ-QAINTEL-003·004) |
| `app/modules/daily_qa/snapshots.py`, `srs_snapshot.py` | 제품별 스냅샷 저장·비교·되돌리기 |
| `app/modules/daily_qa/change_events.py` | 이벤트 11종 감지, 분석 종류 정하기, 지문 (REQ-QAINTEL-005·010) |
| `app/modules/daily_qa/intelligence.py` | 분석별 작업 입력(후보 압축: Exact → BM25) (REQ-QAINTEL-011~016) |
| `app/modules/daily_qa/evidence_validation.py` | 결과의 번호·근거가 실제 자료에 있는지 확인 (REQ-QAINTEL-017) |
| `app/modules/daily_qa/claude_limits.py` | Claude 한도·인증 실패 문장 읽기, 초기화 시각 (REQ-QAINTEL-025) |
| `app/modules/daily_qa/holidays.py` | 공휴일 표(`config/holidays/kr.yaml`) 읽기 (REQ-QAINTEL-001) |
| `app/modules/daily_qa/tc_index.py`, `packages.py` | TC 색인, 사양–TC 연결 점검(AI 없음) |
| `app/modules/daily_qa/workspace.py`, `agent_runner.py` | 격리 작업 폴더·마스킹, `claude -p` 실행 |
| `app/modules/daily_qa/schema.py` | 결과 JSON 형식 검사 |
| `app/modules/daily_qa/checklist_xlsx.py` | 제품 Checklist 형식 초안 Excel (REQ-QAINTEL-018) |
| `app/modules/daily_qa/scheduled_jobs.py` | 제품별 예약 `qa_agent_<slug>`, 한도 catch-up 감시 |
| `app/modules/daily_qa/router.py` | 옛 `/daily-qa/*` 주소를 새 주소로 307 연결 (메일에 남은 링크용) |
| `app/modules/qa_agent/dashboard.py`, `router.py` | `/qa-agent` 대시보드·상태 JSON·[지금 실행]·기간 조회·상세 |

## 제품별로 나뉘는 것

| 대상 | 위치 |
|---|---|
| 스냅샷 | `data/daily_qa/snapshots/<slug>/srs/`, `.../<slug>/issues/` |
| 잠금 | `data/daily_qa/<slug>/run.lock` |
| 상태 값 | `daily_qa_state` 의 `<slug>:` 로 시작하는 키 (예: `vxvue:claude_limit`) |
| 작업 폴더 | `<daily_qa.workspace_dir>/<slug>/` |
| 실행 ID | `YYYYMMDD-HHMMSS-<slug>` |
| 이벤트·Finding·실행 기록 | 같은 DB 표, `product` 열로 구분 |

개편 전 기록(`product` 가 빈 값)과 옛 스냅샷 위치(`data/daily_qa/snapshots/*.json`)는 `daily_qa.product`
(VXvue) 것으로 읽는다. 옮기거나 지우지 않는다.

## 분석 5종과 Skill

| 분석 | 대상 이벤트 | Skill |
|---|---|---|
| 신규 이슈 분석 | 새로 등록된 이슈, 처리 전 이슈의 본문·재현 절차 변경 | `qa-new-issue-analysis` |
| 수정 완료 이슈 분석 | 연구소 결과 FIXED 이슈의 변경, FIXED 이슈의 의미 있는 새 댓글 | `qa-fixed-issue-analysis` |
| Spec 판정 이슈 분석 | 연구소 결과가 사양대로·결함 아님인 이슈의 변경 | `qa-spec-decision-analysis` |
| 새 댓글 분석 | 진행 상태 알림이 아닌 새 댓글 | `qa-comment-analysis` |
| 사양 변경 요약 | 새 SRS·바뀐 SRS | `qa-spec-change-summary` |
| TC 점검 | 사람이 사양 변경 상세에서 요청 | `qa-spec-coverage-analysis` |
| 검증 TC 초안 | 사람이 수정 완료 이슈 상세에서 요청 | `qa-verification-tc-draft` |

상태만 바뀐 이슈, 심각도·버전 같은 속성만 바뀐 이슈, 없어진 SRS·이슈는 기록만 남기고 AI 를 부르지 않는다.

| 공통·제품 Skill | 역할 |
|---|---|
| `qa-common-rules` (+`references/output-contract.md`) | 모든 분석이 먼저 읽는 공통 규칙·결과 형식 |
| `qa-manual-completeness` | 사람이 CLI로 요청하는 매뉴얼 누락 후보 점검. 예약 실행에는 포함하지 않는다 |
| `qa-trace-gap` | 사양–TC 연결 점검의 대화형 후속 확인 (무인 실행 아님) |
| `config/products/vxvue/skills/vxvue-qa-rules/` | VXvue 제품 규칙·검증 관문(G1~G7, `references/gates.md`) |

QA 규칙 원문은 저장소에 넣지 않는다. 지식 폴더에서 수집된 사본(`data/product_knowledge/<slug>/`)을
실행마다 작업 폴더 `rules/` 로 복사한다. 규칙 파일의 판이 제품 설정
`qa_intelligence.rules.supported_rev` 와 다르면 AI 단계가 멈추고 메일 첫 줄에 알린다(REQ-DAILY-010).
제품 규칙 Skill 을 새 판에 맞춰 고친 뒤 그 값을 올린다.

## 입력 자료와 외부로 나가는 것

| 자료 | 어디서 오나 | 언제 새로워지나 |
|---|---|---|
| SRS, 이슈(댓글 포함) | Polarion. 서버가 점검할 때 직접 읽는다 | 매 실행 |
| TC Excel, 매뉴얼, QA 규칙·지침 프롬프트 | 서버의 지식 사본. 담당자 PC 의 `QA_ProductKnowledge_Sync` 가 올린다 | 평일 10:00 업로드 뒤 다음 점검부터 |
| 등록된 사양서 문서 조각 | 공용 Knowledge(`knowledge_documents.py`) | 문서를 등록·교체할 때 |

PC 쪽 예약 작업과 하루 순서는 [자동화 아키텍처 §7.1·§7.2](../AUTOMATION.md) 에 있다.

작업 입력에는 이벤트마다 코드가 먼저 고른 후보(비슷한 과거 이슈, 관련 SRS, 사양서 조각, 매뉴얼, TC)만
들어간다. 작업 폴더에는 오늘 SRS 전체, TC 전체 색인, 매뉴얼 텍스트도 마스킹된 채로 놓인다. 이 가운데
Claude 가 실제로 읽은 부분이 Anthropic 으로 전송된다. 무엇을 읽고 검색했는지는 실행 폴더의
`claude_logs/` 에 남는다.

Claude 의 읽기·검색 도구(`Read`, `Glob`, `Grep`)는 작업 폴더 안(`./**`)으로만 허용한다. 허용 목록에
맞지 않는 호출은 묻지 않고 거부된다(`--permission-mode dontAsk`). 거부된 호출은 `claude_logs/` 의
`permission_denials` 에 남는다.

## 서버 설치

한 번만 한다. 명령은 서버의 앱 사용자로 실행한다(표시한 곳만 `sudo`).

1. **Claude CLI 설치** (Linux 네이티브 설치). 설치 후 `claude --version` 으로 확인한다.
2. **토큰 발급** — 브라우저가 있는 PC 에서 회사 Claude **Team 계정**으로 로그인한 뒤
   `claude setup-token` 을 실행한다. 출력된 토큰(유효기간 1년)을 서버 `secrets.txt` 의
   `CLAUDE_CODE_OAUTH_TOKEN=` 에 넣는다. 서버에서 `claude login` 을 하지 않는다.
3. **Polarion** — **읽기 권한만 있는 계정**으로 PAT 를 발급해 `POLARION_TOKEN=` 에, 사내
   Polarion 주소를 `POLARION_HOST=` 에 넣는다. 프로젝트·조회식·필드 이름은 제품 설정(`alm:`)에 있다.
4. **메일 수신자** — `DAILY_QA_EMAIL_TO=` (비우면 `NOTIFY_EMAIL_TO`). SMTP 는 기존 알림 설정을 쓴다.
5. **작업 폴더** — 기본값 `~/.qa-daily-workspace`. 저장소 밖이고 상위 폴더에 `CLAUDE.md` 가
   없어야 한다. 제품마다 그 아래 `<slug>/` 를 쓴다. 다른 곳을 쓰려면 `config.yaml` 의 `daily_qa.workspace_dir`.
6. **메일 링크** — `config.yaml` 의 `daily_qa.review_base_url` 에 사용자가 여는 주소(예:
   `http://<서버주소>`)를 넣는다. 링크는 `/qa-agent/runs/<실행 ID>` 를 가리킨다.
7. **점검** — 제품마다 한 번:

```bash
.venv/bin/python scripts/run_daily_qa.py --check --product VXvue
```

모든 줄이 `[OK]` 여야 한다. 규칙 줄이 OK 가 아니면 `/knowledge` 화면의 제품 카드에서 마지막 수집
시각을 먼저 본다.

8. **시험 실행** — Claude 를 부르지 않는 dry-run, 그다음 메일 없이 정식 1회:

```bash
.venv/bin/python scripts/run_daily_qa.py --dry-run --no-email
```

```bash
.venv/bin/python scripts/run_daily_qa.py --no-email
```

dry-run 은 스냅샷·이벤트·Finding 을 저장하지 않는다. 그래서 여러 번 해도 다음 정식 실행의 비교
결과가 달라지지 않는다.

**첫 정식 실행**은 SRS·이슈 기준 스냅샷만 저장한다. 실행 결과가 `기준 스냅샷 생성`(`BASELINE`)이고
Claude 호출이 0회인 것이 정상이다. 다음 실행부터 바뀐 것만 분석한다.

9. **예약** — 따로 등록할 것이 없다. 앱(`qa-verification.service`)을 재시작하면 내장 스케줄러가
   `daily_qa.products` 의 제품마다 평일 07:30(한국 시간)에 점검을 분리된 프로세스로 띄운다.
   공휴일(`config/holidays/kr.yaml`)에는 띄우지 않는다. 앱 로그 `output/logs/app.log` 에
   `scheduled_job_registered id=qa_agent_vxvue` 와 `id=qa_agent_limit_catchup` 이 보이면 등록된 것이다.

10. (선택) 서버의 Claude 를 이 점검 전용으로만 쓴다면 `deploy/claude/managed-settings.json` 을
    `/etc/claude-code/managed-settings.json` 으로 복사한다. 서버의 모든 Claude 사용에 명령 실행·웹
    조회 금지가 강제된다.

## 설정 (`config.yaml` 의 `daily_qa`)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `daily_qa.products` | `[daily_qa.product]` | 예약·대시보드에 올릴 제품 이름 목록. 제품마다 `config/products/<slug>.yaml` 이 있어야 한다 |
| `daily_qa.product` | `VXvue` | 제품 목록이 없을 때의 제품, 개편 전 기록의 제품 |
| `daily_qa.schedule.day_of_week` / `.time` | `mon-fri` / `07:30` | 예약 요일·시각(한국 시간) |
| `daily_qa.schedule.skip_holidays` | `true` | 공휴일에 예약 실행을 건너뛴다 |
| `daily_qa.schedule.extra_holidays` | `[]` | 공휴일 표에 더할 날짜(`YYYY-MM-DD`, 회사 휴무일 등) |
| `daily_qa.polarion.issue_query` 등 | `type:issue` | 제품 설정 `alm.queries` 가 없을 때만 쓰는 대체값 |
| `daily_qa.max_tasks_per_run` | `30` | 한 실행의 AI 작업 묶음 상한. 넘은 이벤트는 `pending` 으로 다음 실행에 |
| `daily_qa.intelligence.comment_fetch_limit` | `300` | 한 실행에서 댓글을 읽을 이슈 수 상한. 넘은 이슈는 다음 실행에 |
| `daily_qa.intelligence.comment_min_chars` | `15` | 이보다 짧은 댓글은 의미 없는 댓글로 본다 |
| `daily_qa.intelligence.event_max_attempts` | `3` | 같은 이벤트의 분석 실패가 이 횟수면 포기(`abandoned`) |
| `daily_qa.intelligence.catchup_max_hours` / `catchup_delay_minutes` | `12` / `5` | 세션 한도 초기화가 이 시간 안이면 초기화 몇 분 뒤 한 번 다시 돈다 |
| `daily_qa.intelligence.use_knowledge_documents` | `true` | 등록된 사양서 문서 조각도 후보로 찾는다 |

## 운영

| 확인할 것 | 방법 |
|---|---|
| 예약 등록 여부 | `output/logs/app.log` 의 `scheduled_job_registered id=qa_agent_<slug>` |
| 예약을 건너뛴 이유 | `app.log` 의 `qa_agent_skipped reason=holiday·disabled·running·polarion_설정_없음` |
| 마지막 실행 로그 | `output/logs/daily_qa.out`, `app.log` 의 `daily_qa_launched pid=… product=… trigger=…` |
| 오늘 결과·다음 실행·한도 | `/qa-agent` 대시보드, `/qa-agent/status` (JSON) |
| 기간의 분석 | `/qa-agent/period?start=YYYY-MM-DD&end=YYYY-MM-DD` |
| 실행별 상세 | `/qa-agent/runs/<실행 ID>` · `output/daily_qa/<실행 ID>/audit.json` |
| AI 에 보낸 입력 | `output/daily_qa/<실행 ID>/sent/*.json` (마스킹 후 원본 그대로) |
| Claude 가 읽고 검색한 것 | `output/daily_qa/<실행 ID>/claude_logs/*.claude.json` 의 `tool_call_log` |
| 분석 대기·실패 이벤트 | 대시보드 "분석 대기" 수. 실패한 이벤트는 다음 실행이 다시 분석하고 3회면 포기한다. 한 이벤트가 분석 두 가지를 부르면 실패한 분석만 다시 돈다 |
| 기간 실행 | 종료일이 지난 날이면 저장 스냅샷끼리 비교하고 대기·실패 이벤트는 건드리지 않는다. 매일 실행이 이미 찾은 변경은 다시 만들지 않는다(REQ-QAINTEL-027) |
| Claude 사용량 한도 | 대시보드 맨 위 알림·메일 첫 줄. 초기화 시각과 catch-up 예정이 보인다 |
| 토큰 사용량 | `/cost-dashboard` 의 "QA Agent 점검(예약·수동, Claude CLI)" 줄(`qa_agent_run`) |
| 토큰 만료 | 발급일 + 1년. 만료 30일 전에 2단계를 다시 한다 |
| 공휴일 표 | 해마다 연말에 다음 해 음력·대체·선거 공휴일을 `config/holidays/kr.yaml` 에 더한다 |

종료 코드: `0` 성공(변경사항 없음·기준 스냅샷 생성 포함), `1` 일부·전체 실패, `2` 설정 오류, `3` 다른 실행이 진행 중.
Polarion 설정이 없어 수집을 건너뛴 실행은 변경을 확인하지 못했으므로 `변경사항 없음` 이 아니라 `일부 실패`(종료 코드 `1`)다(REQ-QAINTEL-007).

## 이슈 기록이 없는 기간의 현재 상태 점검 (REQ-QAINTEL-030)

이슈 스냅샷은 첫 매일 실행부터 쌓이므로 그 전 기간의 이슈 변화는 알 수 없다. 대시보드 [현재 상태 점검] 또는
`scripts/run_daily_qa.py --issue-audit --since YYYY-MM-DD --until YYYY-MM-DD` 는 지금 이슈 전체를 그 기간의 SRS
변화와 맞춰 본다. 판정 카드에는 "현재 상태 기준(기간 이력 없음)" 표시가 붙는다.

- 점검 모델: `daily_qa.intelligence.audit_model`(비우면 `ai.claude.models.light`). 지금 설정은 세 등급이 모두
  `claude-opus-5-5` 라 가벼운 모델로 바꿔야 사용량이 줄어든다(예: `claude-sonnet-5-5`).
- 한 작업의 이슈 수: `daily_qa.intelligence.audit_batch_size`(기본 10). 실행당 작업 상한은 `daily_qa.max_tasks_per_run`.
- 2026-10-01 실측(08-20 이후 비교): 이슈 647건 중 연결 SRS 가 바뀐 이슈 371건, SRS 그대로인 Spec 판정 26건,
  SRS 단위 묶음 76개. 기간을 좁히면 대상이 줄어든다.

## 과거 SRS 스냅샷 가져오기 (REQ-QAINTEL-029)

ALM-QA-Automation 의 사양서 자동화(`apps/srs-spec`)가 날짜마다 남긴 SRS 스냅샷을 이 시스템 스냅샷으로 바꿔 넣는다.
그러면 이 시스템을 쓰기 전 기간도 [지금 실행]의 기간 실행으로 분석할 수 있다. 그 도구가 있는 PC 에서 돌린다.

```text
.venv/Scripts/python.exe scripts/import_alm_srs_history.py --product VXvue --dry-run
.venv/Scripts/python.exe scripts/import_alm_srs_history.py --product VXvue
```

- 원본 폴더는 `config.yaml` 의 `daily_qa.alm_history.srs_snapshot_dir`(이 프로젝트 기준 상대 경로)다. `--source` 로 바꿀 수 있다.
- 읽지 못한 파일이 있거나 개수가 `manifest.json` 과 다른 날짜는 건너뛴다. 이미 있는 날짜는 덮어쓰지 않는다.
- 2026-10-01 실측: 12개 날짜 가운데 10개를 가져왔다. 2026-09-07·09-21 은 srs-spec 이 같은 날 두 번 동시에 돌아(정기 작업과 부팅 만회 작업이 겹침) JSON 13개가 깨져 건너뛴다. srs-spec 에 실행 잠금이 들어간 09-22 부터는 깨진 날이 없다.
- 이슈는 가져오지 않는다. `apps/issue-export` 는 요청한 이슈의 지금 상태만 남긴다. 이슈 스냅샷은 첫 매일 실행부터 쌓인다.
- 서버에서 쓰려면 `data/daily_qa/snapshots/<slug>/srs/` 의 가져온 파일을 서버의 같은 폴더로 옮긴다.

## 알려진 제한

- Polarion 이슈 응답에 댓글 관계(`relationships.comments`)의 번호가 실제로 오는지 서버에서 확인하지 않았다.
  오지 않으면 수정 시각이 바뀐 이슈만 댓글을 읽는다. 서버 첫 실행 뒤 `issues` 스냅샷의 `comment_ids` 를 본다.
- 이슈 전체 조회(`type:issue`)의 실제 건수·시간은 서버 첫 실행에서 확인한다.
- 공휴일 표는 2025~2027년만 있다. 근로자의 날과 회사 휴무일은 `extra_holidays` 로 더한다.
- TC 의 옛 Legacy SRS 번호와 현재 `oldId` 가 대부분 맞지 않아(2026-09-28 기준 317종 중 239종),
  사양–TC 연결 점검의 `TC 없음` 에 실제로는 TC 가 있는 SRS 가 섞인다. `qa-trace-gap` 으로 대화형 확인한다.
- 읽기 도구를 작업 폴더 안으로 좁힌 규칙(`Read(./**)` 등)이 실제 CLI 에서 밖의 파일 읽기를 거부하는지는
  서버에서 한 번 확인해야 한다.
- Codex 로 결과를 한 번 더 확인하는 교차 검증(검증 관문 G7)은 이후 고도화 범위다.
