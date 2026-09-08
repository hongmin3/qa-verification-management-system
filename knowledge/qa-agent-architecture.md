# QA Agent — Issue 검증 범위 분석

> Polarion Issue 하나에서 관련 사양·기존 TC·Regression 범위를 근거와 함께 정리한다.
> AI가 QA를 판정하지 않는다 — 조사·비교·초안까지가 자동화 범위이고 확정은 QA다.

## Gate는 프롬프트가 아니라 코드로 판정한다
<!-- akela: id=gate-in-code scope=qa-agent-dev,core-development tier=must -->

- G1·G2·G4는 **LLM 호출 전에** 판정한다. 막히면 API 호출이 0회다.
- "근거가 충분한가"를 LLM에 물으면 같은 입력에 다른 답이 나온다. 있는지 없는지는 세면 되는 문제다.
- Gate 판정과 **차단 이유를 함께** 데이터로 저장한다. 이유가 남지 않으면 QA 규칙 §55가 금지한 "문서 검색 실패를 숨긴 확정 판정"이 된다.
- 판정값은 `PASS` / `PASS_WITH_NOTE` / `NEED_INPUT` / `BLOCK` / `PENDING`.
- `NEED_INPUT`은 QA가 초안을 허용하면 넘어간다. **`BLOCK`은 초안 허용으로도 넘기지 않는다.**
- G3·G5는 판정 결과가 나와야 검사할 수 있어 LLM 호출 이후다. `GateReport.blocks_llm`은 G1·G2·G4만 본다.

## LLM 호출은 분석 1건당 1회다
<!-- akela: id=single-call-four-skills scope=qa-agent-dev tier=must -->

- S01(Issue 의미) · S02(사양 관련도) · S03(TC Coverage) · S05(Regression)을 **한 번의 구조화 호출**로 받는다.
- Skill마다 호출을 쪼개면 같은 근거를 네 번 보내게 되고, Skill 사이에서 모델이 앞 단계 결론을 다시 설명하느라 토큰이 늘어난다.
- 모델에게 **묻지 않는 것**: Issue 유형 분류(코드), 검색 키워드 생성(정규식), 근거 충분성(Gate), Step/Expected 번호 일치(코드).

## Issue 유형은 코드로 분류한다
<!-- akela: id=issue-type-from-lab-review scope=qa-agent-dev tier=must -->

- Polarion `rndReviewResult`가 QA 규칙 §6 Issue 산출물 유형과 대응한다. 실측 38건 중 33건이 LLM 없이 분류됐다.
- `lab_fixed`→Program Fixed, `lab_inspec`/`lab_nobug`→Spec/Not Bug, `lab_duplicate`→Duplicate, `lab_pending`→Blocked.
- `lab_noact`는 사양대로(B)인지 재현 불가(G)인지 코드만으로 갈리지 않는다. **확정하지 않고 후보만 남기고** G1이 `NEED_INPUT`으로 세운다 (규칙 §37이 근거 없는 확정을 금지).
- Spec/Not Bug·Document Fix·Inquiry 유형은 요구받지 않으면 Runtime TC를 자동 생성하지 않는다 (`blocks_auto_runtime_tc`). G5가 검사한다.

## Issue 본문도 코드로 구조화된다
<!-- akela: id=issue-body-markers scope=qa-agent-dev tier=should -->

- `reproductionStep` 본문이 규칙 §29 표준 형식(`[Title]`/`[Precondition]`/`[Step]`/`[Expected Result]`/`[Actual Result]`/`[Log, Data]`)을 쓴다. 실측 38건 중 31건.
- 한국어 별칭(`[기대결과]`, `[현재결과]`)도 처리한다.
- **알려진 마커가 아닌 대괄호 꼬리표는 구획을 바꾸지 않고 내용으로 취급한다** — `[동물용]`, `[뷰어]`가 실제 본문에 쓰인다.
- 마커가 전혀 없으면 본문 전체를 재현 절차로 본다. 내용을 잃지 않기 위함이다.
- 마커가 일부만 있는 Issue도 분석하되 어느 구획이 없는지 G1이 기록한다. 파서 결함이 아니라 Issue 작성 품질에 관한 사실이다.

## Exact와 BM25를 점수로 합치지 않는다
<!-- akela: id=hybrid-not-blended scope=qa-agent-dev,core-development tier=must -->

- 식별자(`VP-5500`, `SRS-1234`, `0x1234`, DICOM Tag)는 **경계 일치**로 찾는다. BM25로는 `VP-55`가 `VP-5500`에 걸리는 오탐을 막을 수 없고, 구두점이 토크나이저에서 사라진다.
- exact 히트가 앞자리를 차지하고 남은 자리를 BM25가 채운다. **점수를 정규화해 합치면** 순위 근거를 설명할 수 없고 가장 강한 근거가 BM25 점수에 묻힌다.
- BM25 자리를 `min_bm25`만큼 남긴다 — exact 히트가 한 문서에 몰려도 다른 근거를 놓치지 않기 위함이다.
- `adaptive=True`면 exact 근거가 확보된 만큼 총 후보 수를 줄인다. **exact 히트가 없으면 줄이지 않는다** (Recall 하락).
- 검색 결과에 "왜 걸렸는지"(`'VP-5500' 정확 일치` / `용어 유사도 0.42`)가 함께 남는다.

## Regression 축은 코드가 고정한다
<!-- akela: id=regression-axes-fixed scope=qa-agent-dev tier=must -->

