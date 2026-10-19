# ALM 변경 리포트 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 분석 설정과 상관없이 매 업무일 이슈·SRS 를 수집하고, 기간 안 기록을 하루씩 비교한 변경 목록을 화면과 매일 메일로 보여 주며, 꺼져 있던 동안의 변경은 [밀린 변경 분석]으로만 분석한다.

**Architecture:** 핵심 앱(FastAPI + Jinja2 + SQLite)만 바꾼다. 수집은 기존 점검 프로세스에 새 실행 종류 `collect` 를 더해 AI 단계만 건너뛴다. 변경 목록은 저장된 날짜별 스냅샷을 기존의 순수 비교 함수(`detect_issue_events`, `diff_snapshots`)로 이웃끼리 비교해 만든다. 화면·메일은 이 목록 하나를 공유한다.

**Tech Stack:** FastAPI, Jinja2, SQLite, APScheduler, pytest. 새 의존성 없음.

**Spec:** `specs/alm-change-report.md`(REQ-ALMCHG-001~005), `specs/qa-intelligence.md`(REQ-QAINTEL-001·002·005·027·035·038). 사양 변경이 필요한 Task 는 첫 단계에서 사양을 먼저 고친다.

## 0. 계획 전에 확인한 것 (2026-10-16, 읽기 전용)

| 질문 | 확인한 사실 | 근거 |
|---|---|---|
| AI 없이 수집만 하는 경로가 있나 | 있다. 절약 모드의 `prepare` 실행이 `collection_only` 로 수집·이벤트 저장까지 하고 AI 앞에서 멈춘다. 다만 준비 목록(`_prepare_plan`)도 만든다 | `app/modules/daily_qa/pipeline.py:441`, `:931-934`, `:1081-1107` |
| 자동 분석을 끄면 무엇이 멈추나 | 예약·만회가 `policy_off`·`not_due` 로 프로세스를 아예 띄우지 않는다 | `app/modules/daily_qa/scheduled_jobs.py:86`, `:105`, `:182` |
| 비교 함수를 다른 스냅샷 쌍에 쓸 수 있나 | 쓸 수 있다. 두 함수 모두 DB·파일·시계를 쓰지 않는다 | `change_events.py:273`, `srs_snapshot.py:99` |
| 날짜별 스냅샷 목록 함수가 있나 | 없다. `previous`·`at_or_before`·`oldest_before` 만 있다. SRS 는 옛 폴더(`legacy_srs_dir`)도 본다 | `app/modules/daily_qa/snapshots.py:22-81` |
| 담당자·이름 | 담당자는 수집하지 않는다. Polarion 조회가 `include=author`, `fields[users]=name` 으로 작성자 이름 표를 이미 만든다. 댓글 작성자는 사용자 번호만 있다 | `polarion.py:82-103`, `product_adapter.py:280-324` |
| 이벤트 상태 | `not_required`·`pending`·`done`·`failed`·`abandoned`. 자동 분석은 `pending`·`failed` 만 고른다 | `daily_qa_storage.py:42-48`, `pipeline.py:903-927` |
| 설정 변경 이력 | `<slug>:automation_policy_history` 상태 값(JSON 목록). 켠 시각을 계산하는 함수는 없다 | `automation_policy.py:32`, `daily_qa_storage.py:419-439` |
| 운영 설정 | 운영은 절약 모드가 켜져 있다(2026-10-14 기록). 운영 `config.local.yaml` 은 저장소에 없다 | `progress.md` 2026-10-14 |
| 기간 조회 화면과 겹치나 | `/qa-agent/period`(REQ-QAINTEL-024)는 저장된 이벤트·분석 결과를 만든 시각으로 모은다. 스냅샷을 비교하지 않는다. 새 화면은 따로 둔다 | `dashboard.py:856`, `router.py:422` |

재사용: `SnapshotStore`, `detect_issue_events`, `diff_snapshots`, `holidays.check`·`parse_days`, `dash.parse_period`·`PeriodError`(날짜 문구), `notifier.send_report`·`mail_subject`, `report_recipients`, `launch_detached`, 테스트 도구 `tests/daily_qa_fixtures.py`·`tests/qa_intel_harness.py`.

새로 만드는 이유: 기간을 하루씩 비교하는 코드와 변경 리포트 메일은 어디에도 없다. `prepare` 경로는 준비 목록을 만들고 절약 모드에서만 켜지므로 그대로 쓰지 않고, 같은 `collection_only` 분기에 `collect` 실행 종류를 더한다.

### 계획 중 정한 사양 보완 (Task 안에서 사양부터 고친다)

1. **밀린 변경을 정하는 시점.** REQ-ALMCHG-005 는 "켠 시각은 설정 변경 이력에서 읽는다"고 적었다. 구현은 더 단순하게 한다. 자동 실행이 변경을 저장하는 순간 자동 분석이 꺼져 있으면 그 이벤트를 `deferred`(밀린 변경)로 저장한다. 결과는 같다. 켠 뒤 자동 분석은 `pending`·`failed` 만 고르므로 밀린 변경을 건드리지 않는다. Task 2 에서 사양 문장을 이 방식으로 고친다.
2. **이름 얻는 방법.** 13절 "확인 필요"를 푼다. 이슈 조회에 `assignee` 를 `include` 에 더해 작성자·담당자 이름 표를 받는다. 댓글 작성자 이름은 같은 스냅샷의 이름 표에서 찾고, 없으면 사용자 번호를 쓴다. Task 3 에서 사양에 적는다.
3. **수동 실행이 만든 이벤트.** [변경사항 점검하기]는 꺼져 있어도 분석한다(REQ-QAINTEL-038 7번). 그래서 밀린 변경 표시는 자동 실행(`scheduled`·`collect`)이 만든 이벤트에만 한다. Task 2 에서 사양에 적는다.
4. **아직 읽지 못한 댓글.** 댓글 번호는 늘었는데 본문을 못 읽은 날은 "작성 정보 미수집" 줄을 두고, 다음 기록에서 그 댓글이 읽히면 그 줄을 작성자·시각이 있는 줄로 바꾼다(같은 댓글 번호는 한 줄). REQ-ALMCHG-002 지킬 것과 같다.

