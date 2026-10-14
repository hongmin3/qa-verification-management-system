# QA 점검 사용량 절감 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 한 달을 선택해도 자동으로 대량 AI 분석이 시작되지 않고, 사용자가 고른 대상만 정해진 한도 안에서 분석한다.

**Architecture:** 기존 수집·변경 감지·분류·검증을 재사용한다. AI 없는 대상 준비와 AI 분석을 분리하고, Sonnet 기본 분석에 공통 실행 예산을 적용한다. 단일 구조화 응답과 안전한 결과 재사용은 측정·품질 검증 후 단계적으로 도입한다.

**Tech Stack:** 기존 FastAPI·Jinja2·SQLite·Python·Claude CLI. 새 외부 서비스·벡터 DB·유료 API를 추가하지 않는다.

**Spec:** `SPEC.md`의 REQ-DAILY-016·REQ-AICALL-006·007 및 `specs/qa-intelligence.md`의 REQ-QAINTEL-025·027·030·036·037·038·040·041을 변경 기준으로 사용한다. 이 문서는 제안 계획이며 현행 동작 사양이 아니다. 구현 시작 시 해당 요구사항을 수정하고 새 요구사항 ID를 당시 사용 여부 확인 후 부여한다.

**상태:** 계획만 작성. 모델·예약·메일·운영 DB 변경 및 새 Claude 호출 없음. 일정은 구현 순서이며 절감률을 보장하는 약속이 아니다.

## 1. 확인한 사실과 목표

- 2026-10-14 같은 수정 완료 이슈 2건: 이전 서버 626,176토큰·CLI 환산 $1.4274·13턴·도구 10회. 개선 후 노트북 581,233토큰·$1.5079·5턴·도구 4회. 형식 통과는 의미상 완전성 검증과 다르다.
- 개선 후 입력·캐시 566,976토큰, 출력 14,257토큰(그중 추론 3,848). 노트북 사용률 13%→18%는 실행 중 관측이며 독립적인 실행 전후 계측이 아니다.
- 입력을 작게 나누면 매 호출의 규칙·지침을 반복 전송한다. 기존 7건 재구성에서 최대 프롬프트는 줄었으나 초기 전달 합계는 195,121→281,095자로 증가했다. 60,000자 분할의 총비용 효과는 미확정이다.
- 9월 14일~10월 14일 저장 자료: SRS 30건 변경. 이슈 시작 기록은 10월 1일이라 이전 이슈 변화는 복원할 수 없다. 중복 제외 전 수정 완료 43건 등이 후보이며, 정합성 점검은 648건 중 341건·SRS별 최소 65묶음이다. 이 수치는 새 호출 수가 아니다.
- Sonnet 5.5 단가는 같은 토큰 구성에서 Opus 5.5의 절반이다. 구독 한도 50% 절감이라는 뜻은 아니다. 기존 월간 한도 120~200% 추정은 운영 기준으로 사용하지 않는다.

### 성공 기준

| 목표 | 완료 판정 |
|---|---|
| 기간만 바꿔도 돈이 나가는 상황 제거 | 대상 확인·기간 변경·사용량 조회의 Claude 프로세스 실행 0회 |
| 호출량 통제 | 초기 기본값: 실행당 새 호출 최대 2회, 이 QA 점검 기능 전체 제품 합산 KST 하루 최대 4회 |
| 자동 소비 방지 | 절약 모드의 예약은 수집·대상 준비만 수행. AI·한도 초기화 후 AI 자동 재개 0회 |
| 일반 모델 변경 | 이 기능의 일반 분석은 Sonnet 5.5·medium, Opus는 사용자가 선택한 대상의 상세 재검토에만 사용 |
| 단위 비용 | 동일 자료·동일 필수 검토 항목의 종단 비용 중앙값 50% 이상 감소를 목표로 측정. 미달이면 미달로 보고 |
| 품질 | 평가 자료에서 필수 근거 누락·중요 충돌 누락·근거 없는 확정 0건. 미분석은 명확히 표시 |
| 대량 실행 방지 | 한 달 선택 시 전체 자동 선택 없음. 예상 범위·선택 수·남은 대상을 실행 전에 표시 |

절약의 가장 큰 원천은 필요 없는 AI 실행을 하지 않는 것이다. 기존 분석을 덜 수행해 줄인 비용과, 같은 분석을 효율적으로 수행해 줄인 비용을 분리해 보고한다.

