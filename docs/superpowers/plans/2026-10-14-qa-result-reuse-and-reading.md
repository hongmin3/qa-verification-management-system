# 이슈별 결과 재사용과 분석 화면 정리 실행 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기간을 넓혀 조회해도 변하지 않은 이슈·사양의 유효한 결과는 DB에서 보여주고, 새 분석이 필요한 대상만 선택하여 실행한다. 결과 화면에서는 결론·할 일·이슈 작성자를 쉽게 찾는다.

**Architecture:** 핵심 앱의 기존 수집·이벤트·Finding·근거 검사·SQLite를 확장한다. 화면 조회와 기존 결과 연결에는 AI를 쓰지 않는다. 분석 결과 본문은 보존하고 표시 순서와 조회 조건만 정리한다.

**Tech Stack:** FastAPI, Jinja2, SQLite, 기존 JavaScript, pytest, 브라우저 시험. 새 외부 서비스나 의존성 없음.

**Spec:** `specs/qa-intelligence.md`의 REQ-QAINTEL-019·020·024·027·033·037·040 및 구현 중인 042~044. 대상별 재사용과 작성자 조회는 사양 변경이므로 구현 전에 관련 요구사항·시험·추적성을 함께 갱신한다.

**상태:** 2026-10-14 사용자 후속 요청을 반영해 1차 구현과 통합·브라우저 시험을 수행했다. [사용량 절감 계획](2026-10-14-qa-usage-reduction.md)에 추가되는 범위이며 운영 반영 여부는 `progress.md`에서 확인한다.

**추가 확정 범위:** REQ-QAINTEL-045~048에 따라 선택한 Checklist 시트(초기 V1.1.0)를 사양·이슈 분석에서 함께 확인한다. 예약 실행은 두 분석을 공통 호출 예산 안에서 수행하고 한 통의 메일로 건수·상태·결과 링크를 보낸다. AI 판정과 별도로 사람이 미처리·진행 중·확인 완료·보류를 기록한다.

## 1. 작업 시작 시 확인한 동작

| 확인한 코드·시험 | 현재 동작 | 이번에 필요한 변화 |
|---|---|---|
| `app/modules/daily_qa/result_cache.py`, `tests/test_daily_qa_result_cache.py` | 묶음 입력·자료 전체·규칙·모델이 같은 성공 결과를 재사용 | 선택 기간과 묶음 구성이 달라도 대상별 유효 결과를 찾기 |
| `app/modules/daily_qa/pipeline.py` | 완료 이벤트의 중복 실행을 막고 같은 묶음 캐시를 검사 | 이미 끝난 항목도 준비 목록에서 기존 결과를 연결해 보여주기 |
| `app/modules/qa_agent/dashboard.py` | 최신 30개와 기간별 생성 결과를 조회 | 전체 결과에서 작성자·번호 조건을 적용한 뒤 페이지 나누기 |
| `app/modules/qa_agent/templates/finding_detail.html` | 요약, 설명, 상세 구획에서 판단·댓글·메모가 반복됨 | 같은 내용을 한 번 배치하고 필요한 근거를 쉽게 펼치기 |
| `app/modules/daily_qa/product_adapter.py` | 이슈 작성자 필드 없음. 댓글에는 작성자 ID가 있음 | 이슈 최초 작성자 ID와 표시 이름을 별도로 보존 |
| `app/modules/daily_qa/polarion.py` | Work Item·댓글 읽기 API 있음. 사용자 이름 조회 함수 없음 | 실제 응답의 작성자 관계와 이름 조회 권한을 확인한 뒤 읽기 경로 확장 |

현재 묶음 캐시는 REQ-QAINTEL-037에 맞는다. 대상별 재사용이 없는 것을 기존 사양의 버그라고 처리하지 않는다. 이미 완료된 이벤트를 건너뛰는 일과, 이번 조회에 유효한 기존 결과를 보여주는 일은 별도다.

## 2. 바꾸지 않을 기준