- 규칙 §32의 8개 축(직접/상태/데이터/저장/연동/권한/기존 Issue/Generator)을 코드가 정하고 모델은 해당 여부와 근거만 채운다.
- 축을 모델이 만들면 실행마다 달라져 결과를 비교할 수 없다.
- **해당하지 않는 축도 `applicable=false`로 반드시 남긴다** — 검토했다는 기록이 있어야 다음 사람이 처음부터 훑지 않는다.
- 모델이 판정하지 않은 축은 검증 단계가 "QA 확인 필요"로 채운다.

## 모델이 만든 ID를 믿지 않는다
<!-- akela: id=qa-agent-id-validation scope=qa-agent-dev tier=must -->

`app/modules/qa_agent/validation.py`가 강제한다.

- 응답의 `tc_id`가 실제 TC 목록에 없으면 그 판정을 **결과에서 제외**한다.
- 근거 `chunk_id`는 실제 Chunk ID만 남긴다.
- 근거가 하나도 남지 않으면 confidence를 review 임계값 아래로 내리고 **`covers_root_cause`를 False로 되돌린다** (규칙 §33 — 근거 없이 Root Cause Coverage를 인정하지 않는다).
- Expected 번호가 Step 범위를 벗어나면 기록한다 (규칙 §14).
- 걸러낸 항목을 **숨기지 않고** 화면에 남긴다.

## Evidence에는 근거 수준과 위치가 있어야 한다
<!-- akela: id=evidence-level-and-locator scope=qa-agent-dev tier=must -->

- 근거 수준은 규칙 §7 정보 우선순위를 숫자로 옮긴 것이다. 숫자가 작을수록 강하다.
- **매뉴얼(4)까지만 Expected를 확정**할 수 있다 (`EXPECTED_EVIDENCE_MAX_LEVEL`). 기존 TC(5)·연구소 Comment(9)는 탐색 단서다.
- 위치는 원문에서 찾아갈 수 있어야 한다 (규칙 §9.3): 사양은 `문서명 · p.12 · 4.3.2`, TC는 `파일명 · 시트명 · N행`.
- 근거 없는 판정은 `EvidenceStore.unsourced`로 드러난다. 규칙 §55 위반 후보다.

## 규칙이 자동 확정을 금지한 것은 코드 경로 자체가 없다
<!-- akela: id=automation-prohibitions scope=qa-agent-dev tier=must -->

규칙 §55의 8가지다. 새 기능을 붙일 때도 이 경계를 넘지 않는다.

- AI 분석만으로 Issue 자동 Close — Close 경로가 없다.
- 근거 없는 SRS 자동 연결 · Source 위치 없는 판단만 저장 — G5가 기록한다.
- TC 결과/이력 자동 삭제 · QA 승인 없이 기존 TC 덮어쓰기 — TC 파일을 읽기만 한다.
- 필수 관찰 수단 없이 Pass — G4가 BLOCK.
- 문서 검색 실패를 숨긴 확정 판정 — Gate 차단 이유와 읽지 못한 문서를 결과에 남긴다.
- Spec 근거 불충분 상태에서 Expected 자동 생성 — 프롬프트가 빈 문자열을 지시하고 G5가 검사한다.
- QA 결정은 AI 판정을 **덮어쓰지 않고** `qa_agent_approvals`에 따로 쌓인다 (규칙 §20).

## QA만 아는 환경 사실은 입력받는다
<!-- akela: id=execution-context scope=qa-agent-dev tier=should -->

- `ExecutionContext`: 실제 수행 가능 여부, Test Data 준비, X-ray Exposure 가능, Dose Table 제공, 확보/필수 관찰 수단, 초안 허용.
- 미입력을 `False`로 바꾸지 않는다. `None`(모름)과 `False`(불가)는 다른 판정을 만든다.
- 필수 관찰 수단이 지정됐는데 확보되지 않으면 G4가 **BLOCK**이다 — 다른 수단으로 Pass 처리하지 않기 위함이다.

## 규칙 절별 구현현황은 코드가 원천이다
<!-- akela: id=rule-capability-table scope=qa-agent-dev,documentation tier=should -->

- `app/modules/qa_agent/rule_capability.py`가 규칙 56개 최상위 절의 구현 방식·상태·위치를 담는다.
- 규칙 Rev가 올라가 새 절이 생기면 `tests/test_rule_capability.py`가 실패한다 — 재분류를 강제한다.
- `status=IMPLEMENTED`인데 `where` 파일이 없으면 실패한다 — 구현 주장과 코드가 어긋나지 않게 한다.
- 문서에 숫자를 손으로 적지 않고 `scripts/rule_capability_report.py`를 인용한다.

## 알려진 한계
<!-- akela: id=qa-agent-known-limits scope=qa-agent-dev tier=should -->

- **G2의 "사양 근거 0건" 차단은 검색 결과가 아예 없을 때만 걸린다.** BM25가 소표본에서 관련도와 무관하게 후보를 채우기 때문이다(기존 구현의 의도적 fallback). 유사도로만 찾은 근거는 막지 않고 `no_exact_evidence`로 기록한다.
- Semantic Search·Re-ranking은 미구현이다. 외부 Embedding API를 쓰면 사양서 원문이 외부로 나가 이 프로젝트의 전제와 충돌한다. 로컬 임베딩이 준비되면 `Retriever` Protocol 구현체만 추가한다.
- 사양 Chunk에 SRS ID·Status·Parent/Child·Linked SRS 메타데이터가 없다. `heading`과 본문 exact 검색으로 찾는다.
- 핵심 앱에 로그인이 없어 QA 승인 기록에 사용자가 남지 않는다.
- `gemini-2.5-pro`는 신규 계정에 제공되지 않는다(실측 404). 상위 등급 모델이 막히면 기본 모델로 물러나고 그 사실을 audit에 남긴다.
