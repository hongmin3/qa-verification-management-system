# 설정 탭·한도 알림·분석 상세와 결과 목록 개정 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영에만 있는 절약 모드 코드를 master에 합치고, 메일·한도 알림을 관리자에게 확실히 보내며, 관리자 설정 탭과 다시 짠 분석 상세·결과 목록을 제공한다.

**Architecture:** 핵심 앱(FastAPI + Jinja2 + SQLite)만 바꾼다. 화면 재구성·결과 목록·설정은 AI 호출 0회다. 이슈 본문은 이미 서버에 저장된 이슈 스냅샷에서 읽고, 설정 값은 기존 `SettingOverrides`(REQ-AICALL-007)를 넓혀 저장한다.

**Tech Stack:** FastAPI, Jinja2, SQLite, 기존 JavaScript, pytest, 창 없는 브라우저 시험. 새 의존성 없음(Python 표준 `hashlib`·`hmac`·`secrets`).

**Spec:** `SPEC.md`(REQ-MAIL-001~003, REQ-WEB-007, REQ-AICALL-007, NFR-SEC-004), `specs/qa-intelligence.md`(REQ-QAINTEL-019·020·025·039·042·046·047·048). 새 요구사항은 각 Task 1단계에서 먼저 사양에 추가한다.

## 0. 작업 시작 시 확인한 사실 (2026-10-15 09:00~10:00 KST, 읽기 전용)

| 질문 | 확인한 사실 | 근거 |
|---|---|---|
| 어제 어디까지 구현됐나 | master `f07ffb8`(10-14 15:33)까지는 입력 크기 개선(REQ-QAINTEL-040·041)과 절약 계획 문서다. 그 뒤 `codex/qa-input-budget` `b8cf77d`(10-14 17:42)가 REQ-QAINTEL-042~048(선택 분석·하루 4회 예산·Sonnet·대상별 재사용·작성자 조회·처리 상태·통합 메일)을 구현했다. 이 커밋은 운영에 44개 파일로 배포됐지만 **master에 병합되지 않았다** | `git log master..codex/qa-input-budget`, 그 브랜치 `progress.md` |
| 캡처의 14건 목록은 무엇인가 | 오늘 분석이 아니다. 10-14 17:41 KST 운영 확인용 "이슈 변경 분석 준비" 실행 `20261014-174057-vxvue`가 만든 준비 목록이다(수정 완료 7·새 댓글 7, AI 0회). 18:11 KST에 만료됐지만 `/qa-agent/plans/latest`가 만료를 보지 않고 가장 최근 목록을 돌려줘 체크박스가 계속 보인다 | 운영 DB `qa_execution_plans`, `app/modules/qa_agent/router.py` `latest_plan` |
| 오늘 자동 분석을 했나 | 했다. `20261015-040000-vxvue`, 04:00~04:03 KST, 결과 `PARTIAL`. Claude 2회(Sonnet 5.5)로 VP-580 사양 분석·VP-7257 새 댓글 분석 2건을 만들었고 둘 다 필수 확인이 남아 실패로 표시됐다. 수정 완료 1건과 나머지 묶음은 예산(실행당 2회) 초과로 대기다. 제품 대기 변경 12건 | 운영 DB `daily_qa_runs`, `daily_qa_findings` 290·291 |
| 메일은 어떻게 오나 | 실행마다 한 통. 제목 `[QA] vxvue <검증 버전> · 사양 N / 이슈 N · 미분석·대기 N`. 본문은 비교 기준일·대기 건수·사양/이슈 결과 링크·판정 건수·이슈 작성자별 묶음(최신 업데이트순)·예산 소진 안내다 | `app/modules/daily_qa/report.py` `_workflow_email` |
| 왜 메일이 안 왔나 | `DAILY_QA_EMAIL_TO`가 없어 `NOTIFY_EMAIL_TO`(관리자 주소와 일치)로 보냈다. Gmail SMTP가 받아들였고 실행 기록은 `메일 sent`다(10-14 13:35, 10-15 04:03 두 번). 서버 이후 구간(Gmail 반송, 회사 메일 차단·스팸 격리)은 서버에서 확인할 수 없다 | `secrets.txt` 키 이름, `output/logs/daily_qa.out` |
| 세션 한도 알림은 있나 | 없다. 한도를 감지하면 `limit_evidence.capture`로 화면 증거만 남긴다. `notifier.notify_for_error`는 Gemini 오류에서만 부른다 | `pipeline.py:314`, `gemini_client.py:197` |
| VP-7287은 신규 이슈인가 | 아니다. 2026-09-21 16:37 등록, 연구소 결과 `FIXED`, 상태 `reviewed`. 10-13 17:20 QA 재현 댓글 때문에 수정 완료 이슈로 다시 분석됐다. 본문은 `reproduction_step`(설명 칸은 비어 있음)에 있는데 상세 화면은 제목만 보여 줘 재현 댓글이 본문처럼 보였다. 분석(10-14 13:35) 뒤 18:09 "수정완료했습니다" 댓글이 달렸고 그 분석은 대기 중이다 | `data/daily_qa/snapshots/vxvue/issues/2026-10-15.json`, 결과 254·288, 이벤트 186 |
| 결과 목록 상태 | 결과 291건 중 214건은 분석 종류가 빈 이전 형식이다. 이슈 29개는 결과가 2건 이상이라 같은 이슈 카드가 겹친다 | 운영 DB 집계 |