### 적용 규칙

- 사양 먼저, 테스트 먼저(TDD). Task 마다 커밋한다. push 는 Task 8 의 전체 검증 뒤 한 번 한다.
- 검증 명령: 대상 테스트 `.venv/Scripts/python.exe -m pytest <파일> -q`, 전체 게이트 `.venv/Scripts/python.exe -m pytest -q`(약 25분. 진행 상황이 보이게 `-q` 출력을 파일로 남기고 중간에 읽는다), 준비 검사 `node .project-check/project-readiness.js .`, 완료 정리 `node "C:/Users/2024980/Documents/자동화/tools/cleanup-workspace.js" --completed <작업 폴더> --apply --auto`.
- 사양·CHANGELOG 를 고친 커밋은 `node .project-check/render-spec-html.js .` 결과(`docs/SPEC.html`)를 함께 넣는다.
- 운영 배포는 이 계획에 넣지 않는다. 구현이 끝나면 사용자에게 배포 여부를 따로 묻는다.

## Global Constraints

- 예약: `daily_qa.schedule` 기본 `mon-fri`, `04:00`, 한국 시간. 업무일 = 월~금 중 등록 공휴일·추가 휴일이 아닌 날.
- 수집만 하는 실행은 Claude 호출 0회다. Claude 로그인 확인도 하지 않는다.
- `daily_qa.schedule.enabled: false` 면 수집 실행과 자동 변경 리포트 메일도 없다.
- 날짜 오류 문구(그대로): "날짜는 YYYY-MM-DD 로 입력하세요." / "시작일이 종료일보다 늦습니다." / "조회 기간은 366일까지입니다."
- 그 밖의 문구(그대로): "이 기간에 저장된 기록이 없습니다." / "비교할 기록이 하나뿐입니다(<날짜>). 다음 기록부터 변경이 보입니다." / "<날짜> ~ <날짜> 기록 없음: 이 사이 변경은 <다음 기록 날짜>에 합쳐서 보입니다." / "<첫 기록 날짜> 전의 변경은 확인할 기록이 없습니다." / "메일 수신자가 설정되지 않았습니다." / "고른 기간에 밀린 변경이 없습니다."
- 메일 제목: `[QA Agent] <제품> 변경 리포트 · <시작>~<끝> · 이슈 <n>건 · SRS <n>건` (`mail_subject` 사용). 수집 실패면 제목에 `수집 실패`.
- 메일에 넣지 않는 것: 댓글 본문, 이슈 설명·재현 절차·발생 원인·조치 내용 문장, SRS 본문 문장, AI 판단 문장.
- 메일 맺음말: "이 메일은 수집 결과를 자동으로 정리한 것입니다. AI 판단은 들어 있지 않습니다."
- 설정 기본값: `daily_qa.change_report.enabled: true`, `send_when_empty: true`, `max_items: 50`.
- 자동 변경 리포트 메일은 제품마다 업무일 하루 한 통이다.
- Polarion 은 읽기만 한다(GET).
- PC 화면 폭 1366px·1920px 에서 확인한다(휴대폰 폭은 확인하지 않는다).

## Shared Interfaces

### Contract: 실행 종류(`trigger`)
기존 `scheduled`, `manual`, `manual_cli`, `catchup`, `prepare` 에 둘을 더한다.
- `collect`: 예약·만회가 AI 분석일이 아닌 날 띄우는 수집 실행. `collection_only=True`, 준비 목록을 만들지 않는다, AI 요약 메일 없음.
- `deferred`: [밀린 변경 분석]. 고른 밀린 변경만 분석한다. AI 요약 메일은 REQ-QAINTEL-038 7번 규칙대로.

### Contract: 이벤트 상태 `deferred`
`EVENT_STATUSES["deferred"] = "밀린 변경"`. 자동 실행(`scheduled`·`collect`)이 저장할 때 자동 분석이 꺼져 있고 `analysis_required` 인 새 이벤트만 이 상태가 된다. 자동 분석·재시도·한도 뒤 재개·[변경사항 점검하기]는 이 상태를 고르지 않는다.

### Contract: 이슈 스냅샷 항목의 새 키
- `assignees: list[str]` — 담당자 사용자 번호(짧은 번호, 정렬). 관계가 없으면 `[]`.
- `user_names: dict[str, str]` — 이 이슈 응답에 들어 있던 사용자 번호 → 이름(작성자·담당자). 비교하지 않는다.

### Contract: `ChangeLine` (`app/modules/daily_qa/change_report.py`)
```python
@dataclass(frozen=True)
class ChangeLine:
    day: str            # "YYYY-MM-DD" 한국 날짜. 댓글은 작성 시각의 한국 날짜
    time: str           # 댓글만 "HH:MM"(한국 시간), 그 밖은 ""
    entity_type: str    # "issue" | "srs"
    entity_id: str
    title: str
    kind: str           # 아래 KIND_* 값
    field: str          # 공통 모델 필드 이름(없으면 "")
    before: str         # 값 변경일 때만, 목록은 ", " 로 이은 이름
    after: str
    detail: str         # SRS 바뀐 곳, 댓글 작성자 이름 등 사람이 읽는 한 줄
    comment_id: str     # 댓글 줄만
    comment_text: str   # 화면 전용. 메일에 쓰지 않는다
```
`kind` 값: 이슈 `created`(신규 등록), `status`(상태 변경), `assignee`(담당자 변경), `comment`(댓글 추가), `content`(내용 수정), `rd_result`(연구소 결과 변경), `cause_action`(발생 원인·조치 내용 변경), `other`(기타 속성 변경), `removed`(조회에서 빠짐). SRS `srs_created`, `srs_modified`, `srs_removed`. 한국어 이름은 `KIND_LABELS` 에 둔다.

