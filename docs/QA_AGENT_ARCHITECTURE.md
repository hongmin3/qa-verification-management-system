# QA Agentic Workflow — 구조 분석과 목표 아키텍처

> 상위: [문서 지도](README.md) · 기능 상세: [qa-agent.md](modules/qa-agent.md)
> 기준: QA 규칙 Rev1.12 · 로드맵 Rev1.0 · 2026-09-08 구현 완료분

이 문서는 **"기존 시스템을 분석하고, 재사용할 것과 보완할 것을 나눈 뒤, 최소 변경으로
목표 구조를 얹는다"**는 요구에 대한 답이다. 아래 판단은 전부 실제 코드와 실측값에 근거한다.

---

## 1. 분석 시점의 현재 구조

작업 착수 시점(2026-09-08) 상태다.

```text
qa-verification-management-system/
├─ app/                     핵심 앱 — 단일 uvicorn 프로세스
│  ├─ core/                 config · storage(SQLite) · gemini_client · prompt_manager · scheduler
│  ├─ prompts/              AI 프롬프트 YAML 3개 (버전·생성 설정 포함)
│  ├─ parsers/              pdf · docx · excel
│  ├─ retrieval/            base.py(Protocol) + bm25_retriever.py
│  └─ modules/
│     ├─ impact_analyzer/   변경 문서 → Regression TC 추천
│     ├─ manual_review/     매뉴얼 개정 검증
│     ├─ knowledge/         사양서·TC 등록
│     └─ cost_dashboard/    토큰·캐시 집계
└─ services/qa-manual-hub/  하위 서비스 (React + PostgreSQL, 별도 프로세스)
```

| 항목 | 상태 |
|---|---|
| 기술 스택 | FastAPI + Jinja2 + SQLite (핵심 앱), React + PostgreSQL (하위 서비스) |
| DB | SQLite. `documents` / `analyses` / `ai_cache` / `products` / `sync_log` / `manual_*` |
| RAG | **있음.** 로컬 파싱 → Chunking → BM25 검색 → Top-K 후보만 LLM 전달 |
| LLM 호출 | `core/gemini_client.py` 단일 통로. Structured Output, SHA-256 캐시, `thinking_budget=0` |
| 문서 처리 | 파싱 결과를 `document_id` 기준 캐시(`document_cache`). BM25 인덱스는 캐시하지 않음 |
| Agent 구조 | **없음.** 단일 프롬프트 + 후처리 검증 |
| Gate | **없음.** 프롬프트 안 지시로만 존재 |
| 감사 | 있음. 분석 상세 화면에 실제 입출력 JSON |

**중요한 발견: 이미 로컬 RAG다.** 원본 문서를 API에 첨부하는 구조가 아니었다. 보안 요구의
전제(원본 미전송)는 착수 시점에 이미 충족돼 있었다. 새로 만들 것은 RAG가 아니라
**Issue 입력 경로 · Skill/Gate 계층 · 마스킹 · 제품 공통화**였다.

---

## 2. 재사용한 것 (재작성하지 않음)

| 모듈 | 재사용 방식 |
|---|---|
| `core/gemini_client.py` | 그대로. `system_suffix`·`model` 파라미터만 추가 |
| `core/prompt_manager.py` | 그대로. YAML 하나 추가 |
| `core/storage.py` | 그대로. 테이블 1개(`qa_agent_approvals`) + `list_analyses(module=)` 추가 |
| `core/document_cache.py` | 그대로 |
| `retrieval/bm25_retriever.py` | 그대로. exact 검색기를 **옆에** 추가 |
| `retrieval/base.py` Protocol | 그대로. 새 구현체가 이 Protocol을 따른다 |
| `parsers/*` | 그대로. `polarion_issue.py` 추가 |
| `core/config.py` 설정 체계 | 그대로. `security`/`models`/`qa_agent` 섹션 추가 |
| `impact_analyzer` 전체 | **건드리지 않음.** 문서 로딩만 공용 모듈로 추출 |
| `manual_review` 전체 | **건드리지 않음.** S07 Document Change Review를 이 모듈이 담당 |
| 분석 작업 수명주기 | `create_analysis`/`update_stage`/`resume_queued_jobs` 패턴을 그대로 따름 |
| 프롬프트·캐시·토큰 집계 | 그대로. 비용 대시보드가 새 기능도 함께 집계 |