재사용할 코드: `SettingOverrides`·`ALLOWED_KEYS`·`app_setting_changes`(설정 저장·이력), `notifier.notify`·`notifications` 테이블(재발송 대기), `limit_evidence.episode_key`(같은 한도 묶음 판별), `qa_issue_reporters`(작성자 이름), `result_views.search`(DB 단계 필터·페이지), `report._issue_author_lines`(작성자별 묶음). 새로 만드는 것은 관리자 인증(기존 앱에 로그인이 없음, NFR-SEC-004)과 이슈 스냅샷 조회기뿐이다.

## Global Constraints

- 화면 조회·검색·필터·설정·준비 목록은 Claude 호출 0회다(REQ-QAINTEL-042).
- 분석 결과 원문·최초 분석 시각·모델·QA 처리 이력은 바꾸지 않는다. 화면은 배치만 바꾼다(REQ-QAINTEL-046·047).
- 이슈 작성자·댓글 작성자 ID와 이름은 화면 표시용이다. AI 입력에 넣지 않는다(2026-10-14 결정).
- 메일 주소·SMTP 계정·관리자 비밀번호는 저장소·로그·화면 응답에 쓰지 않는다. 화면은 마스킹(`hon***@vieworks.com`)만 보인다(REQ-MAIL-001, NFR-SEC-002).
- 사양에 근거가 없는 Expected·판정을 화면이 만들어 내지 않는다(규칙 §55, `REF-qa-agent-architecture#automation-prohibitions`).
- 390px·1920px에서 가로 넘침이 없어야 한다. 자바스크립트가 실패해도 본문을 읽을 수 있어야 한다.
- 운영 배포 전 DB·코드를 `/home/ubuntu/qa-deploy-backups/<작업>-<UTC시각>/`에 백업하고, 배포 뒤 파일 해시를 비교한다.

## Shared Interfaces

### Contract: AdminSession
쿠키 `qa_admin`, 경로 `/`, `HttpOnly`, `SameSite=Strict`, 유효 8시간. 값은 `<만료 epoch>.<hmac_sha256(서버 키, 만료 epoch)>`. 서버 키는 `data/admin_session.key`(최초 실행 때 `secrets.token_bytes(32)`로 생성, 0600). `app/core/admin_auth.py`가 `issue(now) -> str`, `verify(cookie, now) -> bool`, `check_password(plain) -> bool`, `set_password(plain, actor) -> None`을 제공한다.

### Contract: AdminSettingKeys
`SettingOverrides`에 더하는 키와 값 형식. 화면 값이 `secrets.txt`보다 먼저 적용된다.

| 키 | 형식 | 기본 | 뜻 |
|---|---|---|---|
| `notifications.admin_email` | 메일 주소 1개 | `NOTIFY_EMAIL_TO`의 첫 주소 | 한도·오류 알림을 받는 관리자 |
| `daily_qa.report_recipients` | 쉼표로 나눈 주소 목록, 1~10개 | `notifications.admin_email` | 예약 점검 요약 메일 수신자 |
| `daily_qa.savings.analysis_model` / `review_model` | `choices()` 목록 값 | `claude-sonnet-5-5` / `claude-opus-5-5` | 일반 분석·선택 재검토 모델 |
| `daily_qa.savings.per_run` / `per_day` | 정수 1~10 / 1~20 | 2 / 4 | 호출 예산 |
| `admin.password_hash` | `pbkdf2_sha256$<반복>$<salt hex>$<hash hex>` | 없음(없으면 초기 비밀번호 `1`) | 관리자 비밀번호 |

### Contract: IssueBody
`app/modules/qa_agent/issue_body.py` `load(product_slug: str, issue_id: str) -> IssueBody | None`. 최신 이슈 스냅샷(`data/daily_qa/snapshots/<slug>/issues/<날짜>.json`의 `items`)에서 읽는다. 필드: `id, title, status, rd_result, created, updated, reporter_id, reporter_name, description, reproduction_step, occurrence_cause, action_details, occurred_versions, target_versions, comments[{id, created, author_id, author_name, text}]`. 파일 수정 시각 기준으로 한 번만 읽어 둔다.