- 사용자 지침 프롬프트와 필요한 QA Markdown 원문, 필수 검토 항목·근거·회귀 영향 축을 유지한다. 화면을 접는 것과 분석 내용을 줄이는 것을 구분한다.
- 준비·조회·필터·기존 결과 표시는 Claude 호출 0회다. 재사용 불가라고 곧바로 AI를 부르지 않는다. 새 분석은 대상 선택과 실행 예산을 거친다.
- 제품·분석 종류·자료 기준 시점을 구분한다. 같은 번호라는 이유만으로 다른 제품 결과나 다른 검토 종류를 섞지 않는다.
- 같은 이슈 본문이라도 관련 사양·댓글·규칙·필수 자료가 바뀌었으면 재검토 후보가 될 수 있다.
- 과거 결과의 원문·최초 분석 시각·모델·사용자 검토 상태를 보존한다. 조회 시각을 분석 시각으로 바꾸지 않는다.
- 작성자 이름은 서버의 표시 정보다. 이름을 추측하거나 댓글 작성자·담당자·문서 업로더를 이슈 작성자로 대신하지 않는다. 이름 표시·필터 때문에 AI에 개인정보를 추가하지 않는다.
- 실제 이름과 관계 필드의 형태는 구현 전 읽기 전용 응답으로 확인한다. 확인 전에는 특정 API 경로나 표시 이름 필드가 존재한다고 가정하지 않는다.

## 3. 사용자가 기대하는 결과 재사용

예: 어제 `VP-1234`를 분석했다. 오늘 한 달 기간을 선택한다. 동일한 검토에 필요한 이슈·댓글·사양·지침이 그대로라면 `기존 결과 사용`으로 표시하고 어제 결과를 연결한다. 이 항목의 새 AI 호출은 0회다.

### 준비 목록

| 대상 | 상태 | 설명 | 동작 |
|---|---|---|---|
| VP-1234 | 기존 결과 사용 | 최초 분석 시각, 원본 결과 링크 | 결과 보기 |
| VP-2345 | 재검토 필요 | 관련 SRS 변경 등 구체적 이유 | 새 분석 대상으로 선택 |
| VP-3456 | 미분석 | 성공한 분석 기록 없음 | 새 분석 대상으로 선택 |
| VP-4567 | 이전 결과 있음 · 최신 여부 미확인 | 비교 정보 부족·자료 읽기 실패 | 이전 결과 보기, 재검토 선택 |

상단에는 `기존 결과 18건 · 새 분석 대상 2건 · 확인 필요 1건`처럼 표시한다. 건수는 예시이며 실행 시 DB에서 계산한다. 기존 결과도 준비 화면에서 바로 보이므로 완료 항목이 목록에서 사라지지 않는다. 아무 항목도 기본 선택하지 않는다.

### 재사용 판정

1. 제품·대상 종류·대상 번호·분석 종류·자료 기준 시점으로 기존 성공 결과를 찾는다.
2. 원문·관련 자료·유효 상태·댓글·검토에 필요한 변경 전후 값·지침·규칙·Skill·검증 형식의 비교 기록을 확인한다. 이름 표시, 수집 시각, 실행 번호, 화면 필터는 분석 내용 비교에서 제외한다.
3. 기간이 넓어졌다는 이유만으로 무효화하지 않는다. 다만 새로운 기간이 다른 사양 개정 비교나 새로운 댓글 묶음을 요구하면 별도 검토로 본다. 과거 기간에는 당시 자료와 맞는 결과만 연결하며 미래 자료로 덮지 않는다.
4. 완료 상태와 필수 검토 항목·근거를 코드로 재검사한다. 자료를 읽지 못했거나 잘린 입력·실패·부분 결과·미해결 질문이면 검증된 완료 결과로 재사용하지 않는다.
5. 같으면 원본 Finding에 연결한다. 새 AI 분석 결과처럼 복제해 최초 분석 시각이나 승인 상태를 바꾸지 않는다. 이번 실행에는 별도의 결과 연결 기록을 남긴다.
6. 다르거나 판단할 수 없으면 이유를 보여주고 대기로 남긴다. 기존 결과는 이력으로 열 수 있지만 최신 검토 완료로 표시하지 않는다.

기존 DB에 비교 기록이 없는 결과도 보여준다. 비교가 불가능한 결과를 자동으로 최신·유효라고 인증하거나, 기록 보충을 위해 몰래 AI를 실행하지 않는다.

### 검색 범위가 넓은 현재 실행기의 안전한 처리

현재 실행기는 후보 외의 전체 자료를 검색할 수 있다. 대상 본문만 비교하고 전체 자료 검사를 빼면 새 관련 사양이나 중복 이슈를 놓칠 수 있다.

