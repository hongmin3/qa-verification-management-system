# 사용 안내 — 기능별 사용법

> 상위: [문서 지도](README.md) · [README](../README.md)

이 문서는 **어느 기능을 언제 쓰는지**와 **각 기능이 무엇을 보장하고 무엇을 보장하지 않는지**를
정리한다. 화면 단위 조작 절차는 앱 안 `사용법` 메뉴에 있다 — 화면이 바뀌면 같이 바뀌어야
하기 때문에 그쪽이 원본이다.

| 기능 | 주소 | 앱 안 사용법 |
|---|---|---|
| QA Agent — Issue 검증 범위 | `/qa-agent` | `/qa-agent/guide` |
| Regression 영향 분석 | `/impact-analyzer` | `/impact-analyzer/guide` |
| 매뉴얼 개정 검증 | `/manual-review` | `/manual-review/guide` |
| Knowledge — 문서·규칙 관리 | `/knowledge` | `/knowledge/guide` |
| 비용 대시보드 | `/cost-dashboard` | `/cost-dashboard/guide` |
| 규칙 구현현황 | `/qa-agent/rules` | (화면 자체가 설명) |
| QA Manual Hub (하위 서비스) | `/manual-hub/` | [USERGUIDE](../services/qa-manual-hub/docs/USERGUIDE.md) |

---

## 0. 어느 기능을 쓸지 — 입력으로 고른다

세 분석 기능은 **입력이 다르다.** 목적이 아니라 손에 있는 자료로 고르면 된다.

```text
Issue 가 등록됐다              → QA Agent
변경 문서(사양 변경·릴리스)가 있다 → Regression 영향 분석
개정된 매뉴얼을 받았다          → 매뉴얼 개정 검증
```

| | QA Agent | Regression 영향 분석 | 매뉴얼 개정 검증 |
|---|---|---|---|
| 입력 | Polarion Issue 1건 | 변경 문서(PDF/DOCX) 또는 요청 텍스트 | 개정 매뉴얼(DOCX Track Changes / PDF) |
| 질문 | 이 Issue를 어디까지 검증해야 하는가 | 이 변경으로 어디까지 다시 검증해야 하는가 | 이 매뉴얼이 최신 사양을 반영했는가 |
| 산출물 | Issue 분석 · 사양 근거 · TC Coverage 판정 · Regression Matrix | Regression TC 추천 · 신규 TC 초안 · HTML/XLSX 보고서 | 변경별 판정 · 누락 의심 · Word Comment 삽입본 |
| AI 호출 | 1회 (Gate 통과 시). 막히면 0회 | 1회 | 변경별 quick, 필요 시 detail |
| 공통 | 최종 판정은 QA. 근거 없는 항목은 확정하지 않는다 | | |

**세 기능이 같은 문서를 본다.** Knowledge에 등록·수집된 사양서·TC·매뉴얼을 공유하므로,
같은 제품에 대해 서로 다른 결론이 나오면 그 차이를 문서 집합 탓으로 돌릴 수 없다.

---

## 1. 시작 전 준비 — Knowledge

세 기능 모두 Knowledge에 문서가 있어야 동작한다. 없으면 QA Agent는 **G1 Gate에서 막고 AI를
호출하지 않는다.**

### 권장 방식: 제품 지식 폴더 수집

제품마다 최신본을 모아 두는 폴더 하나를 정해 두고, 그 폴더를 읽어서 수집한다.

```text
<제품 폴더>/
  (사양서) VXvue 사양서1(260907).pdf              → 사양서
  (매뉴얼) VXvue Operation Manual.V1.0.11_KO.pdf  → 매뉴얼
  (TC) RA16-148-002_VXvue_TestCase.xlsx           → Test Case
  [QA 작성 규칙] … 가이드_Rev1.12.md               → QA 규칙
  VXvue 업무 자동화 지침 프롬프트.txt               → 지침 프롬프트
```

- **파일명 규약만 지키면 분류·리비전 판별은 자동이다.** 제품별 설정은 폴더 경로 하나뿐이고,
  새 제품을 붙일 때 코드 변경이 필요하지 않다 ([새 제품 추가](PRODUCT_ONBOARDING.md)).