### Contract: FreshnessState
`app/modules/qa_agent/dashboard.py` `freshness(finding: dict, issue: IssueBody | None, events: list[dict]) -> dict` → `{"state": "current"|"changed_after"|"unknown", "after_events": int, "latest_update": str}`. 분석 시각 뒤에 생긴 같은 이슈의 대기 이벤트가 있거나 이슈 업데이트 시각이 분석 시각보다 늦으면 `changed_after`다.

## Review Focus

1. 준비 목록이 만료된 뒤 페이지를 다시 연 경우: 체크박스 없이 "만료됨 · 다시 준비" 안내가 보여야 한다(Task 2).
2. 이슈 스냅샷에 없는 번호(삭제·다른 제품·SRS)의 상세: 본문 칸이 `원문 스냅샷 없음`으로 표시되고 화면이 깨지지 않아야 한다(Task 6).
3. 같은 한도에 하루 여러 번 걸린 경우: 관리자 알림은 한도 묶음당 1통이어야 한다(Task 4).
4. 관리자 쿠키 없이 설정 저장 API를 직접 부른 경우: 403이고 값이 바뀌지 않아야 한다. 기존 서버 직접 접속(SSH 터널) 저장 경로도 계속 동작해야 한다(Task 5).
5. 분석 종류가 빈 이전 형식 결과 214건: 결과 목록·상세에서 `이전 형식`으로 열리고 오류가 없어야 한다(Task 6·7).

---

### Task 1: `codex/qa-input-budget`를 master에 병합해 운영과 맞추기

**Files:**
- Modify: master 작업 폴더(병합 커밋), `docs/SPEC.html`(다시 생성), `progress.md`
- Test: 전체 `pytest`

**Interfaces:**
- Consumes: 없음
- Produces: 운영 코드와 같은 master. 이후 모든 Task는 이 master에서 이 브랜치를 갱신한 뒤 시작한다.

- [ ] **Step 1: 작업 폴더 상태 확인**

`C:/Users/2024980/.codex/worktrees/fa9c/...`의 미커밋 변경은 키트 관리 파일(`.project-check/*`, `AGENTS.md`)과 `akela/learnings-log.jsonl`뿐이어야 한다. 기능 코드 변경이 있으면 멈추고 사용자에게 보고한다. master 작업 폴더의 `M AGENTS.md`와 `docs/SPEC.html.tmp-*`는 건드리지 않는다.

- [ ] **Step 2: 운영 파일과 브랜치 비교**

운영 `/home/ubuntu/ai-regression-impact-analyzer`의 코드 44개 파일 SHA256을 `b8cf77d`와 비교한다. 다르면 운영 쪽 차이를 먼저 보고한다(덮어쓰지 않는다).

- [ ] **Step 3: 병합**

master 작업 폴더에서 `git merge --no-ff codex/qa-input-budget`. 충돌은 `CHANGELOG.md`·`progress.md`·`docs/README.md`의 같은 자리 추가뿐일 것으로 본다. 양쪽을 모두 남기고 `node .project-check/render-spec-html.js .`로 `docs/SPEC.html`을 다시 만든다. rebase·force push는 하지 않는다.

- [ ] **Step 4: 전체 시험(백그라운드, 진행 확인)**

Run: `python -m pytest -q -p no:cacheprovider --basetemp <독립 임시 폴더> 2>&1 | tee <scratch>/pytest-full.log` (출력을 파일로 남겨 진행률을 볼 수 있게 한다)
Expected: 실패 0. 그 브랜치 기록의 이전 전체 결과(1,606 통과·8 제외·8 실패, 이후 개별 재통과)보다 실패가 늘지 않아야 한다.

- [ ] **Step 5: 준비 검사·완료 정리·push**

Run: `node .project-check/project-readiness.js .` → 오류 0. 키트 루트에서 `node tools/cleanup-workspace.js --completed <master 폴더> --apply --auto`. `git push origin master`. 이어서 이 브랜치에 `git merge master`.

### Task 2: 준비 목록 만료 처리와 목록 행 정보 (REQ-QAINTEL-042)

**Files:**
- Modify: `specs/qa-intelligence.md`(REQ-QAINTEL-042 결과에 만료 표시·행 정보 추가), `app/modules/qa_agent/router.py` `latest_plan`, `app/modules/daily_qa/execution_plan.py` `public`, `app/web/static/qa-selection.js`, `app/modules/qa_agent/templates/_intel_style.html`
- Test: `tests/test_qa_execution_plan.py`, `tests/qa_run_progress_ui.cjs`

**Interfaces:**
- Consumes: Task 1의 `execution_plan.public(plan)`
- Produces: `public(plan, now) -> dict`에 `expired: bool`, 대상마다 `kind_label`, `reporter_name`, `issue_updated_kst`, `reason`(변경 이벤트 이름) 추가