**전면 재작성은 하지 않았다.** 새 기능은 `app/modules/qa_agent/`에 격리했고, 기존 두 모듈의
동작은 회귀 테스트 446건으로 보호했다.

---

## 3. 수정한 것 (최소 변경)

| 파일 | 변경 | 왜 |
|---|---|---|
| `core/gemini_client.py` | `system_suffix`·`model` 파라미터, 마스킹, 모델 폴백 | 규칙 발췌 주입 · 등급 라우팅 · 외부 전송 마스킹 단일 통로 |
| `core/storage.py` | `qa_agent_approvals` 테이블, `list_analyses(module=)` | QA 승인 기록 · 기능별 이력 분리 |
| `core/product_config.py` | `knowledge_source`·`issue_source` 스키마, `${ENV}` 치환 | 제품 공통화 |
| `core/document_schemas.py` | `TestCase.workbook/sheet/row` | 규칙 §9.3이 요구하는 근거 위치가 없었다 |
| `parsers/excel_parser.py` | 실제 Excel 행 번호 기록 | 같은 이유 |
| `impact_analyzer/regression_analyzer.py` | 문서 로딩을 `core/knowledge_documents.py`로 추출 | 두 기능이 같은 문서 집합을 봐야 한다 |
| `impact_analyzer/router.py` | 이력 조회에 `module` 필터 | 이력이 섞이는 것을 막는다 |
| `knowledge/router.py` | 지식 폴더 수집·스캔·죽은 등록 정리 | 실서버 웹에서 실행 가능해야 한다 |
| `web/router.py`, `main.py` | 라우터·스케줄러 등록 | prefix만 결정하는 얇은 계층 유지 |
| `config.yaml` | `security`/`models`/`qa_agent` 섹션 | 값을 코드에 하드코딩하지 않는다 |
| `pytest.ini` | `python_functions = test_*` | 기본 패턴 `test*`가 `testcase_payload`를 테스트로 오인했다 |

### 발견해 고친 결함

| 결함 | 영향 | 수정 |
|---|---|---|
| TC에 Sheet명·실제 행 번호가 없었다 | 규칙 §9.3이 요구하는 근거 위치가 빈 값으로 나갔다 | `TestCase`에 필드 추가, 파서가 실제 행 기록 |
| 등록 문서 하나가 파싱 실패하면 분석 전체가 죽었다 | 원본이 사라진 옛 등록 1건 때문에 실패 | 건너뛰고 `failures`에 남긴다 |
| `documents.revision`이 등록 순번(`Rev.1`)이었다 | `260831`이 `260907`보다 최신으로 판정 | 리비전은 파일명에서만 유도 |
| 문서번호 `RA16-148-002`가 `RA16-148`까지만 잘렸다 | 논리 문서 식별 오류 | `\b` → 부정 룩어헤드 |
| `models.pricing.<model>` 점 표기 조회가 키를 갈랐다 | 단가 설정이 무시됨 | 표 전체를 인덱싱 |
| 지식 폴더 수집이 `documents` 등록까지 이어지지 않았다 | 복사만 되고 분석에 쓰이지 않았다 | `sync_and_register()` |

---

## 4. 신규로 만든 것