- 같은 문서가 여러 개면 **리비전이 최신인 것**을 고르고, 원본(PDF/DOCX/XLSX)이 사람이
  뽑아둔 `.txt` 추출본보다 우선한다. 제외한 파일과 그 이유가 기록된다.
- 앱은 그 폴더를 **읽기만** 한다. 수집 사본은 `data/product_knowledge/`에 들어가고 Git에
  포함되지 않는다.

운영 서버에 그 폴더가 없으면 화면에서 버튼이 나오지 않는다. 폴더가 있는 PC에서 실행한다.

```bash
python scripts/sync_product_knowledge.py --dry-run
```

```bash
python scripts/sync_product_knowledge.py --product VXvue --report-to http://10.13.0.222:12000
```

자세한 절차·규약·정리 규칙: 앱 안 [`/knowledge/guide`](../app/modules/knowledge/templates/guide.html)

---

## 2. QA Agent — Issue 검증 범위

Issue 하나에서 **관련 사양 → 기존 TC → Regression 범위**를 근거와 함께 정리한다.

### 흐름

```text
Issue 구조화        코드   Polarion backup.json → 표준 구획 분리
지식 문서 로드       코드   등록된 사양서·TC 전체 (파싱 캐시 재사용)
QA 규칙 로드        코드   제품 규칙 문서 → Skill별 절 태깅
근거 검색           코드   Exact(식별자) → BM25(용어 유사도)
Gate G1·G2·G4       코드   ← 막히면 여기서 끝. API 호출 0회
AI 의미 판단        LLM    1회 구조화 호출 (S01/S02/S03/S05 동시)
ID 교차검증         코드   없는 TC ID·근거 ID 제거, 번호 일치 검사
Gate G3·G5          코드   Coverage · 모순 · 근거 없는 확정 검사
```

### 사람이 채우는 것 — 검증 환경

규칙이 **"QA만 아는 사실"**로 정해 둔 값이다. 자동으로 알 수 없고 최종 판정에 영향을 준다.

| 항목 | 비워 두면 |
|---|---|
| 실제 수행 가능 여부 | G4가 확인 요청 (진행 안 됨) |
| Test Data 준비 | G4가 확인 요청 |
| 실제 X-ray Exposure 가능 | 해당 없음으로 처리. `불가`면 실제 촬영 결과를 Expected로 만들지 않음 |
| Dose Table 제공 | 해당 없음으로 처리. `미제공`이면 공식 촬영 가능 조합·출력 정확도를 확정하지 않음 |
| **반드시 필요한 관찰 수단** | 제약 없음. 지정했는데 확보되지 않으면 **G4가 중단** |

정보가 부족한 상태에서도 초안이 필요하면 **"초안으로 작성"**을 체크한다. 미확정 항목이
"확인 필요"로 표시된 초안이 나온다. 단, 초안 허용이 **중단 조건을 넘기지는 않는다.**

### 결과 읽는 순서

1. **상단 Gate 5개** — `BLOCK`이면 진행되지 않았고 이유가 목록으로 나온다.
2. **Issue 탭** — 구조화 결과와 유형. 유형이 "QA 확정 필요"면 후보를 보고 사람이 고른다.
3. **Specification 탭** — 정확 일치 몇 건 + 유사도 몇 건. 근거마다 문서·페이지·절이 붙는다.
4. **TC Coverage 탭** — 판정과 **"Root Cause 검출 가능/불가"**. 이 값이 핵심이다.
5. **Regression 탭** — 8개 축. 해당 없는 축도 "검토했으나 해당 없음"으로 남는다.
6. **Gate 상세 탭** — 교차검증에서 걸러낸 ID, Step–Expected 번호 불일치.
7. **QA Action 탭** — 승인/거절/수정 후 승인/근거 추가 필요를 기록한다.

### 보장하는 것과 보장하지 않는 것