판정: 실행은 이미 30분 뒤 거절하므로(사양 충족) 화면 표시만 사양에 더한다.

- [ ] **Step 1: 사양 추가** — "30분이 지난 준비 목록은 체크박스 없이 `준비 목록 만료 (준비 <KST 시각>) · 다시 준비하세요`로 표시한다. 각 행은 체크박스·번호·제목·분석 종류·작성자·이슈 업데이트(KST)·분석 이유를 보인다."
- [ ] **Step 2: 실패 시험 작성**

```python
def test_public_plan_marks_expired_after_30_minutes():
    plan = make_plan(created="2026-10-14T08:41:15+00:00")
    data = execution_plan.public(plan, now=datetime(2026, 10, 14, 9, 11, 16, tzinfo=timezone.utc))
    assert data["expired"] is True

def test_public_plan_rows_carry_kind_reporter_and_update():
    row = execution_plan.public(make_plan(), now=CREATED)["targets"][0]
    assert row["kind_label"] == "수정 완료 이슈"
    assert row["issue_updated_kst"].endswith("(한국 시간)")
```

브라우저 시험: 만료 목록이면 `#qa-selection-targets input[type=checkbox]` 개수 0, `선택 대상 분석` 비활성.
- [ ] **Step 3: 시험 실패 확인** — `pytest tests/test_qa_execution_plan.py -k "expired or rows_carry" -v` → FAIL
- [ ] **Step 4: 구현** — 체크박스를 행 왼쪽에 두고 행을 `<label>` 전체 클릭 영역으로 만든다(캡처의 오른쪽 끝 체크박스 해소).
- [ ] **Step 5: 시험 통과 확인 후 커밋**

### Task 3: 메일 발송 기록과 화면 시험 발송 (REQ-MAIL-002·003)

**Files:**
- Modify: `SPEC.md` REQ-MAIL-002(결과에 `message_id` 추가)·REQ-MAIL-003(설정 탭 시험 발송 추가), `app/core/notifier.py` `send_report`·`notify`, `app/modules/daily_qa/pipeline.py`(메일 단계 note), `app/modules/daily_qa/report.py`
- Test: `tests/test_notifier.py`, `tests/test_qa_workflow_email.py`

**Interfaces:**
- Produces: `send_report(...) -> {"status", "recipients", "message_id"}`; 메일 단계 note `발송 <KST 시각> · 수신 <마스킹 주소> · Message-ID <값>`; `send_test(kind: str, recipients: tuple[str, ...]) -> dict` (Task 5 화면이 사용)

- [ ] **Step 1: 사양 수정**
- [ ] **Step 2: 실패 시험** — `test_send_report_returns_message_id`(가짜 SMTP가 받은 메시지의 `Message-ID` 헤더와 결과 값이 같다), `test_mail_stage_note_masks_recipient`(note에 `@vieworks.com` 앞부분 원문이 없다)
- [ ] **Step 3: 실패 확인 → Step 4: `email.utils.make_msgid(domain=발신 도메인)`로 헤더를 직접 넣고 반환 → Step 5: 통과·커밋**

사용자 확인(코드 밖): 회사 메일 스팸·격리함에서 발신 Gmail 계정의 `[QA] vxvue` 메일, Gmail 발신 계정 받은편지함의 반송(Mail Delivery Subsystem) 메일을 찾는다. 회사 메일이 외부 Gmail 발신을 막으면 사내 SMTP 사용 여부를 결정한다(13절 확인 필요).

### Task 4: Claude 세션·주간 한도 알림을 관리자에게 (새 REQ-QAINTEL-049)

**Files:**
- Modify: `specs/qa-intelligence.md`(REQ-QAINTEL-049 추가, 추적성 행), `app/core/notifier.py`(`notify`에 `recipients` 인자), `app/modules/daily_qa/pipeline.py:314` 부근
- Create: 없음
- Test: `tests/test_qa_limit_alert.py`

**Interfaces:**
- Consumes: `AdminSettingKeys.notifications.admin_email`, `limit_evidence.episode_key(limit)`
- Produces: `notifier.notify(kind, detail, context=None, storage=None, recipients=()) -> dict`

사양 문장: "한도(세션·주간·불명)를 처음 감지하면 관리자 주소로 `[QA] Claude <한도 종류> 도달 · 초기화 <KST 시각>` 메일을 보낸다. 본문에는 실행 번호, 남은 대기 건수, 증거 파일 이름을 넣는다. 같은 한도 묶음(`episode_key`)에는 다시 보내지 않는다."

- [ ] **Step 1: 사양 추가**
- [ ] **Step 2: 실패 시험**