## Global Constraints

- 핵심 앱 `app/`만 대상. `services/*`, VXvue 챗봇, 매뉴얼 분석, TC 초안의 모델·정책을 함께 바꾸지 않는다.
- 기존 `max_tasks_per_run=30`, `batch_size=5`, `audit_batch_size=10`, `backlog_max_events=10`, `max_input_chars=60000`은 현재 값이다. 아래 2회·4회와 새 분할 방식은 사양 변경안이며 기존 값으로 오인하지 않는다.
- 규칙·근거·후보 수를 임의로 잘라 성공 처리하지 않는다. 예산에 맞지 않으면 해당 대상을 미분석으로 남긴다.
- AI로 이슈 Close, TC 원본·결과·이력 수정/삭제 금지. TC 검토·초안은 기존 별도 요청 경계를 유지한다.
- 제품별 설정·마스킹·취소·실행 잠금·근거 검증·불완전 결과 표시를 유지한다.
- 구독 5시간 %를 토큰이나 달러로 고정 환산하지 않는다. 서버와 PC 계정·캐시·CLI 버전을 혼합 비교하지 않는다.
- 의미 없는 변경의 제외는 현행 확정적 규칙까지만 허용한다. AI를 호출해 분석할지 분류하는 단계를 추가하지 않는다.
- 기존 운영 설정을 기본값 변경으로 덮어쓰지 않는다. 절약 모드 활성화는 배포 시 별도의 명시적 마이그레이션으로 기록한다.

## 2. 권장 사용자 흐름

1. 기간 선택 → **[분석 대상 확인]**. AI 없이 수집·스냅샷 비교·후보 분류를 수행한다.
2. 실제 비교 기간, 새 변경·기존 대기·재사용 가능·미분석 수를 구분한다. 과거 이슈 기록 부족을 날짜로 표시한다.
3. 목적 선택: 기본 **변경 내용과 검증 영향 분석**, 추가 **이슈–사양 일치 여부 점검**. 목적에 맞는 대상 목록을 표시한다.
4. 사용자가 대상 선택. 초기에는 아무 항목도 선택하지 않는다. 심각도·변경 종류·미분석 기간은 정렬 근거이며 중요도를 AI 확정하지 않는다.
5. Sonnet·medium, 선택 건수, 예상 호출 수, 비용 범위/추정 불가, 이번 실행 최대 2회, 오늘 남은 횟수를 표시한다.
6. **[선택 항목 분석]** 한 번으로 실행한다. 같은 내용을 다시 승인받는 별도 확인창은 만들지 않는다.
7. 완료·근거 부족·형식 실패·예산으로 미실행·자료 변경으로 재확인 필요를 구분한다. 나머지 항목은 완료로 바꾸지 않는다.
8. 필요할 때 대상 하나의 **[Opus로 상세 재검토]**를 선택한다. 같은 예산을 사용하며 결과는 원본을 덮지 않고 연결한다.

두 분석 목적은 유지한다. “현재 이슈 전체 점검” 이름은 실제로 선별된 이슈만 분석하므로 바꾼다. 정합성 점검을 일반 분석의 상위 단계로 설명하지 않는다.

### 예약과 메일

- 신규 절약 모드는 자동 AI를 끄고 기존 시각에 수집·대상 준비만 한다. 기존 일일/주간/중지의 날짜·공휴일 의미는 유지한다.
- 메일 사용 중이면 기존 수신자에게 기존 주기로 코드가 만든 변경 건수·미분석 목록 링크만 보낸다. 제목에 “변경 목록, AI 미분석”을 표시한다. 수신자·주기를 새로 만들지 않는다.
- 예산으로 멈춘 분석은 시간이 지나도 자동 재개하지 않는다. 다음 예약은 목록만 갱신한다.
- 과거에 예약된 catch-up·앱 재시작의 예약 만회도 실행 직전에 새 정책과 예산을 재확인한다.

## 3. 비용 통제 설계

### 모델과 예산