- 1차는 대상별 결과 연결을 만들되 **검색 가능한 자료 전체의 내용 비교를 유지**한다. 전체 자료가 그대로인 사용자의 예시는 기간·묶음 변경과 무관하게 재사용한다.
- 자료 전체가 달라졌다면 보수적으로 재검토 후보로 표시한다. 무관한 변경까지 자동으로 제외하는 최적화는 입력 범위와 근거 완전성이 검증된 뒤 추가한다.
- 묶음 분석에서 대상끼리 비교한 결과는 함께 읽은 대상·근거 의존성을 보존한다. 대상 하나의 결과를 떼는 것만으로 독립 결과가 됐다고 가정하지 않는다.
- Sonnet 기본 전환만으로 과거 Opus 결과를 자동 재분석하지 않는다. 기존 결과는 사용 모델을 표시해 조회한다. 새 실행의 동일 조건 캐시는 모델·추론을 계속 구분하고, 모델을 지정한 재검토는 별도 선택으로 처리한다.

## 4. 상세 화면 구성

상단에는 항상 다음을 보인다. 이름은 표시 예시다.

```text
[VP-1234] 이슈 제목                         원문 열기
홍길동 · 이슈 작성 · 최초 작성일
검토 필요 · 원본 분석 10/13 · 기존 결과 사용

결론: 무엇이 확인됐고 무엇을 더 확인해야 하는지
QA 할 일: 우선순위가 있는 짧은 목록
주의: 읽지 못한 자료·미확인 사항·지난 결과 여부

[판단과 할 일]     [근거 자료]     [분석 기록]
```

| 영역 | 내용 |
|---|---|
| 판단과 할 일, 기본 화면 | 이슈/사양 핵심, 이번 변경, 판단 이유, 필요한 QA 조치 |
| 근거 자료 | 관련 사양·과거 이슈·댓글 해석, 전체 인용·원문 링크. 자료 종류별로 펼침 |
| 분석 기록 | 확인/미확인 자료 전체, 모델·지침 버전·사용량, 원본 결과·재사용 기록·변경 이벤트 |

근거 부족·필수 확인사항은 탭을 열지 않아도 상단에 보인다. 회귀 위험이나 사양 충돌이 있으면 판단 화면에서 요약과 해당 근거로 이동하는 링크를 제공한다. 원인 검토·수정 검토·회귀 영향 축·TC 초안은 종류에 따라 필요한 곳에 모두 남긴다.

반복된 요약·댓글·분석 메모를 한 곳으로 모으고, 값이 없는 일반 구획은 표시하지 않는다. 값이 없는 필수 검토 항목은 숨기지 않고 `확인 기록 없음`으로 구분한다. 과거 결과도 새 값이나 판정을 만들어 넣지 않고 표시한다.

390px에서는 세로 흐름과 줄바꿈, 넓은 화면에서는 상단 요약과 충분한 본문 폭을 사용한다. 탭은 키보드 이동·현재 선택 표시·직접 링크를 지원한다. 자바스크립트 실패 시에도 본문을 읽을 수 있는 기본 HTML을 유지한다.

## 5. 실제 작성자와 검색·필터

- 이슈 최초 작성자의 `reporter_id`, `reporter_name`, `reporter_source`, `reporter_synced_at`을 서버 표시 정보로 보존한다. 최종 필드명은 실제 수집 모델과 대조한다.
- 목록에는 `홍길동`처럼 실제 이름을 표시한다. 상세에는 `홍길동 · 이슈 작성`으로 역할을 구분한다. `등록자`만 보여주는 칸으로 대체하지 않는다.
- 이름을 확인할 수 없으면 `이름 미확인 (사용자 ID)`를 표시한다. ID도 없으면 `이슈 작성자 정보 없음`으로 표시한다. 동명이인은 ID로 구분한다.
- 대시보드·기간별 결과·실행별 결과 목록에 작성자와 이슈번호 필터를 일관되게 적용한다. 작성자 선택과 이슈번호 검색은 AND 조건이며 제품 범위를 유지한다.
- 이슈번호는 대소문자를 정규화한 전체 번호 또는 부분 번호 검색을 지원한다. 목록에는 SRS도 있으므로 UI 입력 이름은 `이슈·사양 번호`로 한다. 작성자 필터 선택 시 작성자 정보가 없는 사양을 이슈와 혼동하지 않는다.
- 필터는 DB 조회 단계에서 적용한 다음 건수·페이지를 계산한다. 최근 30개만 받아서 브라우저에서 거르면 오래된 결과를 놓치므로 이 방식은 쓰지 않는다. 페이지 크기 50개, URL 조건 유지, 초기화와 결과 없음 안내를 제공한다.
- 과거 결과 작성자는 제품·이슈 ID에 연결된 원본 스냅샷 또는 다시 수집한 메타데이터로 보충할 수 있다. 새로 확인한 표시 이름을 과거 시점의 이름으로 단정하지 않는다. 분석 재실행은 하지 않는다.
- 작성자 표시 이름만 변경되면 화면만 갱신한다. 분석 캐시 무효화·변경 이벤트·Claude 호출을 만들지 않는다.