**보장한다**
- 판정마다 근거 위치(문서·페이지·절 / 파일·시트·행)가 붙는다. 없으면 "근거 없음"으로 표시된다.
- 모델이 만든 TC ID·근거 ID는 실제 데이터와 대조해 없는 것은 제외하고, 제외 사실을 남긴다.
- 근거가 없으면 "Root Cause를 덮는다"를 인정하지 않고 confidence를 사람 확인 구간으로 내린다.
- Gate가 막은 이유가 결과에 남는다. 검색 실패를 숨긴 확정 판정을 만들지 않는다.
- 실제로 API에 나간 payload를 원문 그대로 볼 수 있다.

**보장하지 않는다 (QA가 확정한다)**
- Pass/Fail, Issue Close, Release 승인
- TC 파일의 실제 변경 — 판정과 수정안까지만 만든다
- SRS Link 확정
- 이미지 변경 판정 — AI가 판정하지 않는다
- 실제 X-ray Exposure 결과·출력 정확도

자세한 조작 절차: 앱 안 [`/qa-agent/guide`](../app/modules/qa_agent/templates/guide.html) ·
설계: [qa-agent.md](modules/qa-agent.md)

---

## 3. Regression 영향 분석

변경 문서를 등록된 사양서·TC와 대조해 다시 검증할 TC를 추천한다.

- 제품만 고르면 등록된 사양서·TC 전체를 자동 검색한다. 변경 문서는 여러 개 첨부 가능하고,
  문서 없이 요청 텍스트만으로도 분석된다.
- 실제 백엔드 단계 기반 진행 상태(SSE) — 가짜 퍼센트가 없다.
- 산출물: HTML 보고서 + XLSX + 신규 TC 초안(md)
- 분석 상세 화면에서 요청·근거·System Instruction·Gemini 실제 입출력 JSON을 볼 수 있다.
- QA가 정답 TC ID를 확정하면 precision/recall/F1이 집계된다 ([정확도 평가](EVALUATION.md)).

**QA Agent와의 차이**: 이쪽은 Issue가 없는 단계에서 쓴다. 사양 변경이나 릴리스 변경사항
문서를 받았을 때가 대표적이다.

자세한 사용법: 앱 안 `/impact-analyzer/guide` · 설계: [impact-analyzer.md](modules/impact-analyzer.md)

---

## 4. 매뉴얼 개정 검증

연구소가 제출한 개정 매뉴얼이 최신 SRS를 반영했는지 1차 검토하고, 판정을 Word Comment로
삽입해 회신할 수 있게 만든다.

- Track Changes 구조화 → 비기능 변경 필터 → SRS 근거 검색 → Release Note·설계검토보고서
  Scope 대조 → Cross-Manual 영향 추적 → quick/detail 2단계 판정
- **이미지 변경은 AI가 PASS 처리하지 못한다** — 사람이 원본을 확인해야 한다.
- **PDF diff는 confidence 상한 60%** — 레이아웃 해석 오차를 인정하고 QA가 최종 판정한다.
- Round 계보를 추적하고, QA가 확정하기 전에는 이전 지적사항 상태를 자동으로 바꾸지 않는다.

자세한 사용법: 앱 안 `/manual-review/guide` · 설계: [manual-review.md](modules/manual-review.md)

---

## 5. 비용 대시보드

AI 호출 수·토큰·캐시 적중을 기능별로 집계한다. **핵심은 "안 보낸 것"이 보이는 것이다** —
Gate에서 막힌 분석은 호출 0회로 남는다.

| 지표가 나쁠 때 | 확인할 곳 |
|---|---|
| 분석당 입력 토큰이 계속 커짐 | 분석 상세 화면 감사 영역의 입력 JSON 글자 수 |
| 모델 등급이 계속 상위로 올라감 | 결과 화면의 라우팅 이유 |
| 호출 수가 분석 건수보다 많음 | 응답 JSON 잘림으로 인한 재시도 (로그) |

자세한 지표 해석과 절감 기법 12개: 앱 안 [`/cost-dashboard/guide`](../app/modules/cost_dashboard/templates/guide.html) ·
설계: [비용 절감 설계](COST_OPTIMIZATION.md)

---

## 6. 규칙 구현현황 — `/qa-agent/rules`