- `daily_qa` 전용 일반 모델 설정을 추가한다. `ai.claude.models.standard`를 바꿔 챗봇 등으로 영향이 번지지 않게 한다.
- 기존 `comment_model`, `audit_model`을 유지하고 신규 이슈·수정 완료·Spec 판정·사양 변경에 Sonnet을 적용한다. 추론은 medium 유지.
- 호출은 묶음 기준이다. 캐시 재사용은 0회, 실패·취소·형식 재시도·인증 갱신용 호출도 실제 CLI가 시작했으면 1회로 센다.
- SQLite 공통 원장에 호출 시작 전 원자적으로 자리를 예약한다. 제품 간 동시 실행·수동·예약·재개·Opus가 같은 하루 한도를 사용한다.
- 프로세스 장애로 완료 기록이 없으면 사용량 불명으로 남기고 일일 예산을 반환하지 않는다. CLI 미시작이 입증된 경우만 반환한다.
- 자동 의미 재분석/모델 상향은 0회. 결과 형식은 결정적으로 복구 가능한 필드만 코드로 보완하고 원본을 보존한다.
- 달러·토큰 추정값은 다음 호출 시작 여부를 돕는 보조값이다. 이미 시작한 한 호출의 소비와 계정 전체 5시간 비율에 대한 강제 상한을 약속하지 않는다.
- 계정 사용률이 오래되거나 다른 앱과 공유되면 참고값임을 표시한다. 조회 실패를 0%로 표시하지 않는다.
- 사용량 조회의 인증 refresh용 Claude 호출은 목록 조회에서 금지한다. 만료 시 조회 불가 표시만 한다.

### 입력과 호출 방식

1. 첫 단계는 기존 runner를 그대로 사용해 모델 효과만 비교한다. 모델·추론·프롬프트를 동시에 바꾸지 않는다.
2. 다음 단계에서 도구 없는 단일 구조화 응답 경로를 추가한다. 기존 `app/core/claude_cli.py`의 격리·환경·구조화 출력 기능을 재사용하되 일일 점검의 취소·로그·예산을 유지한다.
3. 코드가 필요한 사양 원문·이슈·댓글·규칙 절을 한 번 조립한다. AI가 파일을 읽고 결과를 Write하는 대신 응답 JSON을 앱이 검증·저장한다.
4. 입력 후보만으로 판단할 수 없으면 근거 부족으로 남긴다. 현재 runner에서 유효했던 추가 검색을 막아 놓고 동일 품질이라고 주장하지 않는다. 추가 자료를 코드가 준비한 후 사용자가 재검토를 선택할 수 있게 한다.
5. 모든 필수 판단 구획·Regression 축·근거 위치는 유지한다. 설명 중복과 근거 원문 재인용만 줄인다.
6. 60,000자 고정 분할과 기존 묶음 유지안을 비교한다. 동일 SRS/규칙 공유 이익과 총 초기 입력량을 계산하고, 완성된 분석당 비용이 낮은 안을 채택한다. 큰 단일 항목을 강제로 자르지 않는다.
7. 초기 호출량 예산과 별도로 모델별 최대 입력·출력 길이, timeout·CLI 턴 한도를 설정한다. 실제 지원 플래그를 확인한다. 한도 종료 시 부분 결과를 완성 결과로 채택하지 않는다.

### 결과 재사용

- 초기 출시에서는 현재의 엄격한 전체 묶음 캐시를 유지한다. 실패·근거 부족·규칙 변경·모델 변경 결과를 재사용하지 않는다.
- 재사용 실패 이유(입력/모델/규칙/전체 색인/검증/묶음 구성)를 계수한다. 효과가 작으면 먼저 원인을 확인한다.
- 후속 단계에서 대상별 캐시를 도입한다. **도구 없는, 선언된 입력만 사용한 분석**에 한정한다.
- 키는 제품·대상 원문·관련 댓글·선택된 모든 근거·선택기 버전·모델·추론·규칙·출력/검증 버전·사용자 답변을 포함한다. 단순 이슈 ID 캐시는 금지한다.
- 자료 갱신 후 후보 선택을 다시 수행한다. 새 관련 자료가 후보를 바꾸면 무효화한다. 전체 검색 가능 경로는 전체 색인 지문을 계속 사용한다.
- 재사용 때마다 최신 근거 검증을 한다. 저장소 전체 지문 제거부터 먼저 하는 변경은 하지 않는다.

## Review Focus