## 6. 구현 순서와 검증

### Task 1: 대상별 결과 연결과 안전한 재사용

**Files:** `app/modules/daily_qa/result_cache.py`, `pipeline.py`, 구현 중인 `execution_plan.py`, `app/core/qa_execution_storage.py`, `app/modules/qa_agent/dashboard.py`. 새 시험 `tests/test_qa_target_reuse.py`.

**Interfaces:** `resolve_existing_results(product, targets, context_manifest)`는 대상별 `state`, `reason`, `source_finding_id`, `analyzed_at`, `model`을 반환한다. 조회 함수는 AI 실행기를 받지 않는다. 기존 묶음 캐시는 유지하고 대상별 연결 기록은 별도 버전으로 저장한다.

- [ ] 사양에 기간 변경과 검토 의미 변경의 차이, 과거 결과 표시, 검색 범위 검사를 명시한다.
- [ ] `test_next_day_month_range_reuses_unchanged_issue_and_srs`: 어제 분석 후 한 달 선택, 기존 결과 ID 유지, Claude 새 호출 0회.
- [ ] `test_rebatching_does_not_hide_completed_targets`: 묶음 순서·크기만 변경해도 유효 결과 연결 유지.
- [ ] `test_new_comment_or_relevant_srs_or_rule_requires_review`: 새 댓글·사양·사용자 지침·QA MD 변경이면 이유 표시, 자동 호출 0회.
- [ ] `test_historical_cutoff_and_changed_transition_are_not_conflated`: 과거 종료일과 다른 변경 전후 비교를 잘못 재사용하지 않음.
- [ ] `test_partial_legacy_and_unreadable_results_are_not_certified`: 과거 결과는 볼 수 있지만 비교 불가·부분 결과를 최신 완료로 취급하지 않음.
- [ ] `test_reuse_keeps_source_model_time_and_review_state`: 출처·모델·최초 시각·검토 상태 보존. 모델 기본값 변경만으로 몰래 재실행하지 않음.
- [ ] 각 실패를 확인한 뒤 최소 구현하고 `pytest tests/test_qa_target_reuse.py tests/test_daily_qa_result_cache.py tests/test_qa_execution_plan.py -q` 실행.

### Task 2: 실제 이슈 작성자 수집과 조회 정보

**Files:** `product_adapter.py`, `collector.py`, `polarion.py`, 제품 프로필, `app/core/daily_qa_storage.py`, `app/modules/daily_qa/store.py`. 새 시험 `tests/test_qa_issue_reporter.py`.

**Interfaces:** 사용자 표시 이름 조회는 현재 GET 클라이언트를 확장한다. `(source, product, issue_id)` 기준의 메타데이터 조회를 일괄 수행해 목록의 행 수만큼 API를 부르지 않는다. 화면 조회 중에는 외부 API를 부르지 않는다.

- [ ] 실제 응답에서 최초 작성자 관계와 표시 이름·권한을 확인한다. 테스트 자료는 익명 값으로 작성한다.
- [ ] `test_reporter_is_original_author_not_assignee_or_commenter`, `test_same_name_keeps_distinct_ids`, `test_missing_name_uses_explicit_fallback` 실패 후 구현.
- [ ] `test_reporter_metadata_change_has_zero_ai_calls_and_events`, `test_old_finding_reporter_enrichment_preserves_analysis`로 표시 정보 변경의 영향을 검증한다.
- [ ] `pytest tests/test_qa_issue_reporter.py tests/test_daily_qa_polarion.py tests/test_qa_intel_products.py -q` 실행.

