# QA 검증 관리 시스템

사내 QA 업무 중 **판단은 사람이 하되, 판단에 필요한 자료를 모으는 일은 기계가 하게 만든**
자동화 플랫폼입니다. SW 변경사항을 제품 사양서·Test Case·매뉴얼과 대조해 Regression 검증
대상과 매뉴얼 개정 누락을 찾아내고, 그 근거를 사후에 그대로 열어볼 수 있게 남깁니다.

사용자는 브라우저에서 제품을 고르고 문서를 올리기만 합니다. **AI 채팅창도, Prompt 작성도
필요 없습니다.**

---

## 목차

- [무엇을 해결하는가](#무엇을-해결하는가)
- [왜 외부 AI 웹서비스가 아니라 API로 직접 만들었는가](#왜-외부-ai-웹서비스가-아니라-api로-직접-만들었는가)
- [저장소 구성](#저장소-구성)
- [아키텍처](#아키텍처)
- [기능](#기능)
- [비용 절감 설계](#비용-절감-설계)
- [AI를 어떻게 활용했는가](#ai를-어떻게-활용했는가)
- [Tech Stack](#tech-stack)
- [빠른 시작](#빠른-시작)
- [문서](#문서)

---

## 무엇을 해결하는가

SW 변경이 생겼을 때 QA가 답해야 하는 질문은 늘 같습니다.

> 이번 변경으로 **어디까지** 다시 검증해야 하는가?
> 연구소가 고친 매뉴얼이 **최신 사양을 제대로 반영했는가?**
> 그 매뉴얼의 **최신본이 어느 것인가?**

세 질문 모두 답 자체보다 **답을 찾는 준비 과정**이 오래 걸립니다. 어느 사양서가 최신인지
찾고, 무엇이 실제로 바뀌었는지 리비전을 비교하고, 수백 건의 TC를 훑어야 비로소 판단이
시작됩니다. 그리고 그 과정이 담당자마다 달라서, 같은 변경에 대해 검증 범위가 달라집니다.

이 플랫폼은 그 준비 과정을 결정적인(deterministic) 코드로 옮기고, 의미 판단이 꼭 필요한
마지막 지점에서만 AI를 부릅니다. 그래서 **결과가 흔들리는 범위 자체가 좁습니다.**

| 질문 | 담당 기능 |
|---|---|
| 이 **Issue**를 어디까지 검증해야 하는가 | [QA Agent](docs/modules/qa-agent.md) |
| 이 **변경**으로 어디까지 다시 검증해야 하는가 | [Regression 영향 분석](docs/modules/impact-analyzer.md) |
| 매뉴얼이 최신 사양을 반영했는가 | [매뉴얼 개정 검증](docs/modules/manual-review.md) |
| 매뉴얼의 최신본이 어느 것인가 | [QA Manual Hub](services/qa-manual-hub/README.md) |

세 분석 기능은 **입력이 다릅니다.** 목적이 아니라 손에 있는 자료로 고릅니다 —
Issue가 등록됐으면 QA Agent, 변경 문서를 받았으면 Regression 영향 분석, 개정 매뉴얼을
받았으면 매뉴얼 개정 검증입니다. 셋 다 같은 문서 집합을 보므로 결론이 갈릴 때 그 차이를
자료 탓으로 돌릴 수 없습니다.

---

## 왜 외부 AI 웹서비스가 아니라 API로 직접 만들었는가

이 프로젝트의 출발점이자, 설계 전반을 결정한 제약입니다.

### 1. 사내 문서를 외부 AI 웹서비스에 올릴 수 없다

분석 대상은 제품 사양서, Test Case, 개정 전 매뉴얼입니다. 전부 사내 문서이고, 외부 AI
웹서비스에 업로드하는 것 자체가 허용되지 않습니다. 그래서 처음부터 **문서가 사내 서버 밖으로
파일 형태로 나가지 않는 구조**가 전제였습니다.

- 원본 문서는 사내 서버에 저장되고, 파싱·검색·후보 압축도 전부 서버 안에서 끝납니다.
- 외부로 나가는 것은 **마지막 판단에 필요한 최소한의 구조화된 텍스트 조각**뿐입니다.
  전체 사양서도, 전체 TC도 나가지 않습니다.
- API Key는 `secrets.txt` / `secrets.json` / `.env` 중 한 곳에만 두고 Git·로그·보고서에 절대
  포함하지 않습니다. `/config/status`는 Key의 **설정 여부와 길이, 출처 이름만** 반환하고
  값 자체는 반환하지 않습니다 ([SECURITY.md](SECURITY.md)).
- 무엇이 실제로 전송됐는지는 분석 상세 화면에서 **전송된 입력 JSON 원문 그대로** 확인할 수
  있습니다. 추정이 아니라 실제 payload를 봅니다.

### 2. 반복 업무는 재현 가능해야 한다

채팅창은 매번 사람이 프롬프트를 다르게 씁니다. 같은 변경을 두 사람이 분석하면 다른 답이
나오고, 왜 달랐는지 확인할 방법도 없습니다.

- 프롬프트를 **YAML로 버전 관리**합니다 (`app/prompts/*.yaml`). 누가 돌려도 같은 프롬프트,
  같은 생성 설정입니다.
- 응답은 **JSON Schema로 강제된 Structured Output**입니다. 자유 서술을 사람이 다시 해석하는
  단계가 없습니다.
- 파싱·diff·BM25 검색·후보 압축은 전부 결정적인 Python 코드입니다. AI가 관여하는 구간이
  좁을수록 재현성이 올라갑니다.
- 모델이 반환한 TC ID·근거 Chunk ID가 실제 데이터에 존재하는지 **교차검증**합니다. 존재하지
  않는 ID는 결과에서 제외됩니다.

### 3. 비용은 인원수가 아니라 사용량에 비례해야 한다

유료 구독을 인원수만큼 늘리는 대신, 필요한 지점에서만 API를 호출하고 사용량을 서버에서
통제합니다.

- Rule Engine이 먼저 걸러내므로 **AI 호출 대상 자체가 줄어듭니다.**
- 동일 입력은 SHA-256 캐시로 재사용해 **API 호출이 아예 발생하지 않습니다.**
- 일일 토큰 한도를 넘으면 새 분석 실행 자체를 차단합니다.
- `/cost-dashboard`에서 호출 수·토큰·캐시 적중을 집계해 봅니다.

자세한 내용: [비용 절감 설계](docs/COST_OPTIMIZATION.md)

---

## 저장소 구성

이 저장소 하나에 **배포 단위 두 개**가 들어 있습니다. 어느 쪽에 기능을 붙일지 판단하는
기준은 [공용 아키텍처](docs/SHARED_PLATFORM_ARCHITECTURE.md)에 있습니다.

```text
qa-verification-management-system/
├─ app/                     ① 핵심 앱 — 하나의 FastAPI 프로세스
│  ├─ core/                 공용 인프라 (설정, 저장소, Gemini 클라이언트, 프롬프트 로더, 스케줄러)
│  ├─ prompts/              AI 프롬프트 YAML (버전·생성 설정 포함)
│  ├─ retrieval/            Exact(식별자) → BM25 단계 검색
│  ├─ modules/
│  │  ├─ qa_agent/          Issue 검증 범위 (Skill·Gate)  → /qa-agent
│  │  ├─ impact_analyzer/   Regression 영향 분석          → /impact-analyzer
│  │  ├─ manual_review/     매뉴얼 개정 검증              → /manual-review
│  │  ├─ knowledge/         문서·규칙 관리 (전 기능 공유)  → /knowledge
│  │  └─ cost_dashboard/    AI 사용량 집계                → /cost-dashboard
│  └─ web/                  모듈 라우터를 한 서버에 취합하는 얇은 계층 + 공용 template/static
│
├─ services/                ② 하위 서비스 — 별도 프로세스·별도 DB
│  └─ qa-manual-hub/        매뉴얼 서버                   → /manual-hub
│     ├─ backend/           FastAPI + PostgreSQL
│     ├─ frontend/          React 19 + Vite
│     └─ deploy/            자체 설치·배포·백업 스크립트
│
├─ deploy/nginx/            두 배포 단위를 하나의 origin으로 묶는 nginx 설정
├─ docs/                    설계·운영 문서 (docs/README.md 가 지도)
├─ knowledge/               AI 에이전트용 Knowledge (Akela)
├─ config/products/         제품별 설정
└─ tests/                   핵심 앱 테스트
```

**①과 ②는 코드를 공유하지 않습니다.** 서로를 import하지 않고, 같은 DB를 읽지 않습니다.
연결은 홈 화면의 링크와 nginx 라우팅뿐입니다. 스택이 근본적으로 다르기 때문에(Jinja2 + SQLite
vs React SPA + PostgreSQL) 억지로 한 프로세스에 넣지 않고, 대신 **저장소·이력·CI·문서를
하나로** 관리합니다.

### 왜 저장소를 합쳤는가

두 시스템은 같은 사람이, 같은 팀을 위해, 같은 서버에 운영합니다. 저장소가 나뉘어 있으면
"매뉴얼 개정 검증이 참조하는 매뉴얼"과 "매뉴얼을 보관하는 서버"의 변경이 서로 다른 이력에
쌓여, 어느 시점의 조합이 실제로 돌아갔는지 알 수 없게 됩니다. 병합은 `git subtree` 방식으로
수행해 **원래 저장소의 커밋 이력을 그대로 보존**했습니다.

---

## 아키텍처

### 실행 구조

```text
                        브라우저
                           │
                      nginx :80
        ┌──────────────────┴──────────────────┐
        │ /                                   │ /manual-hub/
        ▼                                     ▼
  핵심 앱 (uvicorn :12000)              Manual Hub SPA (정적 파일)
  FastAPI + Jinja2                      + 백엔드 (uvicorn :9180)
        │                                     │
        ▼                                     ▼
     SQLite                              PostgreSQL 16
  + 파일 저장소                          + 문서 저장소
        │
        ▼
  Gemini API  ← 마지막 판단에만, 최소 입력으로
```

### 분석 파이프라인

```text
변경 문서 → Rule 기반 Change 추출(기준 사양서 diff) → BM25 Specification 검색
   → TC Candidate 선정 → Gemini Semantic Decision(Structured Output, 1회 호출)
   → TC ID / Chunk ID 교차검증 → HTML Report + XLSX + 신규 TC 초안(md)
```

Gemini가 등장하는 곳은 한 군데뿐이고, 나머지는 전부 결정적인 Python 코드입니다.

---

## 기능

### QA Agent — `/qa-agent`

Polarion Issue 하나에서 **관련 사양 → 기존 TC → Regression 범위**를 근거와 함께 정리합니다.
AI가 QA를 판정하지 않습니다 — QA가 반복 수행하는 조사·비교·추적 절차를 표준화합니다.

```text
Issue 구조화 → 지식 로드 → QA 규칙 로드 → Exact→BM25 검색
   → Gate G1·G2·G4 ── 막히면 여기서 끝 (API 호출 0회)
   → Gemini 1회 (Issue 분석 · 사양 관련도 · TC Coverage · Regression 동시)
   → ID 교차검증 → Gate G3·G5 → Human Review 6탭 → QA 승인
```

- **Gate를 프롬프트가 아니라 코드로 판정**합니다. 근거가 부족한 Issue는 API 호출이 **0회**이고,
  왜 멈췄는지가 결과에 남습니다. "근거가 충분한가"는 세면 되는 문제라 LLM에 물을 이유가 없고,
  물으면 같은 입력에 다른 답이 나옵니다.
- **Issue 유형을 LLM에 묻지 않습니다.** Polarion 연구소 검토 결과가 QA 규칙의 유형 분류와
  대응해, 실측 38건 중 33건이 코드로 분류됩니다. 갈리는 값은 확정하지 않고 후보만 남깁니다.
- **판정마다 근거 위치**가 붙습니다 (문서·페이지·절 / 파일·시트·행). 근거 없는 판정은
  "근거 없음"으로 드러나고 confidence가 사람 확인 구간으로 내려갑니다.
- **모델이 만든 ID를 믿지 않습니다.** 실제 데이터에 없는 TC ID·근거 ID를 제외하고,
  제외했다는 사실을 화면에 남깁니다.
- **QA 규칙 문서를 매 호출에 통째로 보내지 않습니다.** 절 단위로 쪼개 Skill별로 태깅하고
  해당 절만 주입합니다 — 전문 30.7KB → **2~5KB (85~94% 절감)**.
- QA 규칙이 **자동 확정을 금지한 8가지**(Issue 자동 Close, QA 승인 없는 TC 덮어쓰기 등)는
  코드 경로 자체가 없습니다.

→ [상세 문서](docs/modules/qa-agent.md) · [구조 분석](docs/QA_AGENT_ARCHITECTURE.md) ·
사용법: 앱 안 `/qa-agent/guide`

### Regression 영향 분석 — `/impact-analyzer`

제품만 선택하면 등록된 사양서·TC 전체를 자동 검색해 분석합니다. 변경 문서는 여러 개 동시
첨부할 수 있고, 문서 없이 요청 텍스트만으로도 분석됩니다.

- PDF / Word(`.docx`) 사양서, 다중 시트 TC Excel 자동 파싱
- 실제 백엔드 단계 기반 실시간 진행 상태(SSE) — 가짜 퍼센트 없음
- 사용자 관점으로 재구성한 HTML 보고서 + XLSX + 신규 TC 초안(md)
- TC ID·Chunk ID 교차검증, Confidence 기반 Manual Review 분류
- 분석 상세 감사 화면 (요청 / 근거 / System Instruction / Gemini 실제 입출력 JSON)

→ [상세 문서](docs/modules/impact-analyzer.md)

### 매뉴얼 개정 검증 — `/manual-review`

연구소가 제출한 Word Track Changes 개정 매뉴얼이 최신 SRS를 반영했는지 1차 검토하고, 판정
결과를 Word Comment로 삽입해 회신할 수 있게 만듭니다.

- Track Changes 구조화 추출 → 비기능 변경 필터 → SRS 근거 BM25 검색 → Release Note·설계검토
  보고서 Scope 대조 → Cross-Manual 영향 추적 → quick / detail 2단계 AI 판정
- **이미지 변경은 AI가 PASS 처리하지 못하게 막고** 사람이 원본을 확인하도록 강제
- **PDF diff는 confidence 상한 60%** — 레이아웃 해석 오차를 인정하고 QA가 최종 판정
- Round 계보 추적. QA가 확정하기 전에는 이전 지적사항 상태를 자동 변경하지 않음

→ [상세 문서](docs/modules/manual-review.md)

### 지식 관리 — `/knowledge`

세 분석 기능이 함께 쓰는 제품별 사양서·TC·매뉴얼·QA 규칙을 관리합니다. 파일을 하나씩
올리는 대신 **제품마다 최신본을 모아 두는 폴더 하나를 읽어 수집**합니다.

- 분류·리비전 판별이 **파일명 규약**으로 자동 처리됩니다. 제품별 설정은 폴더 경로뿐이고
  새 제품을 붙일 때 코드 변경이 필요 없습니다 ([새 제품 추가](docs/PRODUCT_ONBOARDING.md)).
- 같은 문서의 구버전, 바이트까지 같은 중복, 원본이 사라진 죽은 등록을 자동 정리하고
  **확실하지 않은 것은 지우지 않고 보고**합니다.
- 사람이 미리 뽑아둔 `.txt` 추출본이 원본 PDF보다 오래된 사례가 실제로 있어, 원본 형식을
  우선하고 제외 이유를 남깁니다.
- VXvue 최신 사양서는 별도 자동화(Polarion 연동)와도 연계됩니다 ([AUTOMATION.md](docs/AUTOMATION.md)).

→ 사용법: 앱 안 `/knowledge/guide`

### 매뉴얼 서버 — `/manual-hub` (하위 서비스)

제품 매뉴얼과 기술문서를 한 서버에 모아 **Revision 이력을 삭제 없이 보존**하는 문서관리
시스템입니다. Git이 소스 커밋 이력을 관리하듯 문서의 개정 이력을 관리합니다. 새 Revision을
올려도 기존 파일을 덮어쓰거나 지우지 않습니다.

- 제품 → 문서 → 버전 → 파일 4계층. Revision 형식을 강제하지 않음 (문서에 적힌 그대로)
- 업로더는 입력받지 않고 로그인 계정에서 자동 기록. 표시 이름은 업로드 당시 값을 스냅샷 보존
- Archive(Soft delete)만 있고 Hard delete 없음. 감사 로그는 append-only (UPDATE / DELETE 경로 없음)
- Argon2id + 서버 세션, 업로드 매직 넘버 검사, UUID 저장 경로

→ [상세 문서](services/qa-manual-hub/README.md)

---

## 비용 절감 설계

원문을 통째로 LLM에 넘기면 정확도는 높아 보여도 분석 1건마다 비용이 선형으로 늘어나 사내
서비스로 지속 운영할 수 없습니다. 원칙은 하나입니다.

> **판단이 꼭 필요한 지점에서만, 최소한의 근거로 LLM을 부른다.**

| # | 기법 | 효과 |
|---|---|---|
| 1 | **Gate가 API 호출 전에 차단** | 근거가 부족한 Issue는 **호출 0회**. 왜 멈췄는지가 결과에 남음 |
| 2 | Rule Engine 사전 필터 | 비기능 변경은 AI 대상에서 제외 |
| 3 | 기준 사양서 Diff | 진짜 바뀐 줄만 분석 |
| 4 | Exact → BM25 Top-K 후보 압축 | 전체 사양서·TC를 보내지 않음 |
| 5 | 정확 일치 확보 시 후보 축소 | 확실한 근거가 있으면 유사도 후보 수를 줄임 |
| 6 | **QA 규칙 발췌 주입** | 규칙 전문 30.7KB → 해당 Skill 절만 2~5KB (**85~94% 절감**) |
| 7 | 변경 문서 관련 줄 축소 | 요청과 무관한 줄 제외 |
| 8 | Structured Output 단일 호출 | 재질의 왕복 없음. 네 Skill을 한 번에 판단 |
| 9 | quick / detail 2단계 + PASS short-circuit | 문제없으면 비싼 호출 생략 |
| 10 | SHA-256 응답 캐시 | 동일 입력은 API 호출 자체가 없음 |
| 11 | `thinking_budget=0` | 내부 추론 토큰 소비 차단 |
| 12 | 모델 등급 라우팅 | 기본은 Flash. 근거가 흩어지거나 Root Cause가 불명확할 때만 상위 등급 |
| 13 | 일일 토큰 한도 + 감사 기록 | 초과 시 실행 차단, 사후 검증 가능 |

### 실측 — 무엇이 실제로 나가는가

VXvue Issue 1건 분석 기준입니다.

| 항목 | 크기 |
|---|---|
| 등록 문서 전체 (사양 Chunk 737개 + TC 6,407건 + QA 규칙) | **2.11MB ≈ 883K 토큰** |
| **실제 API 전송량** | **30,207자 ≈ 12.1K 토큰** |
| 비율 | **1.37%** |
| AI 호출 | **1회** |

내부 구성: Issue 구조화 986자 + 검색된 사양 Chunk 6개 6,869자 + TC 후보 40건 16,404자 +
Regression 축 730자 + QA 규칙 발췌 5,019자.

**원본 PDF·Excel·DOCX는 서버 밖으로 나가지 않습니다.** 파싱·검색·후보 압축이 전부 서버
안에서 끝나고, 나가는 것은 검색으로 추린 발췌뿐입니다.

호출 직전에 개인정보·사내 경로를 자리표로 바꿉니다 (`[PATIENT_ID]`, `[IP_ADDRESS]`,
`[NETWORK_PATH]` 등). 이때 **마스킹하면 안 되는 것이 더 위험**합니다 — 버전이나 ErrorCode가
가려지면 분석 자체가 불가능해지므로, SRS/Issue ID·DICOM Tag·Command 번호·버전은 보호합니다.
`1.1.0.001`(버전)과 `10.13.0.222`(IP)는 모양이 같아서, 0으로 채운 자리가 있으면 버전으로
판별합니다.

무엇이 마스킹됐는지는 **건수만** 기록합니다 — 로그에 원본이 남으면 마스킹의 의미가 없습니다.
실제 전송본은 분석 결과 화면의 감사 영역에서 원문 그대로 확인할 수 있습니다.

→ [단계별 상세와 관련 코드](docs/COST_OPTIMIZATION.md)

---

## AI를 어떻게 활용했는가

**런타임에서의 AI 활용**과 **개발 과정에서의 AI 활용**이 다릅니다.

### 개발: 저장소에 기준을 두고, 에이전트에게는 그 일부만 준다

구현 자체를 AI 에이전트(Claude Code)와 함께 진행했습니다. 여기서 실제로 문제가 되는 것은
모델 성능이 아니라 **컨텍스트**입니다. 매 작업마다 저장소 문서를 통째로 넣으면 토큰이
빠르게 소모되고, 무관한 문서가 판단을 흐려 엉뚱한 파일을 고칩니다.

그래서 두 가지를 같이 했습니다. 하나는 **폴더에 기준을 만드는 것**, 다른 하나는 그 기준을
이용해 **작업에 필요한 지식만 골라 주입하는 것**입니다. 앞의 것이 없으면 뒤의 것이 불가능합니다.

#### 1. 폴더를 일정한 기준으로 관리한다

"어디에 무엇이 있는지"가 규칙으로 정해져 있어야 에이전트가 전부 읽지 않고도 필요한 곳을
찾습니다. 이 저장소에서 지키는 기준은 다음과 같습니다.

| 기준 | 내용 |
|---|---|
| 배포 단위 | `app/`(핵심 앱)과 `services/*`(하위 서비스) 둘뿐. 어느 쪽에 붙일지 먼저 정한다 |
| 기능 경계 | 기능은 `app/modules/<name>/`이 라우터·서비스·템플릿·테스트를 전부 소유. `app/web/`은 URL prefix만 결정하고 로직을 갖지 않는다 |
| 문서 | 목적별로 나누고 `docs/README.md`가 지도 역할. 사용법은 문서가 아니라 앱 화면 안에 둔다 |
| 지식 | 하위 서비스도 `knowledge/`를 따로 만들지 않고 루트에 `<name>-*.md`로 모은다 |
| 테스트 | 루트 `pytest`는 `testpaths`로 핵심 앱만 수집. 하위 서비스는 자기 CI에서 |
| 프롬프트 | `app/prompts/*.yaml` 한 곳에서 버전과 생성 설정까지 관리 |

규칙은 사람이 기억하는 대신 [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md)와
[공용 아키텍처](docs/SHARED_PLATFORM_ARCHITECTURE.md)에 적어 두고, 에이전트가 매 작업 전에
읽습니다.

#### 2. Akela — 작업 종류별로 지식을 잘라 주입한다

[Akela](https://github.com/TimothyHan/akela)는 저장소의 지식 문서를 **섹션 단위로 쪼개고,
각 섹션에 "어떤 작업에 필요한지(scope)"와 "얼마나 중요한지(tier)"를 태깅**해 두었다가, 작업
종류에 맞는 부분만 뽑아 컨텍스트로 주는 도구입니다. 런타임 의존성이 아니어서 앱 실행·배포
동작에는 전혀 영향을 주지 않습니다.

```text
knowledge/*.md            에이전트용 지식. 섹션마다 scope·tier 태그
  ↓  akela compile --activity <activity>
작업별 slice.md           그 작업에 필요한 섹션만 (나머지는 dropped 로 기록)
  ↓  에이전트 작업
akela log applied / contradicted     근거로 쓴 규칙 · 결과가 뒤집은 규칙을 기록
  ↓  akela stats / curate
지식 유지보수             안 쓰이는 규칙은 좁히거나 버리고, 틀린 규칙은 고친다
```

**이 프로젝트에서 실제로 어떤 이득이 있었나**

지식 베이스는 14개 파일 · 100개 섹션 · 약 69KB입니다. 이걸 매 작업마다 통째로 넣는 대신,
작업 종류에 따라 이만큼만 들어갑니다.

| 작업 종류 | 주입되는 섹션 | slice 크기 |
|---|---|---|
| `testing` | 8 | 3.9KB |
| `documentation` | 8 | 4.6KB |
| `web-ui` | 9 | 4.7KB |
| `manual-hub-ui` | 7 | 4.9KB |
| `manual-hub-backup` | 8 | 5.9KB |
| `manual-hub-auth` | 11 | 7.2KB |
| `deployment` | 11 | 7.7KB |
| `product-knowledge` | 12 | 8.5KB |
| `manual-hub-deploy` | 12 | 9.4KB |
| `qa-agent-dev` | 15 | 11.4KB |
| `manual-hub-dev` | 15 | 14.2KB |
| `core-development` | 25 | 15.1KB |

- **저장소가 커져도 각 작업의 컨텍스트는 그만큼 커지지 않습니다.** QA Manual Hub를 병합하며
  지식이 25KB 늘었지만, 회귀 분석 코드 작업에는 그 25KB가 한 줄도 들어가지 않습니다.
  병합할 때 하위 폴더에 지식을 그대로 두지 않고 루트로 옮겨 activity 단위로 태깅한
  결과입니다.
- **범위는 한 번 정하고 끝내는 게 아니라 재조정합니다.** 처음에는 매뉴얼 서버 지식 31개
  섹션을 `manual-hub` 하나로 묶었는데, 그러면 백업 절차 하나를 고치는 작업에도 인증 설계와
  프론트엔드 구조가 전부 따라 들어와 slice가 27KB였습니다. 실제 작업 단위 5개(백엔드 /
  인증 / 프론트엔드 / 배포 / 백업)로 쪼개 **5.0~14KB**로 줄였습니다. 한 섹션이 여러 작업에
  필요하면 `scope`에 쉼표로 여러 activity를 줍니다.
- **태깅이 잘못되면 지식이 아무 작업에도 전달되지 않는다는 것도 측정으로 확인했습니다.**
  `scope=all` + `tier=should`인 섹션 4개가 **30번의 컴파일에서 100% `general-scope`로
  버려지고 있었습니다.** 로그를 보고 나서야 알았고, activity 단위로 범위를 좁혀 되살렸습니다.
  설정해 두는 것과 실제로 전달되는 것은 다릅니다.
- 지금까지 31개 작업이 기록됐고, 근거로 사용된 규칙 56건과 결과가 반박한 규칙 1건이 로그에
  남아 있습니다(`akela/learnings-log.jsonl`). 반박된 1건은 "제품·버전당 최신 문서 1개만
  검색 대상으로 삼는다"는 잘못된 규칙이었고, 지금은 고쳐진 내용이 지식에 들어가 있습니다.
- `tier=must`(40) / `tier=should`(38)로 나눠 두어, 컨텍스트가 빠듯할 때 무엇을 먼저 버릴지가
  이미 정해져 있습니다.

구성 파일은 `akela.json`(activity 목록), `akela/PROTOCOL.md`(작업 절차),
`akela/CURATE.md`(지식 유지보수 절차)입니다. 자세한 동작은
[Context Engineering](docs/CONTEXT_ENGINEERING.md)에 정리했습니다.

### 프롬프트를 코드처럼 관리한다

프롬프트는 채팅 기록이 아니라 **저장소에 있는 버전 관리 대상**입니다.

- `app/prompts/*.yaml` — 프롬프트 본문, 버전, `thinking_budget` 같은 생성 설정을 한 파일에
- 캐시 키가 `sha256(model + prompt명 + prompt버전 + prompt내용)`이므로, 프롬프트를 고치면
  **캐시가 자동으로 무효화**됩니다. 프롬프트 변경과 결과 변경이 어긋나지 않습니다.
- 어떤 프롬프트로 무엇을 보내 무엇을 받았는지가 분석 상세 화면에 원문 그대로 남습니다.

### AI의 판단을 무조건 믿지 않는 설계

이 프로젝트에서 가장 신경 쓴 부분입니다. AI를 정답을 주는 존재가 아니라 **1차 후보를 좁혀
주는 존재**로 두고, 틀릴 수 있는 지점마다 사람이 개입할 자리를 만들었습니다.

| 위험 | 대응 |
|---|---|
| 존재하지 않는 TC·근거를 만들어냄 | TC ID·Chunk ID를 실제 데이터와 교차검증, 없으면 결과에서 제외 |
| 애매한 판정을 확신처럼 제시 | Confidence 기준으로 추천 / Manual Review를 나눔 |
| 이미지 변경을 텍스트만 보고 PASS | `IMAGE_CHANGE_REVIEW_REQUIRED` 강제 표시, 사람이 원본 확인 |
| PDF 레이아웃 해석 오차 | confidence 상한 60%, PDF에는 Comment 생성 안 함, QA가 최종 판정 |
| 다른 매뉴얼 영향을 임의 확정 | `REVIEW_REQUIRED` 후보로만 표시, QA가 직접 확정 |
| 이전 지적사항 상태를 임의 변경 | QA가 확정하기 전까지 자동 변경 없음 |
| 판단 근거를 알 수 없음 | 입력 JSON·원본 응답·BM25 후보 순위와 점수를 그대로 열람 |

Unit Test는 Gemini Mock Response를 사용하므로 테스트에 API 비용이 발생하지 않습니다.

---

## Tech Stack

| 계층 | 핵심 앱 | QA Manual Hub |
|---|---|---|
| Frontend | Jinja2 + 바닐라 JS | React 19, TypeScript, Vite 6 |
| Backend | FastAPI, uvicorn | FastAPI, uvicorn |
| DB | SQLite (WAL) | PostgreSQL 16, SQLAlchemy 2.0, Alembic |
| 문서 처리 | PyMuPDF, openpyxl, python-docx | — |
| 검색 | rank-bm25 | PostgreSQL ILIKE |
| AI | Google Gemini (Structured Output) | — |
| 인증 | 사내망 전용 | Argon2id + 서버 세션 |
| 테스트 | pytest | pytest (실제 PostgreSQL 필요) |
| 배포 | uvicorn / systemd | systemd + rsync 또는 Docker Compose |

공통: Python 3.12, nginx, GitHub Actions

---

## 빠른 시작

### 핵심 앱

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item secrets.example.txt secrets.txt -Force   # GEMINI_API_KEY 입력
.\scripts\run.ps1                                   # http://localhost:12000
```

```bash
./scripts/run.sh    # Linux / macOS
```

띄운 뒤 `/impact-analyzer/guide`, `/manual-review/guide`에서 각 기능의 사용법을 볼 수 있습니다.

### 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

루트 `pytest`는 핵심 앱 테스트만 수집합니다 (`pytest.ini`의 `testpaths`). 하위 서비스는 자체
런타임이 필요하므로 자기 디렉터리에서 따로 실행합니다.

```bash
cd services/qa-manual-hub/backend
export TEST_DATABASE_URL="postgresql+psycopg://user:pass@127.0.0.1:5432/qa_manual_hub_test"
pytest tests -q
```

### 매뉴얼 서버까지 함께 띄우기

홈 화면의 "매뉴얼 서버" 카드는 `config.yaml`의 `services.manual_hub.url`을 엽니다. 기본값은
같은 서버 nginx가 `/manual-hub/`로 프록시하는 통합 배포 기준입니다. 절차는
[배포 가이드](docs/DEPLOYMENT.md) §8에 있습니다. URL을 빈 문자열로 두면 카드가 표시되지
않으므로, 매뉴얼 서버 없이도 핵심 앱만 그대로 운영할 수 있습니다.

---

## 문서

읽을 문서를 목적별로 고르려면 **[문서 지도](docs/README.md)**에서 시작하세요.

| 문서 | 내용 |
|---|---|
| [docs/README.md](docs/README.md) | 문서 지도 — 목적별 안내 |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 기능별 사용 안내 — 어느 기능을 언제 쓰는지, 무엇을 보장하는지 |
| [docs/QA_AGENT_ARCHITECTURE.md](docs/QA_AGENT_ARCHITECTURE.md) | 구조 분석 · 목표 아키텍처 · RAG/DB/Metadata/Skill/Routing/Security/Audit |
| [docs/PRODUCT_ONBOARDING.md](docs/PRODUCT_ONBOARDING.md) | 새 제품 추가 — 코드 변경 없이 편입하는 방법 |
| [docs/POST_DEPLOY_TESTS.md](docs/POST_DEPLOY_TESTS.md) | 실서버 반영 후 확인 항목 |
| [docs/SHARED_PLATFORM_ARCHITECTURE.md](docs/SHARED_PLATFORM_ARCHITECTURE.md) | 두 가지 확장 방식과 경계, 새 기능 추가 체크리스트 |
| [docs/COST_OPTIMIZATION.md](docs/COST_OPTIMIZATION.md) | 비용 절감 파이프라인 9단계 |
| [docs/CONTEXT_ENGINEERING.md](docs/CONTEXT_ENGINEERING.md) | 저장소 관리 기준과 Akela — 에이전트 컨텍스트 토큰 절감 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 로컬 설치 · 서버 배포 · 하위 서비스 통합 |
| [docs/SERVERS.md](docs/SERVERS.md) | 운영 서버에 떠 있는 3개 서버(핵심 앱/Manual Hub/nginx) 각각의 역할과 필수 명령 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 작업 복구 · 백업 · 모니터링 |
| [docs/EVALUATION.md](docs/EVALUATION.md) | 추천 정확도 평가 |
| [docs/AUTOMATION.md](docs/AUTOMATION.md) | 진행 상태 · 사양서 자동 동기화 |
| [SECURITY.md](SECURITY.md) | 비밀정보 취급 규칙 |
| [NEXT_STEPS.md](NEXT_STEPS.md) · [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | 남은 작업 · 결정 대기 항목 |