1. 과거 이슈 스냅샷 부족: 선택 기간 전체를 분석했다고 보이지 않아야 한다(Task 2).
2. 선택 이후 원문·모델·규칙 변경: 오래된 미리보기로 새 대상을 추가 실행하지 않아야 한다(Task 2).
3. 제품 간 동시 호출·재시작·취소: 예산 중복 소비·반환 오류가 없어야 한다(Task 3).
4. 유효한 JSON에 중요한 충돌이 누락: 형식 검사만 통과한 결과를 품질 통과로 삼지 않아야 한다(Task 5·7).
5. 신규 관련 SRS가 등록됨: 오래된 판정이 캐시로 계속 남지 않아야 한다(Task 6).

## 4. 구현 작업과 검증

공통 작업 순서: 관련 SPEC 수정 → 아래 실패 시험 작성·실패 확인 → 최소 구현 → 해당 시험 통과 → diff 검토 → 작업별 커밋. 계획 작성 단계에서는 이를 실행하지 않는다.

파일명만 적힌 구현 경로는 `app/modules/daily_qa/` 기준이다. 예외로 `dashboard.py`·`router.py`·`templates/*`는 `app/modules/qa_agent/` 기준이다. 모델 설정 저장/표시는 `app/core/setting_overrides.py`, `app/web/settings_router.py`, `app/web/templates/settings_ai.html`을 확장한다. 이 경로 규칙은 아래 모든 작업에 적용한다.

### Task 1: 비용 기준과 평가 자료 고정

**Files:** Create `scripts/measure_qa_usage.py`, `tests/test_qa_usage_measurement.py`; reuse `app/core/claude_usage.py`, `app/modules/daily_qa/agent_runner.py`.

**Interfaces:** `summarize_usage(events: list[dict]) -> dict`는 입력·캐시 생성·재읽기·출력·추론(출력의 부분집합)·실제 모델·턴·도구·실패·quota 관측 시각을 반환한다. `--execute` 없이는 AI 호출 불가.

- [ ] `test_measurement_does_not_double_count_thinking`: 총 토큰 `581233`, 추론 `3848` 별도, 출력에 추론 재합산 없음.
- [ ] `test_prepare_measurement_never_starts_cli`: 준비·집계에서 `calls == 0`.
- [ ] 두 시험 실패 확인 후 구현하고 `pytest tests/test_qa_usage_measurement.py -q` 통과.
- [ ] 기존 표본 2건 및 신규 6건 이상을 개인정보 제거한 평가 자료로 준비. 실제 원문은 Git 제외, manifest·예상 필수 근거만 관리.
- [ ] 도구·기본 시스템 지침 크기와 CLI 버전을 기록. 첫 요청이 약 13만 입력 토큰인 이유를 분해하고 로그 중복 집계도 검사.
- [ ] 실제 호출은 Task 3 예산 도입 후 Task 7에서 수행. 이 단계 커밋.

### Task 2: AI 없는 실행 계획과 대상 선택 화면

**Files:** Create `app/modules/daily_qa/execution_plan.py`, `tests/test_qa_execution_plan.py`; modify `pipeline.py`, `dashboard.py`, `router.py`, `templates/dashboard.html`, `templates/guide.html`, `app/web/static/qa-run-preview.js`, `app/core/daily_qa_storage.py`.

**Interfaces:** `prepare_plan(product: str, since: str, until: str, purpose: str) -> dict`; `validate_selection(plan_id: str, target_ids: list[str]) -> dict`. `purpose`는 `changes`/`consistency`. 응답은 ID·지문·실제 비교기간·모델·대상·캐시상태·예상묶음·경고를 포함한다.

- [ ] `test_prepare_plan_has_zero_ai_calls`: `calls == 0`, 기존 이벤트 완료 상태 불변.
- [ ] `test_selection_is_explicit_and_scoped`: 빈 선택 거절, 다른 제품·계획 밖 ID 거절, `selected_ids` 밖 AI 실행 0.
- [ ] `test_stale_plan_requires_refresh`: 입력/규칙 변경 또는 생성 30분 경과 시 409, 호출 0. 계획은 서버 저장·제품 범위 검증.
- [ ] `test_missing_baseline_shows_actual_coverage`: 9/14 선택·10/1 이슈 기록 시작이면 10/1을 표시하며 이전 기간을 미확인으로 둠.
- [ ] 실패 확인 후 구현, `pytest tests/test_qa_execution_plan.py tests/test_qa_run_preview.py -q` 통과.
- [ ] 저장 스냅샷 조회(GET)는 읽기만 한다. 최신 수집 준비(POST)는 취소 가능한 별도 작업이며 AI 없음. 이벤트 저장과 비교 기준 전진의 원자성을 지켜 수집 후 분석 대상을 잃지 않게 한다.
- [ ] 390px·1920px, 중복 클릭·새로고침·기간 변경 시험 후 커밋.