| 파일 | 역할 |
|---|---|
| `core/product_knowledge.py` | 제품 지식 폴더 수집·분류·리비전 판별·등록 |
| `core/qa_rules.py` | QA 규칙 문서 파싱 · Skill/Gate 태깅 · 예산 기반 slice |
| `core/knowledge_documents.py` | 문서 로딩 (두 모듈 공유) |
| `core/security_filter.py` | 외부 전송 직전 마스킹 |
| `core/model_router.py` | 모델 등급 선택 · 비용 추정 |
| `parsers/polarion_issue.py` | Issue 구조화 |
| `retrieval/exact_retriever.py` | 식별자 경계 일치 검색 |
| `retrieval/hybrid.py` | Exact → BM25 단계 검색 |
| `modules/qa_agent/analyzer.py` | 파이프라인 8단계 |
| `modules/qa_agent/gates.py` | G1~G5 · ExecutionContext |
| `modules/qa_agent/standard_findings.py` | 규칙 §36/§37/§53/§55 목록 |
| `modules/qa_agent/evidence.py` | Evidence Store |
| `modules/qa_agent/schemas.py` | 응답 스키마 · Regression 축 |
| `modules/qa_agent/validation.py` | ID 교차검증 |
| `modules/qa_agent/rule_capability.py` | 규칙 56개 절 구현현황 표 |
| `modules/qa_agent/scheduled_jobs.py` | 지식 폴더 수집 cron |
| `modules/qa_agent/router.py` + `templates/` | 화면 |
| `prompts/qa_agent_issue_impact.yaml` | 단일 구조화 호출 프롬프트 |
| `scripts/sync_product_knowledge.py` | 지식 폴더 수집 CLI |
| `scripts/rule_capability_report.py` | 규칙 구현현황 리포트 |
| `config/products/bellalun-viewer.yaml` | 두 번째 제품 (코드 변경 없이) |

테스트 신규 7파일 · 약 220건.

---

## 5. 목표 아키텍처 (구현됨)

```text
브라우저 (파트원 5명)
    │
nginx :80  ──────────────────────────────┐
    │ /                                  │ /manual-hub/
    ▼                                    ▼
핵심 앱 (uvicorn :12000)            Manual Hub (별도 프로세스)
    │
    ├─ QA Agent Orchestrator  ← app/modules/qa_agent/analyzer.py
    │     │
    │     ├─ 1 Issue 구조화        코드   polarion_issue
    │     ├─ 2 지식 문서 로드       코드   knowledge_documents (파싱 캐시)
    │     ├─ 3 QA 규칙 로드        코드   qa_rules (Skill별 절)
    │     ├─ 4 Local RAG          코드   Exact → BM25
    │     ├─ 5 Gate G1·G2·G4      코드   gates ─── BLOCK ──▶ 종료 (API 0회)
    │     │                                 │
    │     ├─ Evidence Pack 조립    코드   ai_client
    │     ├─ Security Filter      코드   security_filter (마스킹)
    │     │                                 ▼
    │     ├─ 6 Gemini API  ◀── 최소 Context만 (실측 전체의 1.37%)
    │     ├─ 7 ID 교차검증         코드   validation
    │     └─ 8 Gate G3·G5         코드   gates
    │
    ▼
SQLite  documents · analyses · ai_cache · qa_agent_approvals · sync_log
파일    data/product_knowledge/<제품>/{original,normalized}/  (Git 제외)
```

**원본 문서는 서버 밖으로 나가지 않는다.** 나가는 것은 검색으로 추린 발췌뿐이고, 그것도
마스킹을 거친다.

---

## 6. RAG 설계

### 6.1 사전 처리 (로컬)

| 대상 | 파서 | 캐시 |
|---|---|---|
| SRS 사양서 (PDF/DOCX) | `pdf_parser` / `document_parser` | `document_cache` (document_id 기준) |
| Operation / Service Manual, Integration Guide, DICOM Conformance, API Protocol | 동일 | 동일 |
| TC Excel (다중 시트) | `excel_parser` | 동일 |
| 변경사항 영향성평가 Checklist | 동일 (TC로 취급) | 동일 |
| QA 규칙 MD / 지침 프롬프트 | `qa_rules` (escape 정규화 후 절 단위) | `lru_cache` + manifest 시각 |
| Polarion Issue JSON | `polarion_issue` | — (건당 소형) |

BM25 인덱스는 캐시하지 않는다 — 재구성 비용이 파싱보다 훨씬 작다.

### 6.2 Hybrid Search

