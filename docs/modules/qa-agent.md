# QA Agent — Issue 검증 범위 분석

> 상위: [문서 지도](../README.md) · 사용법: 앱 안 `/qa-agent/guide` · 사용 안내: [USER_GUIDE](../USER_GUIDE.md#2-qa-agent--issue-검증-범위)

Polarion Issue 하나에서 **관련 사양 → 기존 TC → Regression 범위**를 근거와 함께 정리한다.
AI가 QA를 판정하지 않는다 — QA가 반복적으로 수행하는 조사·비교·추적 절차를 표준화한다.

---

## 1. 왜 이 기능이 따로 있는가

기존 [Regression 영향 분석](impact-analyzer.md)은 **변경 문서**를 입력으로 받는다. 하지만
QA 업무의 절반은 이미 등록된 Issue에서 시작한다. 그때 필요한 것은 다르다.

| | Regression 영향 분석 | QA Agent |
|---|---|---|
| 입력 | 변경 문서 / 요청 텍스트 | Polarion Issue 1건 |
| 시작 질문 | 이 변경으로 어디까지 다시 검증하나 | 이 Issue를 어디까지 검증하나 |
| 판정 단위 | 변경 항목 → TC 추천 | Issue Root Cause → TC Coverage 판정 |
| 근거 특성 | 문서 diff | Issue가 명시한 SRS Link + Root Cause |

두 기능은 **같은 문서 집합**(`app/core/knowledge_documents.py`)을 본다. 서로 다른 집합을
보면 같은 제품에 대해 다른 결론이 나오고 그 차이를 설명할 수 없다.

---

## 2. 파이프라인

QA 규칙 §54가 정한 10단계 순서를 따른다. 이 모듈이 1~8을 담당하고 9~10은 QA의 몫이다.

```text
1 Issue 구조화        코드   polarion_issue.py     backup.json → 표준 구획 분리
2 지식 문서 로드       코드   knowledge_documents   등록 사양서·TC 전체 (파싱 캐시)
3 QA 규칙 로드        코드   qa_rules.py           제품 규칙 → Skill별 절 태깅
4 근거 검색           코드   retrieval/hybrid.py   Exact(식별자) → BM25(용어)
5 Gate G1·G2·G4       코드   gates.py              ← 막히면 여기서 끝. API 호출 0회
6 AI 의미 판단        LLM    ai_client.py          1회 구조화 호출
7 ID 교차검증          코드   validation.py         없는 ID 제거, 번호 일치 검사
8 Gate G3·G5          코드   gates.py              Coverage · 모순 · 근거 없는 확정
9 Human Review        사람   templates/analysis.html
10 QA 승인            사람   qa_agent_approvals 테이블
```

**LLM이 등장하는 곳은 6단계 한 곳뿐이고, 그것도 1회다.** 나머지는 전부 결정적인 코드다.

### 왜 Gate를 코드로 두는가

두 가지 이유가 있고 둘 다 실용적이다.

1. **토큰.** Gate가 막으면 LLM을 부르지 않는다. 근거가 부족한 Issue는 API 호출 0회로
   "무엇이 없어서 못 한다"만 돌려준다. 규칙 §53 최종 중단 조건이 정확히 이 경우다.
2. **재현성.** "근거가 충분한가"를 LLM에 물으면 같은 입력에 다른 답이 나온다. 있는지
   없는지는 세면 되는 문제다.

Gate 판정은 **차단 이유와 함께 데이터로 저장된다.** 이유가 남지 않으면 규칙 §55가 금지한
"문서 검색 실패를 숨긴 확정 판정"이 된다.

---

## 3. 설계 결정과 근거

### 3.1 Issue 유형을 LLM에 묻지 않는다

Polarion의 `rndReviewResult`(연구소 검토 결과)가 규칙 §6 Issue 산출물 유형과 대응한다.
실측 38건 중 33건이 코드로 분류됐다.

| `rndReviewResult` | 규칙 §6 유형 | 실측 |
|---|---|---|
| `lab_fixed` | A. Program Fixed | 19 |
| `lab_inspec` | B. Spec / As Designed | 6 |
| `lab_nobug` | B. Not Bug | 3 |
| `lab_duplicate` | E. Duplicate | 3 |
| `lab_pending` | F. Blocked | 2 |
| `lab_noact` | **확정하지 않음** (B 또는 G) | 5 |

`lab_noact`는 사양대로(B)인지 재현 불가(G)인지 코드만으로 갈리지 않는다. 규칙 §37이 근거
없는 확정을 금지하므로 **후보만 남기고 QA가 고른다** — G1이 `NEED_INPUT`으로 세운다.

### 3.2 Issue 본문도 코드로 구조화된다

`reproductionStep` 본문이 규칙 §29 표준 형식을 그대로 쓴다. 실측 38건 중 31건.

```text
[Title] [Precondition] [Step] [Expected Result] [Actual Result] [Log, Data]
```

한국어 별칭(`[기대결과]`, `[현재결과]`)도 처리한다. `[동물용]`처럼 **알려진 마커가 아닌
대괄호 꼬리표는 구획을 바꾸지 않고 내용으로 취급**한다 — 실제 본문에 쓰인다.

마커가 없는 Issue도 분석되지만 어느 구획이 없는지 G1이 기록한다. 실측 7건이 `[Step]`이
없었다. 파서 결함이 아니라 **Issue 작성 품질에 관한 사실**이다.

### 3.3 Exact와 BM25를 점수로 합치지 않는다

규칙 §9.1이 정한 조사 키(SRS No, Issue No, Command No, DICOM Tag)는 대부분 문자열 그대로
일치하는 식별자다. BM25로 찾으면 손해다.

- `VP-5500`을 BM25로 찾으면 토큰이 `vp`/`5500`으로 쪼개져 무관한 Chunk가 섞인다.
- `(0008,0018)` 같은 Tag는 토크나이저에서 구두점이 사라진다.
- `VP-55`가 `VP-5500`에 걸리는 오탐을 BM25로는 막을 수 없다 (경계 일치가 필요하다).

그래서 **점수를 섞지 않고 자리를 나눈다.** exact 히트가 앞자리를 차지하고 남은 자리를
BM25가 채운다. 점수를 정규화해 합치면 "왜 이 순위인지"를 설명할 수 없고, 가장 강한 근거인
exact 히트가 BM25 점수에 묻힌다.

`adaptive=True`면 exact 근거가 확보된 만큼 **총 후보 수 자체를 줄인다.** exact 히트가 없으면
줄이지 않는다 — Recall이 떨어지기 때문이다.

### 3.4 Semantic Search를 넣지 않은 이유

로드맵의 3차 Semantic Search·4차 AI Re-ranking은 구현하지 않았다. 판단 근거는 두 가지다.

- **필요성이 아직 측정되지 않았다.** exact + BM25로 확보되는 Recall을 먼저 측정해야 한다.
- **Embedding을 외부 API로 만들면 이 프로젝트의 전제와 충돌한다.** 사양서 원문을 외부로
  보내야 하기 때문이다. 로컬 임베딩 모델을 세우면 `Retriever` Protocol 구현체를 하나 더
  추가하는 것으로 끝난다 — 호출부는 고치지 않는다.

### 3.5 Regression 축을 코드가 고정한다

규칙 §32의 8개 축을 코드가 정하고, 모델은 **해당 여부와 근거만** 채운다.

| 축 | 무엇을 보는가 |
|---|---|
| 직접 | 변경된 기능 자체 |
| 상태 | A→B, B→A, A→B→A · Error→정상 · 연결→해제→재연결 |
| 데이터 | Cache · ErrorCode · List · Patient · Study 잔존·오염 |
| 저장 | Save · Reload · 재진입 · 재시작 후 값 유지 |
| 연동 | DICOM · API · WebSocket · 외부 장치 |
| 권한 | 계정 권한별 노출·동작 차이 |
| 기존 Issue | 같은 Root Cause로 과거에 났던 Issue의 재발 |
| Generator | Step · Size · Study · Dose Mode · Focal Spot · Save Dose |

축을 모델이 만들면 실행마다 달라져 결과를 비교할 수 없다. **해당하지 않는 축도
"검토했으나 해당 없음"으로 남긴다** — 검토했다는 기록이 있어야 다음 사람이 처음부터
훑지 않는다. 모델이 판정하지 않은 축은 "QA 확인 필요"로 채워진다.

### 3.6 모델이 만든 ID를 믿지 않는다

`app/modules/qa_agent/validation.py`가 강제한다. 기존 `impact_analyzer/validation.py`와
같은 원칙이다.

- 응답의 `tc_id`가 실제 TC 목록에 없으면 그 판정을 **결과에서 제외**한다.
- 근거 `chunk_id`는 실제 Chunk ID만 남긴다.
- 근거가 하나도 남지 않으면 confidence를 review 임계값 아래로 내리고, **"Root Cause를
  덮는다"를 인정하지 않는다** (규칙 §33).
- Expected 번호가 Step 범위를 벗어나면 기록한다 (규칙 §14).
- 걸러낸 항목을 **숨기지 않고** 화면에 남긴다.

### 3.7 규칙 전문을 매 호출에 보내지 않는다

제품 QA 규칙 문서는 약 30KB다. 그대로 보내면 호출 하나에 그것만으로 1만 토큰이 넘고,
대부분은 그 요청과 무관하다.

`app/core/qa_rules.py`가 규칙을 절 단위로 쪼개 Skill(S01~S11)·Gate(G1~G5)로 태깅하고,
호출 시점에 그 Skill 절만 문자 예산 안에서 주입한다. `akela compile`이 에이전트에게 지식을
잘라 주는 것과 같은 원리다.

VXvue 실측: 규칙 전문 30.7KB(140개 절) → Skill별 slice **2.0~4.7KB (85~94% 절감)**.

두 가지가 이 계층의 품질을 만든다.

- **하위 절이 부모 태그를 물려받는다.** `# 32. Regression 영향 범위` 아래 `## 직접`/`## 상태`는
  제목만으로 주제를 알 수 없다. 상속을 넣기 전에는 미태깅이 140절 중 71절이었고 넣은 뒤 39절이다.
- **Skill 전용 절과 공통 절의 예산을 분리한다.** 한 예산으로 합치면 문서 앞쪽 공통 절이
  예산을 다 먹는다 (S01 slice 5.8KB 중 Skill 전용이 0.4KB뿐인 상태였다).

규칙 문서는 escape된 상태(`\#`, `&#x20;`, 줄마다 빈 줄)로 저장돼 있어 정규화가 먼저 필요하다.
원본 그대로는 `grep '^#'` 결과가 0건이다.

---

## 4. 실제로 무엇이 외부로 나가는가

VXvue Issue 1건 분석 실측.

| 항목 | 크기 |
|---|---|
| 등록 문서 전체 (사양 Chunk 737개 + TC 6,407건 + 규칙) | **2.11MB ≈ 883K 토큰** |
| **실제 API 전송량** | **30,207자 ≈ 12.1K 토큰** |
| 비율 | **1.37%** |

내부 구성:

| 부분 | 크기 | 줄이는 방식 |
|---|---|---|
| Issue | 986자 | 구조화 필드만. 원본 HTML·전체 Comment 제외 (최근 3건만) |
| 사양 Chunk 6개 | 6,869자 | 검색된 것만, 본문 1,200자까지 |
| TC 후보 40건 | 16,404자 | 후보만, 판정에 쓰는 필드만 (`result`/`remark` 제외) |
| Regression 축 8개 | 730자 | 축 정의 (고정) |
| 규칙 발췌 | 5,019자 | 해당 Skill 절만 |

**원본 PDF·Excel·DOCX는 서버 밖으로 나가지 않는다.** 파싱·검색·후보 압축이 전부 서버 안에서
끝난다.

호출 직전에 `app/core/security_filter.py`가 개인정보·사내 경로를 자리표로 바꾼다. 실제
전송본은 결과 화면 감사 영역에서 원문 그대로 볼 수 있다 — 호출자가 만든 원본이 아니라
**마스킹까지 끝난 것**을 보여준다.

---

## 5. Gate 정의

| Gate | 무엇을 보는가 | 시점 | BLOCK 조건 |
|---|---|---|---|
| **G1** Source Completeness | Issue·사양서·TC·규칙·버전 | LLM 전 | 사양서 0건 / TC 0건 / 규칙 미수집 / Issue 식별 불가 |
| **G2** Specification Evidence | 사양 근거, 취소선, 분할 사양 | LLM 전 | 사양 근거 0건 |
| **G4** Execution Feasibility | 수행 가능성, 관찰 수단 | LLM 전 | 필수 관찰 수단 미확보 |
| **G3** TC & Root Cause Coverage | 기존 TC가 Root Cause를 덮는가 | LLM 후 | — (note/input) |
| **G5** Cross-check | 모순, 근거 없는 확정 | LLM 후 | Spec 유형에 Runtime TC 생성 |

판정은 `PASS` / `PASS_WITH_NOTE` / `NEED_INPUT` / `BLOCK` / `PENDING`.

`NEED_INPUT`은 QA가 초안을 허용하면(`draft_allowed`) 넘어간다 — 규칙 §53의 예외 조항이다.
**`BLOCK`은 초안 허용으로도 넘기지 않는다.**

### G2의 실제 강도 (알려진 한계)

BM25는 소표본에서 관련도와 무관하게 후보를 채운다(기존 구현의 의도적 fallback). 그래서
"사양 근거 0건" 차단이 실제로 걸리는 조건은 **검색 결과가 아예 없을 때**다 — 등록 문서가
있는데 Chunk를 만들지 못한 경우(파싱 실패)가 여기 해당한다.

유사도로만 찾은 근거는 막지 않고 `no_exact_evidence`로 기록한다. 그 사실이 남지 않으면
약한 근거가 강한 근거처럼 보인다. **판단은 QA의 몫**이라는 것이 규칙 §8의 요구다.

---

## 6. QA가 입력하는 환경 사실

규칙이 "QA만 아는 사실"로 정한 값이다. 자동으로 알아낼 수 없고 최종 판정에 영향을 준다.

| 항목 | 규칙 | 미입력 시 |
|---|---|---|
| 실제 수행 가능 여부 | §5.2 | G4 `NEED_INPUT` |
| Test Data 준비 | §5.2 | G4 `NEED_INPUT` |
| 실제 X-ray Exposure 가능 | §28 | 해당 없음. `불가`면 실제 촬영 결과를 Expected로 만들지 않음 |
| Dose Table 제공 | §27 | 해당 없음. `미제공`이면 공식 촬영 가능 조합·출력 정확도 확정 금지 |
| 필수 관찰 수단 | §55 | 제약 없음. 지정 후 미확보면 G4 `BLOCK` |

---

## 7. 규칙이 금지하는 것 (§55)

코드 경로 자체가 없어야 하는 것들이다. 그래서 이 기능은 다음을 **하지 않는다.**

| 금지 항목 | 이 모듈의 대응 |
|---|---|
| AI 분석만으로 Issue 자동 Close | Close 경로가 없다 |
| 근거 없는 SRS 자동 연결 | 근거 없는 판정은 confidence를 내리고 "근거 없음" 표시 |
| TC 결과/이력 자동 삭제 | TC 파일을 읽기만 한다 |
| 필수 관찰 수단 없이 Pass | G4가 BLOCK |
| 문서 검색 실패를 숨긴 확정 판정 | Gate 차단 이유·읽지 못한 문서를 결과에 남긴다 |
| Source 위치 없는 판단만 저장 | G5가 `claim_without_source`로 기록 |
| QA 승인 없이 기존 TC 덮어쓰기 | 판정과 수정안까지만. `TC_JUDGMENTS_REQUIRING_APPROVAL` |
| Spec 근거 불충분 상태에서 Expected 자동 생성 | 근거 없으면 빈 문자열로 두도록 프롬프트가 지시하고 G5가 검사 |

QA 결정은 AI 판정을 **덮어쓰지 않고** `qa_agent_approvals`에 따로 쌓인다. 규칙 §20이
"AI 결과 / QA 수정 결과 / QA 승인·거절"을 각각 저장해 Rule·Prompt 개선에 쓰라고 정하고 있다.

---

## 8. 파일 구성

```text
app/modules/qa_agent/
  analyzer.py            파이프라인 (8단계)
  ai_client.py           payload 조립 + 규칙 발췌 주입 (1회 호출)
  gates.py               G1~G5 + ExecutionContext
  standard_findings.py   규칙 §36/§37/§53/§55 목록을 코드로
  evidence.py            Evidence Store (근거 수준 · 위치)
  schemas.py             응답 스키마 (TC 판정 · Regression 축)
  validation.py          ID 교차검증 · Step–Expected 번호
  rule_capability.py     규칙 56개 절의 구현 방식 표
  scheduled_jobs.py      제품 지식 폴더 수집 cron
  router.py              화면·API
  templates/             index · analysis(6탭) · guide · rules · history

app/core/                (공용)
  product_knowledge.py   제품 지식 폴더 수집·분류·리비전 판별
  qa_rules.py            규칙 문서 파싱·Skill 태깅·slice
  knowledge_documents.py 문서 로딩 (impact_analyzer와 공유)
  security_filter.py     외부 전송 직전 마스킹
  model_router.py        모델 등급 선택 · 비용 추정

app/parsers/polarion_issue.py    Issue 구조화
app/retrieval/exact_retriever.py 식별자 정확 일치
app/retrieval/hybrid.py          Exact → BM25 단계 검색
app/prompts/qa_agent_issue_impact.yaml
```

---

## 9. 설정

| 키 | 기본값 | 의미 |
|---|---|---|
| `qa_agent.rule_char_budget` | 5000 | 호출 1회에 주입할 규칙 발췌 상한(문자) |
| `qa_agent.tc_candidate_limit` | 40 | AI에 보낼 TC 후보 상한 |
| `qa_agent.force_model_tier` | `""` | `light`/`standard`/`complex` 고정. 빈 값이면 자동 |
| `retrieval.specification_top_k` | 8 | 사양 Chunk 후보 상한 |
| `security.mask_outbound` | `true` | 외부 전송 마스킹 |
| `models.standard` | `gemini-2.5-flash` | 기본 모델 |
| `models.complex` | `gemini-2.5-flash` | 상위 등급. `gemini-2.5-pro`는 신규 계정 미제공(실측) |
| `analysis.daily_token_limit` | 0 | 0=비활성 |
| `analysis.max_concurrent_jobs` | 2 | 초과 요청은 429 |

`config/products/<slug>.yaml`의 `issue_source.export_dir`에 Polarion Export 폴더를 넣으면
Issue ID 목록이 화면에 채워진다. 서버에 그 폴더가 없으면 `backup.json`을 업로드한다.

---

## 10. 미구현 (2차 확장)

규칙 절별 구현현황은 `/qa-agent/rules`가 원천이다. 현재 미구현인 Skill:

| Skill | 상태 |
|---|---|
| S04 Fix Verification | 미구현 — 수정확인 Checklist 설계 |
| S06 Issue Writing & Closure | 미구현 — 초안까지만 만드는 것이 규칙 준수 |
| S07 Document Change Review | **매뉴얼 개정 검증 모듈이 이미 담당** |
| S08 API/WebSocket/DICOM | 미구현 — Command 표 추출은 코드로 가능 |
| S09 Generator Verification | 미구현 — Dose Table·장비 상태 입력이 선행 |
| S10 Release Validation | 미구현 |
| S11 Usage Help | 앱 안 `/qa-agent/guide`가 담당 |

```bash
python scripts/rule_capability_report.py --status NOT_PLANNED
```