### Task 3: 실행 예산과 예약 절약 모드

**Files:** Create `app/modules/daily_qa/execution_budget.py`, `tests/test_qa_execution_budget.py`; modify `settings.py`, `pipeline.py`, `scheduled_jobs.py`, `automation_policy.py`, `app/core/daily_qa_storage.py`, `app/core/claude_usage.py` 및 기존 정책 UI/메일 템플릿.

**Interfaces:** `reserve_call(storage, run_id: str, product: str, now: datetime, per_run: int, per_day: int) -> str | None`; `finish_call(storage, reservation_id: str, outcome: dict) -> None`. 원장은 DB 트랜잭션·고유 예약 ID를 쓴다. 상태: `reserved`/`finished`/`usage_unknown`/`not_started`.

- [ ] `test_atomic_global_daily_cap`: 두 제품 동시 요청에서 하루 예약 성공 `<= 4`, 실행당 `<= 2`.
- [ ] `test_cancelled_or_unknown_call_still_counts`: CLI 시작 후 중단·프로세스 손실은 반환하지 않음.
- [ ] `test_collect_only_blocks_catchup_and_retry`: 예약·만회·한도 해제·형식 실패의 자동 추가 호출 `== 0`.
- [ ] `test_usage_preview_never_refreshes_via_cli`: 인증 만료여도 호출 `== 0`, 상태 unavailable.
- [ ] 실패 확인 후 구현, `pytest tests/test_qa_execution_budget.py tests/test_qa_automation_policy.py tests/test_qa_intel_schedule.py tests/test_claude_usage.py -q` 통과.
- [ ] 기존 설치 마이그레이션·중단 재개·KST 자정·시계 변경 시험. 정책 비활성 시 기존 동작 보존 후 커밋.

### Task 4: 일일 점검 전용 Sonnet 모델 배정

**Files:** Modify `settings.py`, `pipeline.py`, `config.yaml`, 기존 AI 설정 저장/표시 경로; Create `tests/test_qa_model_policy.py`.

**Interfaces:** `analysis_model`(일일 점검 일반 분석), 기존 `comment_model`/`audit_model`, `review_model`(명시적 상세 재검토). 제안 기본값은 Sonnet/Sonnet/Sonnet/Opus, effort medium. 기존 전역 `standard`는 보존.

- [ ] `test_all_daily_kinds_use_sonnet_without_affecting_other_modules`: 일반 분석은 Sonnet, 챗봇·매뉴얼 설정 불변.
- [ ] `test_opus_requires_explicit_review_selection`: 실패·근거 부족으로 자동 Opus 호출 0.
- [ ] `test_model_change_invalidates_cache`: 기존 Opus 결과를 Sonnet 신규 결과로 표시하지 않음.
- [ ] 실패 확인 후 구현, `pytest tests/test_qa_model_policy.py tests/test_daily_qa_result_cache.py -q` 통과·커밋.

### Task 5: 단일 구조화 응답과 입력 묶음 최적화

**Files:** Create `app/modules/daily_qa/structured_runner.py`, `tests/test_qa_structured_runner.py`; modify `agent_runner.py`, `input_budget.py`, `workspace.py`, 관련 Skill 프롬프트, `pipeline.py`.

**Interfaces:** `StructuredRunner.run(task: Task, run: RunWorkspace) -> RunnerOutcome`는 기존 runner 결과·취소 계약을 유지한다. `RunnerOutcome.meta`에 비용/실제 모델/도구 여부/출력 불완전 사유를 기록한다.