```text
1차 Exact   식별자 경계 일치 (VP-xxxx · SRS-xxxx · 0x1234 · DICOM Tag) + 문구 부분 일치
2차 BM25    용어 유사도
3차 Semantic  미구현 (§6.4)
4차 Re-ranking 미구현
```

**점수를 합치지 않고 자리를 나눈다.** exact 히트가 앞자리를 차지하고 남은 자리를 BM25가
채운다. 근거로 "왜 걸렸는지"(`'VP-5500' 정확 일치` / `용어 유사도 0.42`)가 함께 남는다.

BM25 자리를 `min_bm25`만큼 남겨 exact 히트가 한 문서에 몰려도 다른 근거를 놓치지 않는다.
`adaptive=True`면 exact 근거가 확보된 만큼 총 후보 수를 줄인다 — exact 히트가 없으면
줄이지 않는다(Recall 하락).

### 6.3 Evidence Pack

`app/modules/qa_agent/ai_client.py`가 조립한다. 검색 결과를 그대로 넘기지 않는다.

| 부분 | 좁히는 방식 | 실측 |
|---|---|---|
| Issue | 구조화 필드만. 원본 HTML 제외. Comment는 최근 3건·각 400자 | 986자 |
| 사양 Chunk | 검색된 것만, 본문 1,200자까지 | 6,869자 (6개) |
| TC | 후보만, 판정에 쓰는 필드만 (`result`/`remark` 제외) | 16,404자 (40건) |
| Regression 축 | 코드가 고정한 8개 정의 | 730자 |
| 검증 환경 | QA 입력값 | 소형 |
| QA 규칙 | 해당 Skill 절만 (전문 30.7KB → 2~5KB) | 5,019자 |
| **합계** | | **30,207자 ≈ 12.1K 토큰 = 전체의 1.37%** |

Unverified·Excluded는 별도 블록이 아니라 **Gate 판정과 Evidence 수준**으로 표현한다 —
`no_exact_evidence`, `deprecated_spec`, `split_spec_partial`, `can_support_expected=false`.
같은 정보를 두 곳에 두면 어긋난다.

### 6.4 Semantic Search를 넣지 않은 판단

| 이유 | 내용 |
|---|---|
| 필요성 미측정 | exact + BM25의 Recall을 먼저 측정해야 한다. 정확도 평가 루프는 이미 있다 |
| 보안 충돌 | 외부 Embedding API를 쓰면 사양서 원문이 외부로 나간다 — 이 프로젝트의 전제와 충돌 |
| 확장 비용이 작다 | 로컬 임베딩을 세우면 `Retriever` Protocol 구현체 하나를 추가하고 생성 지점만 교체한다. **호출부는 고치지 않는다** |

pgvector 도입은 **하지 않았다.** 핵심 앱은 SQLite이고 PostgreSQL은 하위 서비스(Manual Hub)의
것이다. 두 배포 단위는 DB를 공유하지 않는 것이 이 저장소의 경계 규칙이다
([공용 아키텍처](SHARED_PLATFORM_ARCHITECTURE.md)). 벡터 검색을 도입할 때가 오면 FAISS를
로컬 파일로 두는 쪽이 이 구조와 충돌이 없다.

---

## 7. DB 설계

기존 테이블을 재사용하고 1개만 추가했다. 마이그레이션은 `CREATE TABLE IF NOT EXISTS` +
컬럼 보강 방식이다(반복 실행 안전).

| 테이블 | 용도 | 변경 |
|---|---|---|
| `documents` | 제품별 사양서·TC·매뉴얼 등록 | 기존. `kind='manual'` 사용 시작 |
| `analyses` | 분석 작업 수명주기·결과 JSON | 기존. `module='qa_agent'` 사용 |
| `ai_cache` | SHA-256 응답 캐시 | 기존 |
| `products` / `product_versions` | 제품 마스터 | 기존 |
| `sync_log` | 동기화 이력 | 기존. `kind='product_knowledge'` 사용 시작 |
| **`qa_agent_approvals`** | QA 승인/거절/수정 기록 | **신규** |