### Contract: `ChangeReport`
```python
@dataclass
class ChangeReport:
    product: str
    since: str; until: str                 # 요청 기간
    compared_days: dict[str, list[str]]    # {"issues": [...], "srs": [...]} 실제 비교한 기록 날짜
    issues: list[EntityChanges]            # 마지막 변경이 최근인 대상부터
    srs: list[EntityChanges]
    counts: dict[str, int]                 # kind → 건수
    notices: list[str]                     # 확인 범위 문구(Global Constraints 문구 그대로)
    empty_reason: str                      # "" 또는 "이 기간에 저장된 기록이 없습니다." 등

@dataclass
class EntityChanges:
    entity_type: str; entity_id: str; title: str; link: str
    lines: list[ChangeLine]                # 날짜·시각 순
    net: dict[str, tuple[str, str]]        # 시작 기록 → 마지막 기록, 달라진 status·assignees 만
```

### Contract: `ChangeReportSettings`
`DailyQaSettings.change_report: ChangeReportSettings(enabled: bool = True, send_when_empty: bool = True, max_items: int = 50)`. `config.yaml` 의 `daily_qa.change_report.*` 에서 읽는다.

## Review Focus

1. 월요일·연휴 뒤 첫 실행: 자동 메일 기간이 직전 기록 날짜(금요일)부터여야 한다. 하루 전으로 계산하면 주말 변경이 빠진다. → Task 6 `test_monday_mail_covers_friday_to_monday`.
2. 자정 근처 댓글: Polarion 시각은 UTC 다. `2026-10-16T16:30:00Z` 댓글은 10-17(한국) 줄이어야 한다. → Task 4 `test_comment_day_uses_kst`.
3. 옛 SRS 스냅샷 폴더: 새 폴더에 없는 날짜의 SRS 기록도 기간 비교에 들어가야 한다(`previous` 와 같은 규칙). → Task 4 `test_between_reads_legacy_srs_dir`.
4. 댓글 본문을 아직 못 읽은 날: "작성 정보 미수집" 줄이 다음 기록에서 같은 댓글 번호의 완전한 줄로 바뀌고 두 줄이 되지 않아야 한다. → Task 4 `test_unread_comment_is_replaced_later`.
5. 같은 날 두 번 실행(04:00 정시 뒤 만회, 사람이 다시 돌림): 자동 변경 리포트 메일이 한 통만 가야 한다. → Task 6 `test_same_day_rerun_sends_one_mail`.

---

### Task 1: 매 업무일 수집 실행 (REQ-ALMCHG-001)

**Files:**
- Modify: `app/modules/daily_qa/automation_policy.py` (업무일 판정 함수 추가)
- Modify: `app/modules/daily_qa/scheduled_jobs.py:64-190` (`launch_detached`, `launch_scheduled`, `run_missed_schedules`)
- Modify: `scripts/run_daily_qa.py:78-91` (`--trigger` 선택지에 `collect`)
- Modify: `app/modules/daily_qa/pipeline.py:270-279`, `:441`, `:582`, `:931-934`, `_finish` 요약
- Test: `tests/test_alm_change_collect.py`

**Interfaces:**
- Consumes: `holidays.check`, `holidays.parse_days`, `automation_policy.is_schedule_day`, `get_policy`.
- Produces: `automation_policy.is_business_day(cfg, schedule: dict, day: date) -> bool`, `automation_policy.scheduled_trigger(cfg, store, schedule: dict, day: date) -> str` (`"scheduled"` 또는 `"collect"`), `Contract: 실행 종류` 의 `collect`. 요약에 `summary["run_kind"]` = `"analysis"` | `"collect"` 와 `summary["collect_reason"]` = `"policy_off"` | `"weekly_not_due"` | `""`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_alm_change_collect.py`, 테스트마다 `# Validates: REQ-ALMCHG-001`)

```python
def test_policy_off_still_launches_collect(...):      # mode off, 평일 → launch_detached 가 trigger "collect" 로 Popen
    assert popen_args[popen_args.index("--trigger") + 1] == "collect"
def test_weekly_non_first_day_launches_collect(...):  # weekly, 화요일 → "collect"; 월요일 → "scheduled"
def test_holiday_and_disabled_launch_nothing(...):    # 공휴일 → status "holiday", schedule.enabled False → "schedule_disabled", Popen 0회
def test_missed_run_catches_up_collect_when_off(...): # run_missed_schedules: mode off, 08:00 → 한 번 띄우고 scheduled_date=오늘
def test_collect_run_calls_claude_zero_times(env):    # run_daily(trigger="collect") → calls == [], 이벤트 저장됨,
    assert outcome["summary"]["run_kind"] == "collect" and outcome["summary"]["collection_only"] is True
    assert outcome["summary"]["collect_reason"] in ("policy_off", "weekly_not_due")
def test_collect_run_sends_no_ai_summary_mail(env):   # 보낸 메일 가운데 AI 요약 메일(build_email 제목)이 없다(변경 리포트 메일은 Task 6)
def test_collect_run_makes_no_prepare_plan(env):      # outcome["summary"]["plan_id"] 가 비어 있다
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_collect.py -q` / Expected: FAIL (`is_business_day` 없음, trigger "collect" 거부)

- [ ] **Step 3: `automation_policy.is_business_day`·`scheduled_trigger` 구현**
`is_business_day` = `schedule["enabled"]` 이고 요일이 `parse_days(day_of_week)` 안이며 `skip_holidays` 면 `holidays.check` 가 공휴일이 아님. `scheduled_trigger` = `is_schedule_day(...)` 면 `"scheduled"`, 아니면 `"collect"`.

- [ ] **Step 4: 예약 경로 수정**
`launch_detached`: `trigger == "scheduled"` 일 때 `policy_off`·`not_due` 로 돌려보내지 말고, 공휴일 판정 뒤 `scheduled_trigger(...)` 로 바꾼 trigger 로 띄운다. `trigger == "catchup"`(한도 뒤 재개)는 지금 규칙 그대로(일일 모드만). `run_missed_schedules`: 제품별 조건을 `is_business_day` 하나로 바꾼다(모드 검사 삭제). `launch_scheduled` 의 상태 기록 예외 목록은 그대로 둔다.