제품 QA 규칙 문서의 절마다 **어떤 방식으로 구현했는지**와 **Skill별로 규칙이 몇 KB
주입되는지**를 보여준다.

- 구현 방식: 결정적 코드 / 코드+LLM / QA 입력 필수 / 초안까지만 / 런타임 대상 아님
- 규칙 Rev가 올라가 새 절이 생기면 **테스트가 실패**해 재분류를 강제한다.
- "구현됨"이라 적힌 항목은 실제 파일 존재를 테스트가 확인한다.
- Skill별 태깅이 0절이면 결함이 아니라 **그 제품 규칙이 그 주제를 다루지 않는다**는 뜻이다
  (예: Viewer 제품에는 Generator가 없다).

숫자만 빠르게 보려면:

```bash
python scripts/rule_capability_report.py
```

---

## 7. 사내 문서와 외부 전송 — 실측

| 항목 | 값 |
|---|---|
| 등록 문서 전체 (VXvue: 사양 Chunk 737개 + TC 6,407건 + 규칙) | 2.11MB ≈ 883K 토큰 |
| **실제 API 전송량** (Issue 1건 분석) | **30,207자 ≈ 12.1K 토큰** |
| 비율 | **1.37%** |

내부 구성: Issue 구조화 986자 + 검색된 사양 Chunk 6개 6,869자 + TC 후보 40건 16,404자 +
Regression 축 730자 + 규칙 발췌 5,019자.

- **원본 PDF·Excel·DOCX는 서버 밖으로 나가지 않는다.** 파싱·검색·후보 압축이 전부 서버 안에서 끝난다.
- Gemini 호출 직전에 개인정보·사내 경로를 자리표로 바꾼다(`[PATIENT_ID]`, `[IP_ADDRESS]`,
  `[NETWORK_PATH]` 등). QA 판단에 필요한 식별자(SRS/Issue ID, DICOM Tag, Command 번호,
  버전, ErrorCode)는 그대로 둔다.
- 무엇이 마스킹됐는지 **건수만** 기록한다 — 로그에 원본이 남으면 마스킹의 의미가 없다.
- 실제 전송본은 분석 결과 화면의 감사 영역에서 원문 그대로 확인할 수 있다.

취급 규칙: [SECURITY.md](../SECURITY.md)

---

## 8. 자주 막히는 경우

| 증상 | 원인 | 해결 |
|---|---|---|
| QA Agent가 G1 BLOCK | 제품 QA 규칙 문서 미수집 | `/knowledge` → 지금 수집 |
| QA Agent가 G1 BLOCK | 사양서 또는 TC 0건 | `/knowledge`에서 등록 |
| QA Agent가 G2 BLOCK | 사양 근거를 찾지 못함 | Issue에 SRS를 연결하거나 사양서가 최신인지 확인 |
| QA Agent가 G4 NEED_INPUT | 검증 환경 미입력 | 환경 항목 입력 또는 "초안으로 작성" |
| Issue를 찾을 수 없음 | 서버에 Polarion Export 폴더가 없음 | `backup.json` 첨부 |
| 분석이 429로 거절 | 동시 실행 한도(기본 2) 또는 일일 토큰 한도 초과 | 실행 중 작업 완료 후 재시도 / `analysis.daily_token_limit` 조정 |
| 분석 실패 `RESOURCE_EXHAUSTED` | Gemini 선불 크레딧 소진 | AI Studio에서 결제 상태 확인 |
| 분석 실패 `모델 NOT_FOUND` | 상위 등급 모델이 계정에서 미제공 | 기본 모델로 자동 폴백되며 결과에 기록됨. `config.yaml` `models.complex` 조정 |
| 등록 문서를 읽지 못했다는 표시 | 원본 파일이 사라진 등록 | `/knowledge` → 죽은 등록 정리 |
| TC 컬럼 자동 탐지 실패 | 헤더 구조가 다름 | 수동 매핑 화면에서 시트·헤더 행·컬럼 지정 |

운영 중 장애 대응: [운영·백업·모니터링](OPERATIONS.md) ·
실서버 반영 후 확인 항목: [배포 후 테스트](POST_DEPLOY_TESTS.md)