```python
def test_session_limit_sends_one_admin_mail_per_episode(fake_smtp, store):
    run_pipeline_hitting_limit(store, kind=KIND_SESSION, times=2)
    assert [m["To"] for m in fake_smtp.sent] == ["admin@example.test"]
    assert "Claude 세션(5시간) 사용량 한도" in fake_smtp.sent[0]["Subject"]

def test_limit_alert_skipped_without_admin_email(fake_smtp, store):
    run_pipeline_hitting_limit(store, kind=KIND_WEEKLY, admin_email="")
    assert fake_smtp.sent == []
```

- [ ] **Step 3: 실패 확인 → Step 4: 증거 캡처 직후 `notify(KIND_CLAUDE_USAGE_LIMIT, ..., recipients=(admin,))` 호출, 재발송 키는 `claude_usage_limit:<episode_key>` → Step 5: 통과·커밋**

### Task 5: 상단 `설정` 탭과 관리자 로그인 (새 REQ-WEB-008, NFR-SEC-004 수정)

**Files:**
- Modify: `SPEC.md`(REQ-WEB-007 메뉴 표, 새 REQ-WEB-008, NFR-SEC-004 "설정 저장은 관리자 로그인 또는 서버 직접 접속"), `app/web/templates/_nav.html`, `app/web/settings_router.py`, `app/core/setting_overrides.py`(`ALLOWED_KEYS`·`SETTING_LABELS`·`validate`), `app/core/notifier.py`·`app/modules/daily_qa/settings.py`(수신자 읽기 순서)
- Create: `app/core/admin_auth.py`, `app/web/templates/settings.html`, `app/web/templates/admin_login.html`
- Test: `tests/test_admin_auth.py`, `tests/test_settings_screen.py`

**Interfaces:**
- Consumes: `Contract: AdminSession`, `Contract: AdminSettingKeys`, Task 3 `send_test`
- Produces: `require_admin(request) -> str`(관리자 쿠키 또는 기존 `can_edit` 통과 시 행위자 이름, 아니면 403)

화면(`/settings`, 메뉴 이름 `설정`, 기존 `AI 분석 설정` 메뉴를 대체하고 `/settings/ai`는 `/settings#ai`로 이동):

1. 로그인 전: 현재 값 읽기 전용 + 비밀번호 입력 한 칸. 초기 비밀번호는 `1`이다.
2. 로그인 후 구획: `관리자 메일`, `자동 보고 메일 받는 사람`(현재는 관리자 1명, 추가·삭제 가능), `메일 시험 발송`, `AI 모델과 호출 예산`(기존 AI 설정 표 포함), `관리자 비밀번호 변경`, `변경 이력`(기존 `app_setting_changes`).
3. 초기 비밀번호를 쓰는 동안 상단에 `초기 비밀번호 사용 중 · 변경하세요` 배너.

- [ ] **Step 1: 사양 추가·수정**
- [ ] **Step 2: 실패 시험**

```python
def test_login_with_initial_password_sets_cookie(client):
    response = client.post("/settings/login", data={"password": "1"})
    assert response.status_code == 303
    assert "qa_admin=" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]

def test_save_without_admin_cookie_is_refused(remote_client):
    assert remote_client.post("/settings", json={"daily_qa.report_recipients": "x@example.test"}).status_code == 403

def test_report_recipients_override_secret(store, settings):
    save_override(settings, "daily_qa.report_recipients", "a@example.test")
    assert daily_qa_settings(settings).email_to == ("a@example.test",)

def test_password_hash_never_returned(client_admin):
    assert "pbkdf2" not in client_admin.get("/settings/values").text
```

- [ ] **Step 3: 실패 확인**
- [ ] **Step 4: 구현** — 비밀번호는 PBKDF2-SHA256 600,000회. 로그인 실패 5회면 10분 잠금(메모리 카운터). 저장 API는 `Origin` 검사(기존 `can_edit`의 동일 출처 조건)를 유지한다. 수신자 읽기 순서: 화면 값 → `DAILY_QA_EMAIL_TO` → `NOTIFY_EMAIL_TO`.
- [ ] **Step 5: 통과·커밋**

> **주의** 서버는 평문 HTTP다. 비밀번호가 사내망에서 그대로 전달된다. 초기값 `1`은 사용자 요청이며, 외부 노출 전에는 HTTPS를 먼저 정한다(NFR-SEC-004).

### Task 6: 분석 상세 화면 다시 짜기 (REQ-QAINTEL-020 사양 변경)

**Files:**
- Modify: `specs/qa-intelligence.md` REQ-QAINTEL-020(화면 순서 표 교체), `app/modules/qa_agent/templates/finding_detail.html`, `app/modules/qa_agent/dashboard.py`(`finding_explanation`, 새 `freshness`, `VALIDITY_LABEL`), `app/modules/qa_agent/router.py` 상세 경로, `app/web/static/qa-reading.js`(탭 제거)
- Create: `app/modules/qa_agent/issue_body.py`
- Test: `tests/test_qa_finding_detail_layout.py`, `tests/test_qa_issue_body.py`