- [ ] **Step 5: 파이프라인 수정**
`self.collection_only = (cfg.savings_enabled and trigger == "prepare" and not on_demand) or trigger == "collect"`. `_analyses` 의 `collection_only` 분기에서 `trigger == "collect"` 면 `_prepare_plan` 을 부르지 않고 `stages["AI"] = _stage("skipped", "수집만 실행 · AI 분석 없음 (<이유>)")` 로 끝낸다. 이유 문구: `policy_off` → "자동 분석 꺼짐", `weekly_not_due` → "주간 모드의 분석일 아님". 메일 생략 조건(`:270-279`)에 `trigger == "collect"` 를 더한다. `scripts/run_daily_qa.py` 선택지에 `collect` 추가.

- [ ] **Step 6: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_collect.py tests/test_qa_intel_schedule.py tests/test_qa_intel_token_saving.py -q` / Expected: PASS. 기존 예약 시험 가운데 "꺼짐이면 띄우지 않는다"를 단언한 것은 REQ-QAINTEL-038 변경에 맞게 "collect 로 띄운다"로 고치고, 고친 테스트 이름을 커밋 메시지에 적는다.

- [ ] **Step 7: 커밋** — `git commit -m "feat: AI 설정과 상관없는 매 업무일 수집 실행 (REQ-ALMCHG-001, REQ-QAINTEL-001·035·038)"`

### Task 2: 밀린 변경으로 저장하기 (REQ-ALMCHG-005 1~2번)

Task 1 만 배포하면 꺼져 있던 동안의 이벤트가 `pending` 으로 쌓였다가 켜는 순간 자동 분석된다. 그래서 Task 1 바로 뒤에 둔다.

**Files:**
- Modify: `specs/alm-change-report.md` (REQ-ALMCHG-005 "지킬 것"·1번: 밀린 변경은 저장 시점의 설정으로 정한다. 자동 실행만 해당)
- Modify: `app/core/daily_qa_storage.py:42-48` (`EVENT_STATUSES` 에 `deferred`)
- Modify: `app/modules/daily_qa/pipeline.py` `_persist` (`:791-852`)
- Test: `tests/test_alm_deferred.py`

**Interfaces:**
- Consumes: `Contract: 실행 종류`, `automation_policy.get_policy`.
- Produces: `Contract: 이벤트 상태 deferred`. 요약 `summary["deferred_events"]: int`.

- [ ] **Step 1: 사양 고치기** — REQ-ALMCHG-005 순서 1번을 "자동 실행(예약·만회)이 변경을 저장할 때 자동 분석이 꺼져 있으면, AI 분석이 필요한 새 이벤트를 밀린 변경으로 저장한다. 사람이 누른 실행이 만든 이벤트는 밀린 변경이 아니다."로, 지킬 것의 "켠 시각은 설정 변경 이력에서 읽는다…"를 "밀린 변경인지는 변경을 저장하는 순간의 설정으로 정한다."로 바꾼다. 1절 용어표의 `켠 시각` 줄도 같은 뜻으로 고친다. HTML 다시 만들기.

- [ ] **Step 2: 실패하는 테스트 작성** (`# Validates: REQ-ALMCHG-005`)

```python
def test_collect_while_off_stores_deferred(env):       # 정책 off + trigger "collect" → 새 이슈 이벤트 analysis_status == "deferred"
def test_not_required_events_stay_not_required(env):   # 상태만 바뀐 이벤트는 "not_required"
def test_weekly_collect_keeps_pending(env):            # weekly 의 collect → "pending"
def test_manual_run_while_off_keeps_pending(env):      # trigger "manual" + off → "pending"(REQ-QAINTEL-038 7번)
def test_auto_analysis_skips_deferred(env):            # 정책을 켜고 trigger "scheduled" → deferred 이벤트는 분석 안 함,
    assert calls == [] and store.list_events(statuses=("deferred",))  # 끄기 전 pending 은 분석됨(별도 단언)
```

- [ ] **Step 3: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_deferred.py -q` / Expected: FAIL

- [ ] **Step 4: 구현** — `EVENT_STATUSES["deferred"] = "밀린 변경"`. `_persist` 에서 `add_events` 뒤, `trigger in ("scheduled", "collect")` 이고 `get_policy(...)["mode"] == "off"` 면 새로 저장된 `analysis_required` 이벤트 번호를 `update_events(ids, "deferred")` 로 바꾸고 `summary["deferred_events"]` 에 수를 적는다.

- [ ] **Step 5: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_deferred.py tests/test_qa_intel_pipeline.py tests/test_qa_intel_failures.py -q` / Expected: PASS

- [ ] **Step 6: 커밋** — `git commit -m "feat: 자동 분석이 꺼진 동안의 변경을 밀린 변경으로 저장 (REQ-ALMCHG-005)"`

### Task 3: 담당자와 사용자 이름 수집 (REQ-QAINTEL-002·005)

**Files:**
- Modify: `specs/alm-change-report.md` 13절 (이름 얻는 방법 "확인 필요" 를 풀어 REQ-ALMCHG-002 지킬 것으로 옮긴다)
- Modify: `app/modules/daily_qa/polarion.py:82-103` (`include` 를 `"author,assignee"` 로, 항목마다 `_user_names`)
- Modify: `app/modules/daily_qa/product_adapter.py:280-311` (`assignees`, `user_names`)
- Modify: `app/modules/daily_qa/change_events.py:83` (`METADATA_FIELDS` 에 `"assignees"`)
- Modify: `tests/daily_qa_fixtures.py:59` (`issue_item(..., assignees=(), author=None)` — 각 값은 `(사용자 번호, 이름)`, `FakePolarion` 이 `included` 사용자 표를 돌려줌)
- Test: `tests/test_alm_assignee.py`