`qa_agent_approvals`는 AI 판정을 **덮어쓰지 않는다.** 규칙 §20이 "AI 결과 / QA 수정 결과 /
QA 승인·거절"을 각각 저장해 Rule·Prompt 개선에 쓰라고 정하고 있다.

```sql
qa_agent_approvals(analysis_id, claim_kind, claim_label, qa_decision, qa_note, created_at, updated_at)
UNIQUE(analysis_id, claim_kind, claim_label)
```

파일 저장소:

```text
data/product_knowledge/<슬러그>/
  manifest.json                    수집 이력 · 선택 근거 · 제외 이유
  original/<종류>/<파일명>          원본 사본
  normalized/<종류>/<파일명>.txt    정규화 텍스트
```

`data/product_knowledge/`는 `.gitignore` 대상이다.

---

## 8. 문서 Metadata Schema

### 8.1 지식 자산 (`KnowledgeAsset`)

```text
kind · file_name · source_path · base_name · revision · revision_kind
doc_number · language · status_note · extension · size · sha256 · modified
selected · exclude_reason · superseded_by
logical_id  = kind|base_name|language   ← 같은 논리 문서 판별 키
```

### 8.2 사양 Chunk (`SpecificationChunk`)

```text
chunk_id · document_id · page · heading · text · revision_marks
```

> **알려진 한계.** SRS ID·Legacy SRS No.·Status·Parent/Child·Linked SRS를 Chunk 메타데이터로
> 뽑지 않는다. 현재는 `heading`과 본문에서 exact 검색으로 찾는다. Polarion REST API로 SRS
> 워크아이템을 직접 수집하면 이 필드들을 채울 수 있다 — `alm-issue-export`가 이미 그
> 접근을 갖고 있어 Issue와 같은 방식으로 확장 가능하다. **미구현.**

### 8.3 TC (`TestCase`)

```text
tc_id · category · feature · precondition · step · expected_result · result · remark
workbook · sheet · row        ← 규칙 §9.3 근거 위치 (이번에 추가)
locator = "파일명 · 시트명 · N행"
```

TC ID·행 순서·시트 구조·버전별 결과 이력을 **시스템이 변경하지 않는다.** 읽기만 한다.

### 8.4 Issue (`IssueRecord`)

```text
issue_id · project · title · title_en · status · severity · priority
occurrence_frequency · defect_score · product_stage · created · updated
precondition · steps · expected · actual · log_data · unmarked_body
occurrence_cause · action_details · lab_review_result
issue_type · issue_type_candidates
linked_srs · linked_issues · mentioned_work_items
occurred_versions · target_versions · comments · attachments · body_images
search_terms · source_path · portal_url
```

### 8.5 Evidence

```text
kind(specification|testcase|manual|issue|rule) · ref · document · locator
excerpt · level · match_reason
level_label · can_support_expected · citation
```

근거 수준은 규칙 §7 정보 우선순위를 숫자로 옮긴 것이다. **매뉴얼(4)까지만 Expected를
확정**할 수 있고, 기존 TC(5)·연구소 Comment(9)는 탐색 단서다.

---

## 9. Agent / Skill 구조

Skill은 **업무 책임의 경계**이고 LLM 호출 단위가 아니다. 규칙 §2가 그렇게 정의한다.

| Skill | 상태 | 구현 방식 |
|---|---|---|
| S01 Issue Analysis | **구현** | 필드 추출·유형 분류는 코드, Trigger/Root Cause 의미는 LLM |
| S02 Specification Trace | **구현** | linked SRS exact 검색은 코드, 관련도 판정은 LLM |
| S03 TC Coverage Review | **구현** | 후보 검색은 코드, 6단 판정은 LLM, ID 검증은 코드 |
| S05 Regression Impact | **구현** | 8개 축은 코드, 축별 해당 여부·근거는 LLM |
| S07 Document Change Review | **기존 모듈** | `manual_review`가 담당 |
| S04 / S06 / S08 / S09 / S10 | 미구현 | `/qa-agent/rules`가 현황 원천 |
| S11 Usage Help | 화면 | `/qa-agent/guide` |