**Interfaces:**
- Consumes: `Contract: IssueBody`, `Contract: FreshnessState`, `qa_issue_reporters`
- Produces: 상세 템플릿 문맥 `issue`, `freshness`, `trigger`(분석을 만든 이벤트 목록), `verdict_view`

화면 순서(위에서 아래, 탭 없이 한 흐름):

| 순서 | 구획 | 내용 | 비고 |
|---|---|---|---|
| 1 | 머리 | `[번호] 제목` · 원문 열기. 둘째 줄을 크게: **작성자 이름** · 등록일 · **최근 업데이트** · 상태 · 연구소 결과 | 이름이 없으면 `이름 미확인 (ID)` |
| 2 | 최신성 | `changed_after`이면 `분석(<시각>) 이후 변경 <N>건 · 재분석 대기` 배너 | 화면만 표시, 자동 AI 호출 없음 |
| 3 | 분석 이유 | `왜 분석했나: <KST 시각> 새 댓글` 처럼 이벤트를 중복 없이 한 줄로 | 지금의 `댓글 추가 · 댓글 추가` 반복 제거 |
| 4 | 이슈 본문 | 설명·재현 절차·발생 원인·조치 내용·발생/목표 버전. 긴 글은 8줄 뒤 펼치기 | 스냅샷에 없으면 `원문 스냅샷 없음` |
| 5 | AI 판단 | 종류별 질문에 대한 결론 한 줄 + 판정 + 신뢰도 + 근거 사양 3개 | 아래 종류별 표 |
| 6 | QA 할 일 | 최대 3개, 그 아래 `확인하지 못한 자료` | 기존 `card.todos` |
| 7 | 댓글 | 시간순 전체. 분석에 쓰인 댓글은 해석·QA 영향을 붙이고, 분석 뒤 댓글은 `분석 이후` 표시 | 해석을 만들지 않는 분석 종류에는 `해석 없음` 문구를 숨긴다 |
| 8 | 근거 자료 | 판단 근거 전체, 관련 과거 이슈, 선택 버전 TC 비교, 회귀 축 표 | 기존 종류별 블록 재사용 |
| 9 | 분석 기록(접힘) | 모델·추론·실행 번호·분석 메모 원문·근거 검증 기록·변경 이벤트·확인 자료 구분 누락 안내 | 지금 위쪽 주황 배너와 메모를 여기로 이동 |
| 10 | 처리 상태 | 상태·처리자·메모 저장, 이력 | 넓은 화면은 오른쪽 고정 |

종류별 AI 판단 질문(코드가 기존 판정 값을 이름만 바꿔 보인다. AI 출력 형식은 바꾸지 않는다):

| 분석 종류 | 화면 질문 | 판정 값 → 표시 |
|---|---|---|
| 신규 이슈(`NEW_ISSUE`) | 사양으로 보면 이슈가 맞는가 | 사양과 다름 → `이슈로 보임` · 사양대로 동작 → `사양상 정상, 이슈 아닐 수 있음` · 사양 미정의 → `사양 미정의, 기획 확인 필요` · 중복 후보 있으면 `중복 의심` 표시 추가 |
| 수정 완료(`FIXED_ISSUE`) | 원인 설명과 수정이 사양에 맞는가 | 원인 검토·수정 검토 요약 + 회귀 위험 축 중 `해당` 개수 |
| 새 댓글(`COMMENT`) | 새 댓글이 QA 판단을 바꾸는가 | 댓글 분류 + QA 영향 |
| Spec 판정(`SPEC_DECISION`) | 연구소 설명이 사양으로 뒷받침되는가 | 기존 판정 |
| 사양 변경(`SPEC_COVERAGE`) | 바뀐 사양을 TC가 덮는가 | 기존 Checklist 범위 |
| 빈 종류(이전 형식) | `이전 형식 결과` | 저장된 요약·상세만 |

판정 값 이름 매핑의 실제 코드 값은 `app/modules/daily_qa/skills/qa-new-issue-analysis/SKILL.md` 출력 형식에서 가져와 `VALIDITY_LABEL`에 둔다. 매핑에 없는 값은 원래 값을 그대로 보이고 `검토 필요`로 둔다.

VP-7287에 적용하면(운영에서 읽은 값만 사용):

