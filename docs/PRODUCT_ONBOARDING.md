# 새 제품 추가

> 상위: [문서 지도](README.md) · 사용 안내: [USER_GUIDE](USER_GUIDE.md)

**코드 변경 없이 YAML 한 장으로 끝난다.** 제품 간 공통 규약은 파일명이고, 제품별로 다른
것은 지식 폴더 경로뿐이다.

VXvue와 Bellalun Viewer가 이미 같은 파일명 규약을 쓰고 있어 그것을 기준으로 삼았다.

---

## 1. 3단계

### ① 제품 등록

`/knowledge` 화면의 **새 제품 추가**에 제품명을 넣는다. Regression 분석·매뉴얼 개정 검증·
QA Agent의 제품 목록에 함께 표시된다.

### ② 설정 파일 작성

`config/products/<slug>.yaml`을 만든다. `<slug>`는 제품명을 소문자·하이픈으로 바꾼 것이다
(`Bellalun Viewer` → `bellalun-viewer`).

```yaml
product: Acme Viewer
version: "1.0"
manual_types:
  - "Acme Viewer Operation Manual"
  - "Acme Viewer Service Manual"

# 이 제품의 사양서·매뉴얼·TC·QA 규칙 최신본을 모아 두는 폴더. 앱은 읽기만 하고 쓰지 않는다.
# 담당자 PC는 로컬 경로, 서버는 마운트 지점으로 본다 — 기본값은 PC 경로로 두고
# 서버에서만 환경변수로 덮어쓴다 (§6).
knowledge_source:
  dir: "${QA_KNOWLEDGE_DIR_ACME:-C:/Users/<계정>/Documents/자동화/Acme Viewer/지식}"

specification:
  source: manual        # ALM 크롤러 연동이 있으면 alm_crawler
testcase:
  source: manual

# Polarion Issue Export 폴더가 있으면 채운다. 없으면 QA Agent 화면에서 JSON을 업로드한다.
issue_source:
  source: manual

sync:
  day_of_week: "mon"
  schedule_time: "08:00"
```

### ③ 수집

지식 폴더가 있는 **담당자 PC에서** 실행한다. 먼저 무엇이 수집될지 확인하고,

```bash
python scripts/sync_product_knowledge.py --product "Acme Viewer" --dry-run
```

맞으면 운영 서버로 올린다 (서버는 이 폴더를 볼 수 없다 — §6).

```bash
python scripts/sync_product_knowledge.py --product "Acme Viewer" --upload-to http://10.13.0.222:12000
```

바뀐 파일만 전송한다 (sha256 비교). VXvue 실측으로 첫 회 106MB, 이후 변경 없으면 0MB다.
서버가 폴더를 직접 볼 수 있는 환경이라면 `/knowledge` 화면의 **"지금 수집"** 버튼으로도
같은 일을 할 수 있다 — 볼 수 없으면 버튼이 숨는다.

---

## 2. 파일명 규약 — 이것만 지키면 분류는 자동

| 접두사 / 패턴 | 분류 | `documents` 등록 |
|---|---|---|
| `(사양서)`, `*SRS 사양서*` | specification | O |
| `(매뉴얼)`, `System Integration Guide*`, `*Conformance Statement*`, `*Operation Manual*`, `*Service Manual*`, `*API Protocol Manual*` | manual | O |
| `(TC)`, `*Test Case*`, `*TestCase*`, `*Checklist*` | testcase | O |
| `[QA 작성 규칙]` | qa_rules | X (규칙 로더가 읽음) |
| `*지침 프롬프트*` | instruction_prompt | X (규칙 로더가 읽음) |

`(TC)` 접두사가 있으면 `*Checklist*` 패턴보다 먼저 testcase로 확정된다 — 분류 우선순위가
정해져 있다.

규약에 맞지 않는 파일은 **조용히 버리지 않고** "분류되지 않음"으로 보고한다.
`/knowledge/source/<제품>`에서 무엇이 왜 빠졌는지 확인할 수 있다.

### 규약이 다른 제품

`classify`로 덮어쓴다. 기본 규약과 합쳐지지 않고 **대체**된다.

```yaml
knowledge_source:
  dir: "..."
  classify:
    specification: ["사양_*", "SPEC_*"]
    testcase: ["TC_*"]
```

### 수집하지 않을 파일

```yaml
knowledge_source:
  dir: "..."
  ignore:
    - "[자동화*"      # Bellalun Viewer 의 자동화 프로젝트 운영 문서
    - "회의록*"
```