**네 Skill을 한 번의 호출로 판단한다.** Skill마다 호출을 쪼개면 같은 근거를 네 번 보내게
되고, Skill 사이에서 모델이 앞 단계 결론을 다시 설명하느라 토큰이 늘어난다.

Skill별 규칙은 `system_suffix`로 그 Skill 절만 주입한다.

---

## 10. Model Routing

난이도 판정에 LLM을 부르지 않는다 — **입력에서 세어지는 신호로만** 고른다.

| 등급 | 모델 | 조건 |
|---|---|---|
| light | `gemini-2.5-flash-lite` | 요약·분류·정형 추출 (현재 QA Agent는 사용하지 않음) |
| standard | `gemini-2.5-flash` | 기본 |
| complex | 설정값 | Root Cause 미명시 / 근거가 3개 이상 문서에 흩어짐 / API·DICOM·WebSocket 용어 / 연결 SRS 다수인데 exact 일치 0건 |

라우팅 결정과 **이유**가 결과에 남는다.

> **실측 제약.** `gemini-2.5-pro` 호출이 404로 실패했다 — "This model is no longer available
> to new users. Please update to `gemini-3.1-pro-preview`". 상위 등급 모델이 계정에서 막혀
> 있어 분석 전체가 실패하는 것은 환경 문제이므로, 기본 모델로 한 번 물러나고 물러났다는
> 사실(`requested`/`used`/`reason`)을 audit에 남긴다. `config.yaml`의 `models.complex`
> 기본값은 검증된 `gemini-2.5-flash`로 두었다.

단가는 `config.yaml` `models.pricing`에 있다. 비용은 **추정치**이고 실제 청구는 Google
콘솔이 기준이다.

**참고 규모**: 실측 입력 12.1K 토큰 기준, 5명 × 하루 10건 × 월 20일 = 월 1,000건이면 Flash로
월 수 달러 수준이다. 캐시 적중과 Gate 차단이 있으면 그보다 낮다.

---

## 11. Security Filter

**단일 통로에 둔다.** `core/gemini_client.py`의 `generate_structured`가 외부로 나가는 모든
문자열이 지나는 곳이다. 기능별로 붙이면 빠뜨릴 여지가 생긴다.

```text
Evidence Pack 조립 → mask_text(prompt) + mask_text(system_suffix) → Gemini
                     └─ MaskReport(무엇이 몇 건) → audit
```

| 마스킹 대상 | 자리표 |
|---|---|
| 이메일 | `[EMAIL]` |
| IPv4 | `[IP_ADDRESS]` |
| Patient ID / Name | `[PATIENT_ID]` / `[PATIENT_NAME]` |
| 병원명 / 고객명 | `[HOSPITAL]` / `[CUSTOMER]` |
| Serial / 계정 / API Key | `[SERIAL]` / `[ACCOUNT]` / `[SECRET]` |
| UNC / 로컬 절대 경로 | `[NETWORK_PATH]` / `[LOCAL_PATH]` |

**마스킹하면 안 되는 것**이 더 위험하다. 버전이나 ErrorCode가 가려지면 분석 자체가
불가능해진다. 다음은 보호한다: Work Item ID(`VP-1234`), SRS ID, 16진(`0x1234`), DICOM
Tag(`(0008,0018)`), `V`로 시작하는 버전(`V1.0.11`).

설계상 주의 두 가지:

- **경로 규칙이 IP 규칙보다 먼저 돈다.** IP를 먼저 지우면 `\\10.13.0.5\qa\...` 패턴이 깨져
  사내 폴더 체계가 남는다.
- **`1.1.0.001`(버전)과 `10.13.0.222`(IP)는 모양이 같다.** 0으로 채운 자리가 있으면 버전으로
  본다. 제품 버전은 마지막 자리를 `001`처럼 채우고 IP는 그러지 않는다.

원본 값은 **보고서에 담지 않는다.** 건수만 기록한다 — 로그에 원본이 남으면 마스킹의 의미가
없다. 끄려면 `security.mask_outbound: false`이지만 운영에서는 켠 채로 둔다.