**Interfaces:**
- Produces: `Contract: 이슈 스냅샷 항목의 새 키`. 담당자 변경은 `ISSUE_METADATA_CHANGED` 의 `changed_fields` 에 `"assignees"` 로 들어간다.

- [ ] **Step 1: 사양 고치기** — REQ-ALMCHG-002 지킬 것에 "작성자·담당자 이름은 이슈 조회가 함께 돌려준 사용자 이름을 쓰고, 없으면 사용자 번호를 쓴다."를 더하고 13절의 해당 "확인 필요" 줄을 지운다. HTML 다시 만들기.

- [ ] **Step 2: 실패하는 테스트 작성** (`# Validates: REQ-QAINTEL-002, REQ-QAINTEL-005`)

```python
def test_normalize_issue_reads_assignees_and_names():
    # 픽스처: issue_item(..., assignees=(("u-assignee", "담당자A"),), author=("u-author", "작성자A"))
    item = normalize_issue(raw, profile)
    assert item["assignees"] == ["u-assignee"]
    assert item["user_names"] == {"u-assignee": "담당자A", "u-author": "작성자A"}
def test_missing_assignee_relation_is_empty_list():     # 관계 없음 → []
def test_assignee_change_is_metadata_event():           # 같은 상태, 담당자만 바뀜 → ISSUE_METADATA_CHANGED, changed_fields == ["assignees"]
def test_user_names_change_alone_is_not_an_event():     # 이름 표만 달라짐 → 이벤트 없음
def test_polarion_query_includes_assignee():            # 요청 params["include"] == "author,assignee"
```

- [ ] **Step 3: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_assignee.py -q` / Expected: FAIL

- [ ] **Step 4: 구현** — 관계 이름은 `relations.get("assignees", "assignee")`(제품 설정에 없으면 Polarion 표준 이름 `assignee`). `user_names` 는 응답 `included` 의 `users` 가운데 이 항목의 작성자·담당자 번호만 담는다.

- [ ] **Step 5: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_assignee.py tests/test_qa_intel_events.py tests/test_qa_intel_products.py tests/test_qa_intel_pipeline.py -q` / Expected: PASS. 옛 스냅샷(키 없음)과 새 스냅샷을 비교해도 담당자 이벤트가 생기지 않는지 `test_old_snapshot_without_assignees_is_not_a_change` 를 더한다(옛 값이 없으면 비교하지 않음).

- [ ] **Step 6: 커밋** — `git commit -m "feat: 이슈 담당자·사용자 이름 수집과 담당자 변경 감지 (REQ-QAINTEL-002·005)"`

### Task 4: 기간 안 기록을 하루씩 비교 (REQ-ALMCHG-002)

**Files:**
- Modify: `app/modules/daily_qa/snapshots.py` (`between` 추가)
- Create: `app/modules/daily_qa/change_report.py`
- Test: `tests/test_alm_change_report.py`

**Interfaces:**
- Consumes: `Contract: 이슈 스냅샷 항목의 새 키`, `detect_issue_events`, `diff_snapshots`, `holidays.check`, `dash.parse_period` 의 문구.
- Produces: `SnapshotStore.between(kind: str, start_day: str, end_day: str) -> list[Path]` (시작일 또는 그 전 가장 가까운 기록 + `start_day < 날짜 <= end_day` 인 기록, 날짜순, 같은 날짜는 새 폴더 우선), `Contract: ChangeLine`, `Contract: ChangeReport`, `build_change_report(cfg: DailyQaSettings, snapshots: SnapshotStore, since: str, until: str, *, intel=None, noise_patterns=()) -> ChangeReport`, `KIND_LABELS: dict[str, str]`, `kst_day(iso: str) -> str`.

- [ ] **Step 1: 실패하는 테스트 작성** (`# Validates: REQ-ALMCHG-002`). 스냅샷은 `SnapshotStore.save` 로 tmp 폴더에 직접 만든다.

```python
def test_status_round_trip_shows_two_lines_and_no_net_change():   # 10-16 A, 10-17 B, 10-19 A
    lines = [l for l in report.issues[0].lines if l.kind == "status"]
    assert [(l.day, l.before, l.after) for l in lines] == [("2026-10-17", "A", "B"), ("2026-10-19", "B", "A")]
    assert "status" not in report.issues[0].net
def test_assignee_line_has_names():                                # before/after 가 이름, 없으면 번호
def test_comment_day_uses_kst():                                   # created "2026-10-16T16:30:00Z" → day "2026-10-17", time "01:30"
def test_unread_comment_is_replaced_later():                       # 첫 날 comments_deferred → "작성 정보 미수집", 다음 날 읽힘 → 같은 comment_id 한 줄
def test_srs_lines_and_sentence_counts():                          # 신규·수정(본문 더해진 2, 빠진 1)·조회에서 빠짐
def test_gap_and_before_first_notices():                           # 10-17 기록 없음 → 문구 그대로, 시작 전 기록 없음 → 문구 그대로
def test_between_reads_legacy_srs_dir():                           # 옛 폴더의 날짜도 포함
def test_no_records_and_single_record_reasons():                   # empty_reason 문구 그대로
def test_same_input_same_report():                                 # 두 번 만든 결과 asdict 가 같다
def test_removed_then_back_shows_both():                           # 조회에서 빠짐 → 다음 기록에서 신규 등록
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_report.py -q` / Expected: FAIL (모듈 없음)

- [ ] **Step 3: `SnapshotStore.between` 구현** — `_dirs(kind)` 의 파일을 모두 모아 날짜(파일 이름)별로 새 폴더를 우선해 하나씩 고른다.

- [ ] **Step 4: `build_change_report` 구현** — 이슈는 이웃한 두 기록을 `detect_issue_events(before, after, previous_collected_at=meta(before)["collected_at"], ...)` 로 비교해 이벤트를 `ChangeLine` 으로 바꾼다. 바꾸는 규칙:

| 이벤트 | 줄 |
|---|---|
| `ISSUE_CREATED` | `created` 한 줄 |
| `ISSUE_REMOVED` | `removed` 한 줄 |
| `ISSUE_STATUS_ONLY_CHANGED` | `status` 한 줄 |
| `ISSUE_METADATA_CHANGED` | 바뀐 필드마다 한 줄: `status`→`status`, `assignees`→`assignee`, 나머지→`other` |
| `ISSUE_CONTENT_UPDATED`·`ISSUE_REPRODUCTION_UPDATED` | `content` 한 줄, `detail` 에 바뀐 필드의 한국어 이름 |
| `ISSUE_RD_RESULT_CHANGED` | `rd_result` 한 줄, 원본 값 전후 |
| `ISSUE_ROOT_CAUSE_CHANGED`·`ISSUE_ACTION_DETAILS_CHANGED` | `cause_action` 한 줄, 필드 이름만 |
| `ISSUE_COMMENT_ADDED` | 댓글마다 한 줄 |

본문을 못 읽은 날(`comments_deferred`·`comments_error`)에 `comment_ids` 가 늘었으면 새 번호마다 "작성 정보 미수집" 줄을 두고, 뒤 기록에서 같은 번호가 읽히면 그 줄을 바꾼다. SRS 는 `diff_snapshots` 결과의 `added`·`modified`·`removed` 를 각각 줄로 만든다. 날짜는 뒤 기록의 파일 날짜, 댓글은 `kst_day(created)`. 확인 범위는 요청 기간 안 업무일(`is_business_day`) 중 기록이 없는 날을 이어진 구간으로 묶어 문구를 만든다. 링크는 `<Polarion 기본 주소>/polarion/redirect/project/<project_id>/workitem?id=<번호>` 다. 기본 주소는 `cfg.polarion.host`(비밀 설정의 `polarion_host`, `app/modules/daily_qa/settings.py:258`)를, `project_id` 는 제품 설정을 쓴다. 기본 주소가 비어 있으면 링크 없이 번호만 보인다. 주소를 코드에 적지 않는다.

- [ ] **Step 5: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_report.py -q` / Expected: PASS

- [ ] **Step 6: 커밋** — `git commit -m "feat: 기간 안 스냅샷을 하루씩 비교한 변경 목록 (REQ-ALMCHG-002)"`

### Task 5: 변경 리포트 화면 (REQ-ALMCHG-003, REQ-QAINTEL-027 6번)

**Files:**
- Modify: `app/modules/qa_agent/router.py` (`GET /changes`, `POST /changes/mail`)
- Create: `app/modules/qa_agent/templates/changes.html`
- Modify: `app/modules/qa_agent/templates/dashboard.html:73-93` ([변경 리포트] 링크), `run_detail.html` (기간 실행이면 같은 기간 변경 리포트 링크), `guide.html` (사용법 한 절)
- Test: `tests/test_alm_change_page.py`

**Interfaces:**
- Consumes: `build_change_report`, `KIND_LABELS`, `dash.choose`, `dash.parse_period`, `PeriodError`.
- Produces: `GET /qa-agent/changes?product=&since=&until=&kind=&q=` (HTML). 메일 보내기 버튼과 `POST /qa-agent/changes/mail` 은 Task 6 이 붙인다.

- [ ] **Step 1: 실패하는 테스트 작성** (`# Validates: REQ-ALMCHG-003`). `tests/test_qa_intel_dashboard.py` 의 `world` 구성 방식(`dash.choose` 바꿔치기, `TestClient`)을 따른다.

```python
def test_default_period_is_last_two_records(world):   # 날짜 없이 열면 마지막 두 기록 날짜가 기간 칸에 들어 있다
def test_issue_and_srs_tabs_render_lines(world):      # 상태 변경 줄, SRS 신규 줄, Polarion 링크가 보인다
def test_comment_text_only_on_page(world):            # 댓글 본문이 접힌 영역(<details>)에 있다
def test_kind_filter_and_search(world):               # kind=assignee → 담당자 줄만, q=VP-1 → 그 이슈만
def test_bad_dates_return_400_with_message(world):    # Global Constraints 문구 그대로
def test_dashboard_and_period_run_link(world):        # 대시보드에 /qa-agent/changes 링크, 기간 실행 상세에 since/until 링크
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_page.py -q` / Expected: FAIL (404)

- [ ] **Step 3: 화면 구현** — 날짜가 둘 다 비면 `SnapshotStore.latest`·`previous` 로 마지막 두 기록 날짜를 기간으로 쓴다. 하나라도 있으면 `dash.parse_period` 로 검사한다. 템플릿은 `module_base.html` 을 잇고 `_intel_style.html` 을 쓴다. 구획은 REQ-ALMCHG-003 결과 표 순서다. 탭 전환과 거르기는 서버 주소 값으로 한다(새 JavaScript 를 만들지 않는다).