```text
[VP-7287] [레드마인 #15859 재발생] 동물용 Sequence 관련 Tag 값을 None 으로 변경하여도 Sequence 값 그대로 전송됨
정지혜 · 등록 2026-09-21 16:37 · 최근 업데이트 2026-10-14 18:09 · reviewed · 연구소 FIXED
⚠ 분석(2026-10-14 13:35) 이후 변경 1건 · 재분석 대기
왜 분석했나: 2026-10-13 17:20 새 댓글
이슈 본문: 재현 절차 [Precondition] 동물용 뷰어 … / 발생 원인 Edit Patient에서 None으로 수정하면 Key값은 수정 … / 조치 Edit Patient 시, 선택 항목에 값이 있을 때만 새 값으로 채우도록 수정
AI 판단 (수정 완료): <결과 288 요약> · 판정 사양 미정의 · 신뢰도 확인 필요
QA 할 일: <결과 288의 할 일 3개>
댓글: 1) 2026-10-13 17:20 · <ID 2021659의 이름> · 분석에 사용   2) 2026-10-14 18:09 · <ID 2021659의 이름> · 분석 이후
```

댓글 작성자 이름은 `qa_issue_reporters`(제품·이슈별 작성자 표)에서 같은 `reporter_id`의 이름을 찾아 보인다. 운영에서 ID 2021659는 이 표에 6건 있어 이름을 찾을 수 있다. 표에 없는 ID는 `이름 미확인 (ID)`로 보인다. 이 조회는 `IssueBody.comments[].author_name`을 채우는 `issue_body.load`가 맡는다.

- [ ] **Step 1: 사양 수정**(위 화면 순서 표와 종류별 질문 표를 REQ-QAINTEL-020에 넣는다)
- [ ] **Step 2: 실패 시험**

```python
def test_issue_body_reads_reproduction_step(snapshot_dir):
    issue = issue_body.load("vxvue", "VP-7287")
    assert issue.reproduction_step.startswith("[Title]")
    assert issue.description == ""

def test_detail_shows_body_before_ai_judgement(client, fixed_issue_finding):
    html = client.get(f"/qa-agent/findings/{fixed_issue_finding}").text
    assert html.index("이슈 본문") < html.index("AI 판단")

def test_detail_marks_changes_after_analysis(client, finding_with_later_comment):
    assert "이후 변경 1건" in client.get(f"/qa-agent/findings/{finding_with_later_comment}").text

def test_trigger_events_are_deduplicated(client, finding_with_two_comment_events):
    assert "댓글 추가 · 댓글 추가" not in client.get(f"/qa-agent/findings/{finding_with_two_comment_events}").text

def test_new_issue_shows_validity_question(client, new_issue_finding):
    assert "사양으로 보면 이슈가 맞는가" in client.get(f"/qa-agent/findings/{new_issue_finding}").text

def test_missing_snapshot_and_legacy_kind_render(client, legacy_finding):
    html = client.get(f"/qa-agent/findings/{legacy_finding}").text
    assert "원문 스냅샷 없음" in html and "이전 형식 결과" in html
```

- [ ] **Step 3: 실패 확인**
- [ ] **Step 4: 구현** — 기존 종류별 블록(`view == ...`)은 8번 근거 자료 구획으로 옮겨 재사용한다.
- [ ] **Step 5: 브라우저 확인** — 운영 결과 사본으로 390px·1920px, 결과 288·290·291과 이전 형식 1건. 가로 넘침·JavaScript 오류 없음.
- [ ] **Step 6: 통과·커밋**

### Task 7: 분석결과 목록 다시 짜기 (REQ-QAINTEL-019·046 사양 변경)

**Files:**
- Modify: `specs/qa-intelligence.md` REQ-QAINTEL-019·046, `app/modules/qa_agent/templates/dashboard.html`(분석결과 구획), `app/modules/qa_agent/templates/results.html`, `app/modules/qa_agent/result_views.py` `search`, `app/modules/qa_agent/dashboard.py` `card`
- Create: `app/modules/qa_agent/templates/_result_filters.html`, `app/modules/qa_agent/templates/_result_card.html`
- Test: `tests/test_qa_results_reader.py`, `tests/test_qa_intel_dashboard.py`

**Interfaces:**
- Consumes: `Contract: FreshnessState`, Task 1 `result_views.search(choice, filters)`
- Produces: `filters`에 `view: "issue"|"all"`(기본 `issue`), `quick: "review"|"open"|"changed"|""`; `card()`에 `freshness`, `trigger`, `history_count`

목록 개선안(카드 하나씩 확인한 결과):