Office 임시 파일(`~$*`)과 `*.tmp`는 기본으로 제외된다.

---

## 3. 리비전 판별

파일명에서 읽는다. **`documents.revision` 컬럼은 등록 순번(`Rev.1`, `Rev.2`)이라 문서
리비전이 아니다** — 그 값으로 비교하면 `260831`이 `260907`보다 최신으로 판정된다.

| 표기 | 종류 | 예 |
|---|---|---|
| `(260907)` | 날짜 | `(사양서) VXvue 사양서1(260907).pdf` |
| `V1.0.11`, `V1.0.12W1` | 버전 | `(매뉴얼) VXvue Operation Manual.V1.0.11_KO.pdf` |
| `Rev1.12` | Rev | `[QA 작성 규칙] … 가이드_Rev1.12.md` |

리비전이 아닌 것:

- `RA16-148-002`, `R-20-643`, `R-23-2346` — 사내 **문서번호**
- `_KO` / `_EN` — 언어. 같은 문서의 한국어·영어본은 **서로 다른 문서**로 본다
- `_확인완료`, `_수정_확인완료`, `(개정)` — 진행 상태 꼬리표. 문서 식별에서 제외한다

리비전 표기 방식이 서로 다르면(날짜 vs 버전) **비교하지 않는다.** 확실할 때만 판정한다.

---

## 4. 같은 문서가 여러 개일 때

`logical_id`(종류 + 리비전·언어·꼬리표를 뺀 이름 + 언어)가 같으면 같은 논리 문서다.
하나만 고르고 나머지는 제외 이유와 함께 기록한다.

우선순위: ① 리비전이 더 최신 ② 원본 형식(PDF/DOCX/XLSX) > MD > TXT ③ 파일 수정시각

> **원본이 추출본보다 우선하는 이유.** 사람이 미리 뽑아둔 `.txt`가 원본보다 오래된 사례가
> 실제로 있었다 — `VXvue 사양서1(260824).txt` vs `(사양서) VXvue 사양서1(260907).pdf`.
> 추출본은 갱신을 잊기 쉽다.

### 구버전 정리 규칙

```text
같은 문서의 리비전이 확실히 더 오래됨   → 자동 정리
바이트까지 같은 파일이 두 번 등록됨      → 자동 정리
원본 파일이 사라진 등록                → 자동 정리
종류가 어긋나게 등록됨 (매뉴얼→사양서)  → 지우지 않고 보고
리비전 비교 불가                      → 지우지 않고 보고
```

"확실할 때만 지운다"가 원칙이다. 사람이 다른 기준으로 올려둔 문서를 자동으로 없애면 그
판단 근거가 사라진다.

---

## 5. QA 규칙 문서

제품마다 두 개를 유지한다. **없으면 QA Agent가 G1에서 막고 AI를 호출하지 않는다** —
규칙 없이 낸 판정은 담당자마다 달라지고 근거를 되짚을 수 없기 때문이다.

| 문서 | 내용 |
|---|---|
| `<제품> 검증 DB AI 지침 프롬프트` | 역할·Skill Routing·Gate·정보 우선순위 |
| `[QA 작성 규칙] <제품> … 가이드_Rev*.md` | 세부 실행 절차 |

### 제품마다 규칙 범위가 다르다 — 결함이 아니다

`/qa-agent/rules`에서 Skill별 태깅 절 수를 볼 수 있다. 0절이면 **그 제품 규칙이 그 주제를
다루지 않는다**는 뜻이다.

| | VXvue Rev1.12 | Bellalun Viewer |
|---|---|---|
| 절 수 / 전문 | 140절 / 30.7KB | 82절 / 23.2KB |
| S03 TC Coverage | 12절 | 40절 (TC 설계 중심 문서) |
| S09 Generator | 11절 | **0절** — Viewer 제품에 Generator가 없다 |
| S04 / S06 / S10 | 6 / 8 / 2절 | **0절** |

Skill 태깅은 제품 고유 용어가 아니라 **QA 공통 용어**로 매칭한다. 비교 시 공백을 지우므로
`자체검토`/`자체 검토`처럼 표기가 갈려도 같게 잡힌다. 하위 절은 부모 절의 태그를 물려받는다.

---

## 6. 서버는 이 폴더를 볼 수 없다