- [ ] `test_structured_runner_writes_only_validated_result`: 도구 허용 목록 비어 있음, 응답만 앱이 저장, 원본 응답 보존.
- [ ] `test_truncated_output_is_not_completed`: JSON 잘림·턴/출력 상한·근거 부족을 완료로 채택하지 않음, 자동 재호출 0.
- [ ] `test_required_axes_and_evidence_survive_packing`: 모든 필수 축·원문 위치 유지, 중복 공유 후 복원 일치.
- [ ] 실패 확인 후 구현, `pytest tests/test_qa_structured_runner.py tests/test_qa_input_budget.py tests/test_qa_intel_validation.py -q` 통과.
- [ ] 60k 분할 유지/해제 후보의 입력 합계·한 호출 크기를 오프라인 비교. API 출력 한도·CLI flag 지원 확인 후 구성값 확정, SPEC에 숫자 기록.
- [ ] Task 7에서 근거 누락 없는 비용 개선을 입증하기 전에는 기본 runner로 전환하지 않음. 기능 스위치와 함께 커밋.

### Task 6: 대상별 검증 결과 재사용(후순위)

**Files:** Modify `result_cache.py`, `pipeline.py`; Create `tests/test_qa_target_cache.py`.

**Interfaces:** `target_fingerprint(target: dict, evidence: list[dict], policy: dict) -> str`; `policy`에 선언된 입력 범위/선택기/모델/규칙/검증 버전을 포함. 구조화 runner 전용 cache namespace를 추가한다.

- [ ] `test_rebatching_reuses_same_validated_target`: 묶음 순서 변경만으로 추가 AI 호출 없음.
- [ ] `test_new_relevant_evidence_invalidates_target`: 후보 재선정 시 새 관련 원문이 생기면 miss.
- [ ] `test_unrelated_change_reuses_only_closed_input_runner`: 전체 검색 가능한 기존 runner는 전체 지문 유지.
- [ ] `test_partial_or_uncertain_results_never_reused`: 누락·질문·검증 실패는 miss.
- [ ] 실패 확인 후 구현, `pytest tests/test_qa_target_cache.py tests/test_daily_qa_result_cache.py -q` 통과·커밋. 키 버전 분리로 기존 캐시 보존.

### Task 7: 비용과 QA 품질 비교, 출시 판정

**Files:** Task 1 측정 도구 확장; Create `docs/verification/2026-10-14-qa-usage-evaluation.md`(실측 시점 날짜로 변경), `tests/test_qa_usage_evaluation.py`.

- [ ] 코드로 된 수집·분류·단위 시험을 먼저 마친다. 실제 AI 표본 8건 이상: 명확한 수정 2·상충 사양 2·버전/장비 예외 1·사양 변경 1·댓글 1·근거 부족 1. 별도로 중복·형식 실패·재개·큰 입력을 합성 시험한다.
- [ ] QA가 원문을 보고 필수 사실·인용 위치·필수 영향 축·불확실성 기준을 고정한다. Opus 출력을 정답으로 삼지 않는다.
- [ ] 1차는 같은 CLI·계정·입력·medium에서 Opus 대 Sonnet만 비교. 2차는 Sonnet에서 기존 runner 대 단일 응답만 비교. 3차는 묶음 정책 비교.
- [ ] 첫 유료 실험은 호출 최대 2회. 이후 평가도 하루 4회 한도 안에서 분할한다. 큰 대조군 추가 실행이 필요하면 사용량 계획을 제시하고 선택받는다.
- [ ] 중요 누락·근거 없는 확정 1건이라도 있으면 해당 경로 출시 보류. 불확실성을 정직하게 남긴 결과와 오답을 구분한다. 재검토 부담도 함께 기록한다.
- [ ] `test_quality_gate_rejects_critical_omission`: JSON 정상이어도 필수 충돌 누락 시 release_allowed=False.
- [ ] cold/warm 캐시·재시도 포함 전체 비용·완료 대상 수·근거 충족률·미분석 수·지연을 기록. 실패 후 복구 비용도 포함한다. 월간 외삽은 유형별 표본 수와 오차를 표시한다.
- [ ] 구독 %는 가능하면 호출 직전/직후 읽기 전용 계측과 다른 사용 부재를 확인한 값만 사용. 관측 불가면 퍼센트 추정 불가로 보고한다.

### Task 8: 단계별 운영 반영과 되돌리기