| 지금 | 문제 | 바꾸는 방법 |
|---|---|---|
| 같은 이슈 결과가 카드 여러 장(29개 이슈) | 무엇이 최신인지 모름 | 이슈당 최신 결과 1장, `이전 분석 N건` 링크. `모든 결과 보기`로 전환 가능 |
| 작성자·업데이트가 회색 작은 글씨 | 담당자가 자기 이슈를 못 찾음 | 카드 첫 줄에 **작성자 이름 칩**과 **이슈 업데이트(KST, `N시간 전`)**를 굵게 |
| 판정·종류·처리 상태가 흩어짐 | 우선순위를 못 정함 | 둘째 줄에 분석 종류 태그·판정 색·처리 상태를 한 줄로 |
| 분석 뒤 이슈가 바뀌어도 표시 없음 | 낡은 결론으로 일함 | `분석 이후 변경` 경고 배지(Task 6 `freshness`) |
| 왜 분석했는지 없음 | 신규인지 댓글인지 모름 | `왜 분석했나` 한 줄(새 댓글·신규 등록·상태 변경) |
| 대시보드는 정렬만, 검색은 다른 페이지 | 조회가 두 번 걸림 | 대시보드와 결과 페이지가 같은 필터: 검색창 1개(번호·제목·작성자·요약) + 빠른 칩(`검토 필요`·`미처리`·`분석 이후 변경`·`이슈`·`사양`) + 정렬 + `자세한 조건` 접기(기간·판정·처리 상태) |
| 요약이 길게 펼쳐짐 | 한 화면에 3~4장 | 요약 2줄 자르기, 할 일 최대 3개 |
| 이전 형식 214건이 섞임 | 종류 필터에서 사라짐 | `이전 형식` 태그와 칩. 기본 목록에는 포함 |
| 작성자별 보기 없음 | 메일은 작성자별인데 화면은 아님 | `작성자별로 묶기` 전환(메일 `_issue_author_lines`와 같은 순서) |

기본 정렬은 이슈 최신 업데이트순이다. 필터는 DB 조회 단계에서 적용하고 50개씩 나눈다(REQ-QAINTEL-046 유지).

- [ ] **Step 1: 사양 수정**
- [ ] **Step 2: 실패 시험**

```python
def test_issue_view_keeps_latest_finding_per_subject(store):
    seed_findings(store, [("VP-7287", "2026-10-14T23:05:19Z"), ("VP-7287", "2026-10-14T04:35:47Z")])
    cards = result_views.search(choice, {"view": "issue"})["cards"]
    assert [c["subject"] for c in cards] == ["VP-7287"] and cards[0]["history_count"] == 2

def test_search_box_matches_reporter_name(store):
    assert result_views.search(choice, {"q": "정지혜"})["total"] >= 1

def test_quick_changed_filter_uses_freshness(store):
    assert all(c["freshness"]["state"] == "changed_after" for c in result_views.search(choice, {"quick": "changed"})["cards"])

def test_card_header_emphasizes_reporter_and_update(client):
    html = client.get("/qa-agent/results?product=VXvue").text
    assert 'class="qa-reporter"' in html and 'class="qa-updated"' in html
```

- [ ] **Step 3: 실패 확인 → Step 4: 구현 → Step 5: 390px·1920px 브라우저 확인 → Step 6: 통과·커밋**

### Task 8: 문서·전체 검증·배포·master 병합

**Files:**
- Modify: `CHANGELOG.md`(ID 앞머리), `progress.md`, `README.md`(설정 탭 사용법 한 줄), `docs/README.md`(이 계획 행), 화면 안 사용법 `/qa-agent/guide`, `docs/SPEC.html`

- [ ] **Step 1:** `node .project-check/render-spec-html.js .`
- [ ] **Step 2:** 키트 루트에서 `node tools/cleanup-workspace.js --completed <이 작업 폴더> --apply --auto`
- [ ] **Step 3:** 전체 `pytest`(Task 1 Step 4와 같은 방법) → 실패 0. `node .project-check/project-readiness.js .` → 오류 0
- [ ] **Step 4: 이 브랜치를 master에 병합·push** — 다른 세션이 같은 파일을 고치는 중이면 push를 미루고 보고한다.
- [ ] **Step 5: 운영 배포** — 백업 → 파일 복사 → `systemctl restart qa-verification` → `active`·`/health` 200 → 배포 파일 해시 비교 → `/settings`에서 관리자 메일과 보고 수신자를 관리자 주소로 저장 → 메일 시험 발송 1통(사용자 수신 확인 요청) → 운영 화면 390px·1920px 확인
- [ ] **Step 6:** 다음 예약 실행(평일 04:00 KST) 뒤 메일 단계 note의 Message-ID와 사용자 수신 여부를 대조해 `progress.md`에 기록

## 13. 확인 필요 (구현 전 사용자 결정)

| 항목 | 기본 제안 |
|---|---|
| `AI 분석 설정` 메뉴를 새 `설정` 탭으로 합칠지 | 합친다. `/settings/ai`는 새 화면으로 이동 |
| 회사 메일이 Gmail 발신을 막는 경우 사내 SMTP를 쓸지 | 시험 발송 결과를 본 뒤 결정 |
| 관리자 비밀번호 초기값 `1` 유지 기간 | 배포 당일 관리자가 바꾼다. 바꾸기 전까지 배너 표시 |