---

## 12. Audit / Traceability

| 항목 | 기록 위치 | 상태 |
|---|---|---|
| 요청 시간 | `analyses.created_at` | O |
| Issue / 질문 | `analyses.request_json` + `result.issue` | O |
| 검색 Query·Term | `result.retrieval.terms` | O |
| 검색된 Document ID | `result.knowledge_documents` | O |
| 검색된 Chunk ID | `result.evidence.claims[].evidence[].ref` | O |
| Evidence Pack | `result.ai_audit.user_prompt` (실제 전송본) | O |
| **외부로 실제 전송된 Sanitized Context** | `result.ai_audit.user_prompt` + `masking` | O |
| 사용 Model | `result.ai_audit.model` + `routing` + `model_fallback` | O |
| Input / Output Token | `result.token_usage` + `cost_estimate` | O |
| AI 결과 | `result.decision` | O |
| 걸러낸 ID | `result.validation` | O |
| Gate 판정·차단 이유 | `result.gates` + `blocking_reasons` | O |
| QA 최종 판정·수정 내용 | `qa_agent_approvals` | O |
| **사용자** | — | **미구현** |

> **미구현 (사용자 식별).** 핵심 앱에 로그인이 없어 승인 기록에 "누가" 승인했는지 남지
> 않는다. Manual Hub에는 이미 Argon2id + 서버 세션이 있으므로 그 계정을 재사용하는 방법과
> 핵심 앱에 별도 인증을 붙이는 방법이 있다. 어느 쪽이든 **평문 HTTP로 운영하는 현재 상태와
> 함께 결정해야 한다** — 인증을 붙이면서 HTTPS를 미루면 자격증명이 평문으로 흐른다.

민감정보가 로그에 남지 않도록: 마스킹 보고는 건수만, `/config/status`는 API Key의 설정
여부·길이·출처만 반환한다.

---

## 13. 개발 / 운영 환경 분리

현재 상태와 남은 것을 구분한다.

| 항목 | 현재 |
|---|---|
| API Key | `secrets.txt` / `secrets.json` / `.env` / OS 환경변수 (우선순위 정의됨). 배포되지 않으므로 서버에서 직접 넣는다 |
| 마스킹 수준 | `security.mask_outbound` (기본 켬) |
| 모델 | `models.*` |
| 토큰 한도 | `analysis.daily_token_limit` (0=비활성) |
| 캐시 | `analysis.cache_enabled` |
| 로깅 | `output/logs/` |

> **미구현 (`ENV=development|production`).** 위 값들을 개별 설정으로 두고 있어 환경별 프로파일이
> 하나로 묶여 있지 않다. 5명 운영을 시작하면 `config.yaml` 옆에
> `config.production.yaml` 오버레이를 두는 방식이 이 저장소의 설정 체계와 충돌이 없다.
> **회사 보안 정책상 외부 생성형 AI API 사용 가능 여부는 Human Approval Gate로 둔다** —
> 코드가 아니라 운영 승인 문제다.

---

## 14. 구현 단계 — 실제 진행 결과

로드맵 Phase와 대조한다.

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | GPTs 실사용 검증 | 사용자 완료 (규칙 Rev1.12) |
| 2 | Polarion Issue 구조화 | **완료** — `alm-issue-export` 산출물 파싱, 실측 38건 |
| 3 | SRS/TC Parser | **기존** + TC 근거 위치 추가 |
| 4 | BM25 Retrieval | **기존** + Exact 검색 추가 |
| 5 | S01 Issue Analysis | **완료** |
| 6 | S02 Specification Trace | **완료** |
| 7 | S03 TC Coverage | **완료** |
| 8 | S05 Regression Impact | **완료** |
| 9 | Gate Engine | **완료** — G1~G5, 판정을 데이터로 저장 |
| 10 | Cross-check | **완료** — G5 + ID 교차검증 |
| 11 | HTML Dashboard | **완료** — Human Review 6탭 |
| 12 | QA 승인 Workflow | **완료** — `qa_agent_approvals` |
| — | 제품 공통화 | **완료** — Bellalun Viewer가 코드 변경 없이 편입 |
| — | Security Filter | **완료** |
| — | Model Routing | **완료** |
| 2차 | S04/S06/S08/S09/S10 | 미구현 |
| 2차 | Semantic Search / 로컬 Embedding | 미구현 (§6.4 판단) |
| 2차 | SRS 메타데이터 확장 | 미구현 (§8.2) |
| 2차 | 사용자 인증 · ENV 프로파일 | 미구현 (§12, §13) |