**운영 방식은 업로드다.** 서버가 담당자 PC 폴더를 CIFS 로 마운트하는 방법도 검토했고
포트(445)도 열려 있었지만, 담당자 PC 가 Wi-Fi DHCP 라 IP 가 바뀌고 사내 DNS 에 이름이
없으며(서버에서 `getent hosts` 실패) 외근 중엔 네트워크에 아예 없다. 마운트였다면 그 주
수집이 통째로 비었을 것이다. 그래서 폴더를 볼 수 있는 쪽이 밀어 올린다 — ALM 크롤러
동기화와 같은 방향이다. 프로토콜과 근거는 `app/core/knowledge_upload.py` 에 있다.

담당자 PC 에서 주 1회 자동 실행하려면 작업 스케줄러에 등록한다.

```
schtasks /Create /TN "QA_ProductKnowledge_Sync" /SC WEEKLY /D MON /ST 07:45 ^
  /TR "C:\path\to\.venv\Scripts\python.exe C:\path\to\scripts\sync_product_knowledge.py --upload-to http://10.13.0.222:12000"
```

### 그래도 서버가 폴더를 볼 수 있는 환경이라면

공용 네트워크 드라이브에 지식 폴더를 두는 경우처럼 서버가 직접 읽을 수 있다면, 같은
폴더를 담당자 PC 는 로컬 경로로, 운영 서버는 마운트 지점으로 본다. `${ENV:-기본값}` 을
쓰면 **YAML 에 PC 경로를 기본값으로 두고 서버에서만 환경변수로 덮어쓸** 수 있다 — 양쪽
모두 환경변수를 설정할 필요가 없다.

```yaml
knowledge_source:
  dir: "${QA_KNOWLEDGE_DIR_ACME:-C:/Users/<계정>/Documents/자동화/Acme Viewer/지식}"
```

서버에서는 systemd 유닛에 넣는다 (마운트 절차는 [배포 후 테스트 §2](POST_DEPLOY_TESTS.md#2-제품-지식-폴더-수집--서버에서-되는지가-관건)).

```ini
[Service]
Environment=QA_KNOWLEDGE_DIR_ACME=/srv/knowledge/acme
```

기본값 없는 `${ENV}` 인데 환경변수도 없으면 **빈 값**이 된다 — `${...}` 문자열이 그대로
경로가 되어 엉뚱한 폴더를 만드는 일을 막는다. 빈 값이면 "설정되지 않음"으로 표시되고
수집 버튼이 나오지 않는다.

기본값에 `C:/...` 처럼 콜론이 들어가도 `:-` 구분자와 혼동하지 않는다 (첫 `:-` 만 구분자로 본다).

---

## 7. 확인

```bash
python scripts/sync_product_knowledge.py --product "Acme Viewer" --dry-run
```

- `/knowledge` — 수집 대기 건수, 마지막 수집 시각, QA 규칙 Rev
- `/knowledge/source/<제품>` — 선택된 자산과 제외 이유 (JSON)
- `/qa-agent/readiness?product=<제품>` — 사양서·TC 건수, `rules_available`
- `/qa-agent/rules?product=<제품>` — Skill별 규칙 태깅과 주입량

`tests/test_product_knowledge.py`의 opt-in 테스트가 **실제 폴더의 모든 파일이 분류되는지**
확인한다. `real_fixtures.local.env`에 `REAL_VXVUE_KNOWLEDGE_DIR`을 넣으면 동작한다 —
새 문서가 조용히 unknown으로 빠지는 것을 잡기 위한 테스트다.

---

## 8. 코드를 고쳐야 하는 경우

거의 없지만, 있다면 다음뿐이다.

| 상황 | 고칠 곳 |
|---|---|
| 새로운 리비전 표기 방식 (예: `2026Q3`) | `app/core/product_knowledge.py` 의 리비전 정규식 |
| 새로운 자산 종류 (예: Release Note를 별도 종류로) | `KIND_*` 상수 + `DEFAULT_CLASSIFIERS` |
| 새로운 문서 형식 (예: `.pptx`) | `DEFAULT_EXTENSIONS` + `app/parsers/` |
| 사양서 출처가 다른 크롤러 | `specification.source` 값 + 해당 출처 모듈 (`vxvue_spec_sync.py` 패턴) |

리비전 정규식과 분류 규약은 **제품 무관 공통 규칙**으로 유지한다. 제품 이름으로 분기하는
코드를 넣지 않는다 — 그러면 제품이 늘 때마다 코드가 늘어난다.