- [ ] **Step 4: 통과 확인과 화면 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_page.py tests/test_qa_intel_dashboard.py -q` / Expected: PASS. 로컬 서버를 띄워 1366px·1920px 에서 `/qa-agent/changes` 를 열고 화면을 캡처한다.

- [ ] **Step 5: 커밋** — `git commit -m "feat: /qa-agent/changes 변경 리포트 화면 (REQ-ALMCHG-003, REQ-QAINTEL-027)"`

### Task 6: 변경 리포트 메일 (REQ-ALMCHG-004)

**Files:**
- Modify: `app/modules/daily_qa/settings.py` (`ChangeReportSettings`), `config.yaml:209-252` (`daily_qa.change_report` 블록)
- Create: `app/modules/daily_qa/change_mail.py`, `app/modules/daily_qa/templates/change_report_email.html`
- Modify: `app/modules/daily_qa/pipeline.py` (`execute` 의 수집·저장 직후)
- Modify: `app/core/notifier.py:351-394` (`send_test` 에 `"change_report"`), `app/web/settings_router.py:48`, `:341` (시험 발송 종류)
- Modify: `app/modules/qa_agent/router.py`, `app/modules/qa_agent/templates/changes.html` (메일 보내기 버튼)
- Test: `tests/test_alm_change_mail.py`, `tests/test_alm_change_page.py`

**Interfaces:**
- Consumes: `Contract: ChangeReport`, `Contract: ChangeReportSettings`, `mail_subject`, `report_recipients`, `run_daily(..., send_email=...)` 의 발송기.
- Produces: `build_change_email(report: ChangeReport, *, product: str, page_url: str, ai_state: dict, max_items: int) -> tuple[str, str, str]` (제목, 글 본문, HTML), `build_change_failure_email(product: str, stages: dict, *, kind: str) -> tuple[str, str, str]` (`kind`: `"collect_failed"` | `"baseline"`). 요약 `summary["change_mail"] = {"status": "sent"|"skipped"|"failed"|"not_configured", "reason": str, "since": str, "until": str}`. 상태 키 `<slug>:change_mail_date`. `POST /qa-agent/changes/mail` (폼 `product`, `since`, `until` → JSON `{"status": "sent"|"failed"|"not_configured", "detail": str}`, 수신자 없음은 409).

- [ ] **Step 1: 실패하는 테스트 작성** (`# Validates: REQ-ALMCHG-004`). `Harness` 로 금요일·월요일 실행을 돌린다.

```python
def test_monday_mail_covers_friday_to_monday(h):     # 금요일(10-16)·월요일(10-19) 실행 → 월요일 메일 기간이 금~월
    subject, text, html = h.change_mails[-1]
    assert subject.startswith("[QA Agent] VXvue 변경 리포트 · 2026-10-16~2026-10-19")
def test_mail_has_values_but_no_bodies(h):           # 상태 전후 값·담당자 이름·댓글 작성자·시각은 있고, 댓글 본문·재현 절차·SRS 본문 문장은 없다
def test_same_day_rerun_sends_one_mail(h):           # 같은 날 scheduled 두 번 → 한 통
def test_collect_failure_mail(h):                    # Polarion 실패 → 제목에 "수집 실패", 목록 없음, 기준 유지 문구
def test_baseline_mail(h):                           # 첫 실행 → "첫 기록을 저장했습니다. 다음 업무일부터 변경을 보냅니다."
def test_empty_day_respects_send_when_empty(h):      # 변경 없음: True → "변경 없음" 메일, False → 보내지 않음
def test_max_items_cuts_with_link(h):                # 이슈 51건, max_items 50 → "나머지 1건은 화면에서 보기"
def test_disabled_setting_sends_nothing(h):          # change_report.enabled False → 자동 메일 없음
def test_manual_and_catchup_runs_send_no_change_mail(h):  # manual·catchup·deferred 실행은 자동 메일을 보내지 않는다
def test_test_mail_kind_change_report():             # send_test("change_report", ...) 본문에 "예시 자료"
def test_mail_button_sends_one_mail(world):          # tests/test_alm_change_page.py: send_report 한 번, 본문에 댓글 본문 없음
def test_mail_button_without_recipients(world):      # 수신자 없음 → 버튼 disabled + "메일 수신자가 설정되지 않았습니다.", POST 는 409
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_mail.py -q` / Expected: FAIL

- [ ] **Step 3: 설정 구현** — `ChangeReportSettings` 와 `config.yaml` 블록(값과 한 줄 설명).

- [ ] **Step 4: 메일 본문 구현** — 구획은 REQ-ALMCHG-004 결과 표 순서. 글 본문은 사양의 예시 모양을 따른다. 이슈 줄은 `ChangeLine.comment_text` 를 쓰지 않는다. 날짜 표시는 `MM-DD(요일)`.

- [ ] **Step 5: 파이프라인 연결** — `trigger in ("scheduled", "collect")` 이고 `change_report.enabled` 이며 `<slug>:change_mail_date` 가 오늘이 아닐 때만 보낸다. 기간은 이슈 비교 기준 기록 날짜(`self.basis["issues"]`, 없으면 SRS)부터 오늘까지. 수집 실패·기준 저장은 실패 메일. 보낸 뒤(실패 포함) 상태 키에 오늘을 적는다. 발송 실패는 `summary["change_mail"]` 에만 남기고 실행 결과를 바꾸지 않는다. AI 단계보다 먼저 보낸다.

- [ ] **Step 6: 시험 발송** — `send_test` 에 `"change_report"` 견본(꺾쇠 자리표시 값과 "예시 자료" 표시), 설정 화면 시험 발송 목록에 "변경 리포트".

- [ ] **Step 7: 화면 메일 버튼** — `changes.html` 에 [이 기간 리포트 메일 보내기]. 수신자가 없으면 `disabled` 와 "메일 수신자가 설정되지 않았습니다.". 누르면 `confirm()` 에 수신자 수를 넣고, 확인하면 `POST /qa-agent/changes/mail` 결과를 버튼 옆에 보인다. 이 수동 메일은 `<slug>:change_mail_date` 를 건드리지 않는다.