---

## 15. 규칙 Rev1.12 전체 구현 가능성

56개 최상위 절 전부를 분류해 `app/modules/qa_agent/rule_capability.py`에 표로 두었다.

| 구현 방식 | 절 수 | 비중 |
|---|---|---|
| 결정적 코드 (LLM 호출 0) | 23 | 41% |
| 코드 + LLM 판정 1회 | 23 | 41% |
| QA 입력 필수 (환경 사실) | 4 | 7% |
| 초안까지만, 확정은 QA | 3 | 5% |
| 런타임 자동화 대상 아님 | 3 | 5% |

**49/56절(88%)이 자동화 범위이고, 그중 32절이 현재 구현돼 있다(전체의 57%).**
단 "전체 구현"의 정의를 규칙 자신이 좁혀 놓았다.

| 구현 상태 | 절 수 |
|---|---|
| 구현됨 | 32 |
| 구현 안 함 (2차 확장 21 + 런타임 대상 아님 3) | 24 |

- **§54 자동화 Handoff 원칙**이 "Python/API/RAG 자동화로 확장"과 10단계 순서를 명시한다 —
  이 시스템이 그 절의 구현이다.
- **§55 자동화 금지 원칙**이 8가지 자동 확정을 금지한다. 그래서 §16(기존 TC 첨삭)·
  §29(Issue 작성)·§41(종료 Comment)은 **초안까지만**이 규칙 준수다.
- **자동화가 원리적으로 불가능한 4절**은 QA만 아는 환경 사실이다: §5(수행 가능 범위·
  Test Data), §22(Generator 연결), §27(Dose Table 유무), §28(실제 Exposure 가능 여부).
- **런타임 대상이 아닌 3절**: §47(Skill 설계 24점 자가진단 — 사람용), §49(Debt 관리 —
  운영 프로세스), §56(Rev 변경 이력).

표를 코드로 두면 두 가지 드리프트가 테스트로 잡힌다.

- 규칙 Rev가 올라가 새 절이 생기면 `unclassified`로 실패 → 재분류를 강제한다.
- `status=IMPLEMENTED`인데 `where` 파일이 없으면 실패 → 구현 주장과 코드가 어긋나지 않는다.

```bash
python scripts/rule_capability_report.py
```

---

## 16. 이 구조가 일반 RAG 챗봇과 다른 점

| | 일반 사내 RAG 챗봇 | 이 시스템 |
|---|---|---|
| 입력 | 자유 질문 | Issue 1건 (구조화된 워크아이템) |
| 출력 | 자연어 답변 | Gate 판정 + 근거 붙은 판정 레코드 |
| 근거 | 인용 문단 | 문서·페이지·절 / 파일·시트·행 + 근거 수준 |
| 판정 기준 | 프롬프트 | 제품 QA 규칙 문서(Rev 관리) → Skill별 발췌 |
| 실패 처리 | 모델이 "모르겠다" | Gate가 API 호출 전에 차단하고 이유를 저장 |
| 검증 | 없음 | 모델이 만든 ID를 실제 데이터와 대조해 제외 |
| 확정 | 답변이 곧 결론 | QA 승인이 별도 레코드. AI 결과를 덮어쓰지 않음 |
| 자동화 경계 | 없음 | 규칙 §55가 금지한 8가지는 코드 경로 자체가 없음 |

목표는 답변 생성이 아니라 **Issue → Specification → TC → Regression 추적성을 근거 기반으로
남기는 것**이다.