### Task 3: 목록에 이름·번호 필터 연결

**Files:** `dashboard.py`, `router.py`, `dashboard.html`, `period.html`, `run_findings.html`, `app/core/daily_qa_storage.py`. 새 시험 `tests/test_qa_finding_filters.py`.

**Interfaces:** GET 조건 `reporter_id`, `subject`, `page`를 기존 제품·기간·실행 조건과 결합한다. 조회와 COUNT에 동일 조건을 사용하고 SQL 값을 매개변수로 전달한다.

- [ ] `test_reporter_subject_filters_apply_before_pagination`에서 30개 밖의 결과를 찾아냄을 먼저 확인한다.
- [ ] `test_filter_product_isolation`, `test_filter_links_preserve_dates_and_run`, `test_unknown_reporter_and_srs_are_explicit`, `test_filter_request_calls_no_ai` 실패 후 구현.
- [ ] `pytest tests/test_qa_finding_filters.py tests/test_qa_intel_dashboard.py -q` 실행.

### Task 4: 상세 화면에서 중복 설명 정리

**Files:** `finding_detail.html`, 필요한 부분 템플릿, `_intel_style.html`, `dashboard.py`, 전용 화면 스크립트. 새 시험 `tests/test_qa_finding_reading.py` 및 브라우저 시험.

- [ ] 종류별 기존 필수 필드와 새 화면의 위치를 대조하는 표를 만든다. 지침상 필요한 필드를 없애지 않는다.
- [ ] `test_critical_uncertainty_visible_without_expanding`, `test_required_regression_axes_remain_accessible`, `test_legacy_explanation_is_not_fabricated` 실패 후 구현.
- [ ] 이름·댓글에 HTML이 들어와도 실행되지 않는 출력 이스케이프, 원문 링크, TC 버튼·중단·진행 표시를 검증한다.
- [ ] 브라우저에서 390px·1920px, 키보드 탭 이동, 필터·뒤로가기·직접 링크, 실제 HTTP 렌더링을 확인한다.

### Task 5: 통합 검증과 운영 반영

- [ ] 어제의 완료·실패·미분석 항목이 섞인 DB로 오늘 한 달 조회를 실행한다. 유효 기존 결과는 표시되고 새 AI 호출은 0회여야 한다.
- [ ] 대상 선택으로 실행할 때 기존 예산·지침 누락 차단·중단·재시작 규칙을 우회하지 않는지 확인한다.
- [ ] 변경 관련 시험 후 전체 프로젝트 시험을 실행한다. `node .project-check/render-spec-html.js .`, `node .project-check/project-readiness.js .`를 실행하고 결과를 읽는다.
- [ ] SPEC·CHANGELOG·progress·문서 지도를 갱신한다. 공통 cleanup 후 관련 파일만 커밋·일반 push한다. 다른 세션의 변경은 보존한다.
- [ ] 실행 중인 작업과 DB 변경을 확인하고 백업 후 배포한다. 운영 조회·필터 smoke는 AI 0회로 수행한다. 재사용 실패 시 재사용을 끄되 자동 대량 재분석을 켜지 않는다.

## Review Focus

1. 월간 조회가 과거 분석 시각을 오늘로 바꾸거나 부분 결과를 최신 완료로 승격하지 않는가.
2. 새 관련 사양·중복 이슈 후보와 사용자 지침 변경을 대상 본문 비교만으로 놓치지 않는가.
3. 이슈 작성자·댓글 작성자·담당자·업로더가 섞이거나 동명이인이 합쳐지지 않는가.
4. 필터가 최근 일부 결과에만 적용되거나 다른 제품의 결과를 노출하지 않는가.
5. 화면을 간소화하면서 필수 QA 검토·근거 부족·주의 사항이 보이지 않게 되지 않는가.

## 기존 사용량 계획과의 관계

대상별 결과 연결과 사용자의 다음 날 월간 조회 시나리오는 후순위에서 **1차 완료 조건**으로 올린다. 구조화 단일 응답이나 무관한 자료 변경을 제외하는 공격적인 재사용은 여전히 품질 검증 후 도입한다. 새로운 AI 평가 호출 없이도 대상 연결·UI·필터·예산 경계는 합성 결과와 실제 HTTP로 시험할 수 있다.