- [ ] **Step 8: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_change_mail.py tests/test_alm_change_page.py tests/test_qa_workflow_email.py tests/test_notifier.py -q` / Expected: PASS

- [ ] **Step 9: 커밋** — `git commit -m "feat: 매 업무일 변경 리포트 메일·화면 메일 버튼·시험 발송 (REQ-ALMCHG-004)"`

### Task 7: [밀린 변경 분석] (REQ-ALMCHG-005 3~7번)

**Files:**
- Modify: `app/modules/qa_agent/router.py` (`GET /deferred`, `POST /deferred`), `templates/dashboard.html` (밀린 변경 줄과 버튼)
- Modify: `app/modules/daily_qa/scheduled_jobs.py:64` (`launch_detached(..., deferred_since: str = "", deferred_until: str = "")`)
- Modify: `scripts/run_daily_qa.py` (`--deferred-since`, `--deferred-until`, trigger `deferred`)
- Modify: `app/modules/daily_qa/pipeline.py` (`run_daily`/`_Run` 인자, `_target_events` 분기, 끝날 때 남은 이벤트 되돌리기, 메일 규칙)
- Test: `tests/test_alm_deferred.py` (Task 2 파일에 더한다)

**Interfaces:**
- Consumes: `Contract: 이벤트 상태 deferred`, `Contract: 실행 종류` 의 `deferred`, `store.list_events(statuses=("deferred",), since=, until=)`, `dashboard._utc_bounds`.
- Produces: `GET /qa-agent/deferred?product=&since=&until=` → JSON `{"count": int, "first": "YYYY-MM-DD", "last": "YYYY-MM-DD", "by_analysis": {analysis: int}}`, `POST /qa-agent/deferred` (폼 `product`, `since`, `until`) → 202 `{"status": "launched"}`, 409 은 [변경사항 점검하기]와 같은 문구, 0건이면 400 "고른 기간에 밀린 변경이 없습니다.".

- [ ] **Step 1: 실패하는 테스트 작성** (`# Validates: REQ-ALMCHG-005`)

```python
def test_deferred_summary_counts_by_analysis(world):   # GET /deferred → count, first, last, by_analysis
def test_deferred_run_analyzes_only_chosen_range(env): # 이틀치 중 하루 → 그 이벤트만 done, 나머지 deferred
def test_limit_returns_rest_to_deferred(env):          # FakeRunner 한도 → 남은 이벤트 deferred (pending 아님)
def test_cancel_returns_rest_to_deferred(env):         # 분석 중단 → deferred
def test_deferred_run_mail_follows_policy(env):        # 정책 off → AI 요약 메일 생략 + 이유 기록, on → 한 통
def test_dashboard_hides_button_when_none(world):      # 밀린 변경 0건 → 버튼 없음
def test_empty_range_returns_400(world):               # 문구 그대로
def test_running_returns_409(world):                   # is_running True → 409
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_deferred.py -q` / Expected: FAIL

- [ ] **Step 3: 실행 경로 구현** — `_target_events` 맨 앞 분기: `trigger == "deferred"` 면 기간 안 `deferred` 이벤트를 고르고 곧바로 `pending` 으로 바꾼 뒤 평소처럼 분석한다. 실행 끝(성공·한도·중단·실패 모두)에 고른 번호 가운데 아직 `pending`·`failed` 인 것을 `deferred` 로 되돌린다. 메일 생략 조건에서 `deferred` 는 빠지고(절약 모드의 수동 실행 생략 규칙을 따르지 않는다), 정책 `off` 면 기존 7번 규칙대로 생략된다. 한도 뒤 자동 재개(`_schedule_catchup`)는 `deferred` 실행에서 걸지 않는다.

- [ ] **Step 4: 화면 구현** — 대시보드 "점검 실행" 구역에 "밀린 변경 <n>건 (<첫 날짜> ~ <마지막 날짜>)"과 [밀린 변경 분석]. 누르면 기간 두 칸이 있는 확인 영역을 펼치고 `GET /deferred` 로 건수·분석 종류별 수와 "Claude 사용량을 씁니다"를 보인 뒤 [분석 시작]이 `POST /deferred` 를 부른다. 기존 `_run_progress.html` 진행 표시를 그대로 쓴다.

- [ ] **Step 5: 통과 확인** — Run: `.venv/Scripts/python.exe -m pytest tests/test_alm_deferred.py tests/test_qa_intel_dashboard.py tests/test_qa_limit_alert.py -q` / Expected: PASS

- [ ] **Step 6: 커밋** — `git commit -m "feat: [밀린 변경 분석] 버튼과 실행 (REQ-ALMCHG-005)"`

### Task 8: 추적성·문서·전체 검증·push

**Files:**
- Modify: `specs/alm-change-report.md` 12절(Implementation 에 새 파일 추가, Test 에 테스트 파일 경로, 상태), `specs/qa-intelligence.md` 12절(REQ-QAINTEL-001·002·005·027·035·038 행에 새 테스트 경로), `CHANGELOG.md`, `progress.md`, `README.md`(변경 리포트 한 줄과 화면 주소), `docs/README.md`(문서 지도에 계획 문서), `docs/SPEC.html`

- [ ] **Step 1: 추적성 갱신** — 게이트가 도는 자동 테스트가 있는 행은 `verified`, 화면 캡처만 있는 부분이 남으면 `implemented`.
- [ ] **Step 2: 문서 갱신** — CHANGELOG 항목은 ID 를 앞에 둔다. progress 에 확인한 것과 미확인(운영 배포, 실제 Polarion 이틀 비교)을 나눠 적는다.
- [ ] **Step 3: 완료 정리** — Run: `node "C:/Users/2024980/Documents/자동화/tools/cleanup-workspace.js" --completed <작업 폴더> --apply --auto` / Expected: `실패 0개`
- [ ] **Step 4: 전체 게이트** — Run: `.venv/Scripts/python.exe -m pytest -q` 를 백그라운드로 돌리고 출력 파일을 중간중간 읽는다 / Expected: 실패 0
- [ ] **Step 5: 준비 검사** — Run: `node .project-check/project-readiness.js .` / Expected: `failed=0`
- [ ] **Step 6: 실제 자료 확인** — 운영 서버의 이슈·SRS 스냅샷 날짜 목록을 읽기 전용으로 확인한다. 이틀 이상 있으면 그 사본으로 로컬 `/qa-agent/changes` 를 열어 Polarion 원본(`get_polarion_item`, 댓글 작성 시각·현재 상태)과 다섯 건 이상 대조한다. 기록이 없으면 그 사실을 progress 에 적는다.
- [ ] **Step 7: 커밋·push** — `git commit -m "docs: ALM 변경 리포트 구현 완료 기록 (REQ-ALMCHG-001~005)"` 뒤 `git push`.