- [ ] Task 2·3·4를 1차 출시: 대상 준비/선택·예산·Sonnet만. 이후 평가를 통과한 Task 5, 필요할 때 Task 6을 따로 출시한다.
- [ ] 기존 실행 중 여부·원격 변경·DB 마이그레이션을 확인하고 기존 정책·앱·DB를 백업한다. 서버 문서가 Git과 달랐던 이력이 있으므로 무조건 덮지 않는다.
- [ ] 전체 pytest, `node .project-check/project-readiness.js .`, 실제 로컬 HTTP/브라우저 실행을 확인한다. 자체 테스트는 현재 사용할 수 있는 venv의 Python으로 실행한다.
- [ ] SPEC/CHANGELOG 변경 후 `node .project-check/render-spec-html.js .`; 문서 지도·progress 갱신, 공통 cleanup 실행 후 관련 파일만 커밋·일반 push. 다른 작업 변경을 포함하지 않는다.
- [ ] 운영 기본은 수집만. AI 0회로 대상 확인/권한/실행 예산/남은 일 보존을 smoke 검사한다. 실제 한 묶음 검증도 정해진 호출 예산에 포함한다.
- [ ] 품질 저하·중복 실행·예산 우회 시 새 AI 실행부터 중지한다. runner/cache 스위치를 이전 값으로 되돌리되 자동 대량 실행은 되살리지 않는다. 새 결과·미완료 이벤트·사용량 원장은 보존한다.

## 5. 사양 변경과 기간 표시

현재 자동 분석/자동 catch-up/전체 묶음 캐시/60k 분할은 기존 사양에 근거한다. 이를 변경하는 작업은 버그 수정으로 위장하지 않고 SPEC 변경으로 처리한다.

REQ-QAINTEL-027 순서 1은 시작일 이전 기록이 없으면 가장 오래된 스냅샷을 쓰도록 정하며 현재 구현도 같다. 사양/코드 불일치는 확인되지 않았다. 다만 선택한 기간과 실제 비교 기간이 달라질 수 있으므로 Task 2에서 실제 비교 시작일과 미확인 구간을 눈에 띄게 표시한다. 기간 내 이슈 기록이 전혀 없는 경우와 일부만 있는 경우를 구분해 시험한다.

추론 강도 low, Haiku 분류, 외부 검색 서비스, 임베딩, 자동 Opus 승격은 이번 범위에서 제외한다. 먼저 단순한 통제 구조의 실제 효과를 확인한다.

## 6. 조사·재사용·출처

- 읽은 구현: `settings.py`, `pipeline.py`, `agent_runner.py`, `input_budget.py`, `workspace.py`, `result_cache.py`, `issue_audit.py`, `snapshots.py`, `change_events.py`, `app/core/claude_cli.py`, `app/core/claude_usage.py`, QA dashboard/router/guide, 관련 Skill.
- 읽은 시험: 입력 분할/근거 공유 시험, 전체 묶음 캐시 무효화 시험, 자동 정책 시험. 기존 `test_qa_run_preview.py`, `test_qa_run_progress_browser.py`를 화면 회귀에 재사용한다.
- 기존 `docs/COST_OPTIMIZATION.md`의 전체 재시도 비용·중복 제외 경계도 반영했다. 그 문서의 10/13 Opus 유지 결정은 당시 결정이며 이번 Sonnet 전환 제안과 구분한다.
- workspace `tools/` 파일명에서 budget/usage/token/cost 관련 공통 구현을 검색했으나 재사용할 예산 원장은 발견하지 못했다. 기존 앱의 SQLite/usage/정책을 확장한다.
- [공식 가격](https://platform.claude.com/docs/en/about-claude/pricing), [사용량과 길이 한도](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work), [effort](https://platform.claude.com/docs/en/build-with-claude/effort). 가격·모델 가용성은 유료 검증 직전에 다시 확인한다.
- 현재 테스트 로그는 로컬 임시 측정 폴더에 있다. 구현 시작 시 필요한 입력/출력·manifest를 Git 제외 검증 폴더에 보존하고 내용 해시를 기록한다. 임시 파일 경로만 장기 근거로 삼지 않는다.

## 7. 계획 자체 검토

- [x] 비용 절감과 분석 생략을 별도 지표로 정의했다.
- [x] 기능 전체의 모델 변경이 다른 화면으로 번지지 않게 범위를 정했다.
- [x] 예약·재시도·인증 갱신·동시 실행의 숨은 호출을 예산에 포함했다.
- [x] 원문 누락·캐시 오판·부분 기간·출력 잘림을 완료 기준에 넣었다.
- [x] 기존 사양 변경점과 검증/배포/되돌리기 경로를 기록했다.
- [x] 실제 토큰·5시간 % 절감률은 미측정 값으로 남겼다.
