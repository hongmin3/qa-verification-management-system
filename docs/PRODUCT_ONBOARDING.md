# 새 제품 추가

> 상위: [문서 지도](README.md) · 사용 안내: [USER_GUIDE](USER_GUIDE.md)

**코드 변경 없이 YAML 한 장으로 끝난다.** 제품 간 공통 규약은 파일명이고, 제품별로 다른
것은 지식 폴더 경로뿐이다.

VXvue와 Bellalun Viewer가 이미 같은 파일명 규약을 쓰고 있어 그것을 기준으로 삼았다.

---

## 1. 3단계

### ① 설정 파일 작성 (제품 등록은 이것으로 끝난다)

화면에서 이름만 적어 제품을 더하는 양식은 없다(REQ-KNOW-002). 설정 파일을 배포하고 앱을 다시
띄우면 제품 표에 들어가고, `/knowledge` 현황판에 카드가 생기며, Regression 분석·매뉴얼 개정
검증·QA Agent 의 제품 목록에도 나온다.

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

# 자료 종류별 출처 프로필 (REQ-KNOW-018). source: manual | alm_crawler | knowledge_folder | external_sync
# required 를 비우면 종류별 기본값(사양서·TC·QA 규칙은 꼭 필요, 매뉴얼·지침 프롬프트는 없어도 됨).
specification:
  source: manual        # ALM 크롤러 연동이 있으면 alm_crawler (화면 업로드가 막힌다)
testcase:
  source: manual
manual:
  source: knowledge_folder   # 기본값. manual 로 바꾸면 상세 화면에서 매뉴얼도 올릴 수 있다
qa_rules:
  source: knowledge_folder   # 규칙 자산은 화면 업로드가 없다(규칙 로더가 수집 기록만 읽는다)
  required: true
instruction_prompt:
  source: knowledge_folder

# Polarion Issue Export 폴더가 있으면 채운다. 없으면 QA Agent 화면에서 JSON을 업로드한다.
issue_source:
  source: manual

sync:
  day_of_week: "mon"
  schedule_time: "08:00"
  stale_after_days: 14       # 마지막 성공 수집이 이보다 오래되면 상태가 '업데이트 필요'
```

### ② 수집

지식 폴더가 있는 **담당자 PC에서** 실행한다. 먼저 무엇이 수집될지 확인하고,

```bash
python scripts/sync_product_knowledge.py --product "Acme Viewer" --dry-run
```

맞으면 운영 서버로 올린다 (서버는 이 폴더를 볼 수 없다 — §6).

```bash
python scripts/sync_product_knowledge.py --product "Acme Viewer" --upload-to http://<서버 주소>:24357
```

바뀐 파일만 전송한다 (sha256 비교). VXvue 실측으로 첫 회 106MB, 이후 변경 없으면 0MB다.
서버가 폴더를 직접 볼 수 있는 환경이라면 제품 상세 화면(`/knowledge/products/<slug>`)의
**"지금 수집"** 버튼으로도 같은 일을 할 수 있다 — 볼 수 없으면 버튼이 숨는다.

### ③ 상태 확인

`/knowledge` 의 카드가 `정상` 인지 본다. `주의`·`오류` 면 줄에 마우스를 올려 이유를 보고,
`상세보기` 에서 자료별 상태와 수집 제외 목록을 확인한다(REQ-KNOW-019·020).

---

## 2. 파일명 규약 — 이것만 지키면 분류는 자동

| 접두사 / 패턴 | 분류 | `documents` 등록 |
|---|---|---|
| `(사양서)`, `*SRS 사양서*` | specification | O |
| `(매뉴얼)`, `System Integration Guide*`, `*Conformance Statement*`, `*Operation Manual*`, `*Service Manual*`, `*API Protocol Manual*` | manual | O |
| `(TC)`, `*Test Case*`, `*TestCase*`, `*Checklist*` | testcase | O |
| `[QA 작성 규칙]` | qa_rules | X (규칙 로더가 읽음) |
| `*지침 프롬프트*`, `*지침_프롬프트*` | instruction_prompt | X (규칙 로더가 읽음) |

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

담당자 PC 에서 평일 10:00 에 자동 실행하려면 작업 스케줄러에 등록한다. 사양서 동기화(평일 09:40) 뒤에 돌게 한다.

```
schtasks /Create /TN "QA_ProductKnowledge_Sync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 ^
  /TR "C:\path\to\.venv\Scripts\python.exe C:\path\to\scripts\sync_product_knowledge.py --upload-to http://<서버 주소>:24357"
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

- `/knowledge` — 제품 카드의 상태(정상·주의·오류)와 자료 종류별 개수·출처
- `/knowledge/products/<slug>` — 자료별 상태, 수집 제외 목록, `고급 정보`(폴더 경로·접근 가능 여부·QA 규칙 Rev)
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
| 다른 제품에 ALM 사양서 자동 수집을 붙임 | 지금 ALM 수집 어댑터(`app/modules/knowledge/vxvue_spec_sync.py`, `knowledge/scheduled_jobs.py`)는 `vxvue.yaml` 하나만 읽는다. 어댑터를 제품 설정 목록을 도는 형태로 일반화해야 한다. 공통 Knowledge 코드(현황판·상태 판정·등록)는 고치지 않는다 |
| 새 출처 종류 (네 가지 밖) | `app/core/product_config.py` 의 `KNOWN_SOURCES`·`SOURCE_LABELS` 와 `app/core/knowledge_status.py` 의 문구 표 (SPEC REQ-KNOW-018 표 먼저) |
| QA Agent 점검: 제품이 Polarion 이 아닌 다른 ALM 을 씀 | `app/modules/daily_qa/polarion.py` 와 같은 읽기 전용 클라이언트를 새로 두고 `collector.py` 가 고르게 한다 (§9) |
| QA Agent 점검: 새 연구소 결과 종류 (공통 값 8개에 없는 뜻) | `product_adapter.py` 의 `RD_*` 와 `change_events.route_issue` 분석 표 (사양 REQ-QAINTEL-010 먼저) |
| QA Agent 점검: 새 Regression 축 | `app/modules/qa_agent/schemas.py` 의 축 코드 (제품 설정은 있는 축을 고르기만 한다) |
| QA Agent 점검: 초안 Excel 이 표 한 장이 아닌 형식 | `app/modules/daily_qa/checklist_xlsx.py` |

리비전 정규식과 분류 규약은 **제품 무관 공통 규칙**으로 유지한다. 제품 이름으로 분기하는
코드를 넣지 않는다 — 그러면 제품이 늘 때마다 코드가 늘어난다.

---

## 9. QA Agent 점검(QA Intelligence Agent)에 붙이기

매일 아침 변경을 탐지해 분석하는 `/qa-agent` 대시보드에 제품을 올리는 절차다. 엔진은 제품 공통이고
제품마다 다른 것은 아래 설정과 제품 규칙 Skill 뿐이다(NFR-QAINTEL-002). VXvue 설정
(`config/products/vxvue.yaml` 끝의 `alm:`·`qa_intelligence:`)을 본보기로 쓴다.

### 9.1 Polarion 연결

서버 `secrets.txt` 의 `POLARION_HOST`·`POLARION_TOKEN` 은 제품 공통이다. 토큰 계정이 새 제품 프로젝트를
**읽을 수 있는지**만 확인한다. 쓰기 권한은 주지 않는다(클라이언트에 GET 만 있다).

```yaml
alm:
  project_id: "AcmeViewer"          # Polarion 프로젝트 ID
```

### 9.2 SRS·Issue 조회식

```yaml
alm:
  queries:
    srs: "type:srs"                 # SRS 전체
    issue: "type:issue"             # 이슈 전체. 좁히지 않는다 — 스냅샷 비교가 새 이슈·바뀐 이슈를 가린다
```

조회식을 좁히면 조건에서 빠진 이슈가 "없어진 이슈"로 기록된다. 이슈 수가 어제의 절반 아래로 줄면
(`daily_qa.intelligence.issue_drop_ratio`) 수집 실패로 보고 기준을 옮기지 않는다.

### 9.3 필드 매핑

공통 모델 이름 → 그 제품의 Polarion 필드 이름이다. 적지 않은 공통 필드(`id`, `title`, `status`,
`updated`, `description`, `severity`, `created`)는 Polarion 표준 이름을 쓴다. 목록으로 적으면 앞에서부터
값이 있는 첫 필드를 쓴다.

```yaml
alm:
  fields:
    srs:
      legacy_id: oldId                       # 옛 SRS 번호. TC 가 이 번호를 쓰면 적는다
      description: [descriptionKR, description]
      is_category: isCategory
    issue:
      rd_result: rndReviewResult             # 연구소 검토 결과
      reproduction_step: reproductionStep
      occurrence_cause: occurrenceCause
      action_details: actionDetails
  relations:                                 # 공통 관계 이름 → Polarion relationships 이름
    occurred_versions: occurredVersion
    target_versions: targetVersion
    linked_items: linkedWorkItems
    comments: comments
```

### 9.4 연구소 결과(R&D) 매핑

그 제품의 연구소 결과 원본 값 → 공통 값이다. 공통 값은 `FIXED`(수정 완료), `SPEC`(사양대로),
`NOT_BUG`(결함 아님), `DUPLICATE`, `PENDING`, `NO_ACTION` 이다. 값이 비면 `UNSET`, 표에 없는 값은
`OTHER` 가 되어 분석 대상에서 빠진다.

```yaml
alm:
  rd_result_mapping:
    fixed: FIXED
    as_designed: SPEC
    not_a_bug: NOT_BUG
```

> **주의** `FIXED` 로 매핑한 값만 수정 완료 이슈 분석과 TC 초안이 된다. `SPEC`·`NOT_BUG` 는 Spec 판정 이슈 분석이 된다.

### 9.5 Knowledge

TC·매뉴얼·(ALM 을 쓰지 않으면) 사양서는 §1~§6 의 지식 폴더 수집으로 서버에 올린다. 대시보드 맨 위
"지식 문서 업로드 현황"에 이 문서들의 최신본과 업로드 날짜가 보인다. 등록된 사양서 문서 조각은
분석 후보로도 쓴다(`daily_qa.intelligence.use_knowledge_documents`).

### 9.6 QA Rules

QA 규칙 `.md` 는 §5 의 규칙 문서 규약으로 지식 폴더에 둔다. 규칙이 없거나 판이 다르면 AI 단계가
돌지 않는다.

```yaml
qa_intelligence:
  rules:
    supported_rev: "1.0"            # 제품 규칙 Skill 이 기준으로 삼는 판. 비우면 판을 검사하지 않는다
    product_skill: "acme-qa-rules"
```

### 9.7 제품 규칙 Skill

`config/products/<slug>/skills/<product_skill>/SKILL.md` 를 만든다. 공통 Skill(`qa-common-rules` 등)이
모든 제품에 쓰는 결과 형식·금지 조치를 정하고, 제품 Skill 은 그 제품 규칙의 절 번호·검증 관문·용어만 담는다.
본보기는 `config/products/vxvue/skills/vxvue-qa-rules/` 다. QA 규칙 원문은 저장소에 넣지 않는다.

```yaml
qa_intelligence:
  regression_axes: [DIRECT, STATE, DATA, PERSISTENCE, INTEGRATION, PERMISSION, PRIOR_ISSUE]   # 비우면 공통 7축
  comment_noise_patterns: []        # 진행 알림 댓글 정규식. 공통 기본 규칙에 더한다
```

### 9.8 TC·Checklist 형식

수정 완료 이슈·사양 변경의 초안 Excel 은 제품의 영향성평가 Checklist 형식을 따른다.

```yaml
qa_intelligence:
  checklist:
    template_name_contains: "영향성평가"   # 지식 사본 TC 가운데 이 글자가 파일 이름에 있으면 본보기
    category: "Changes Checklist"
    headers: ["Category", "TC ID", "버전", "SRS No", "변경사항", "Title", "Precondition", "Test Step", "Expected Result"]
```

### 9.9 예약 켜기

`config.yaml` 의 `daily_qa.products` 에 제품 이름을 더하고 앱을 재시작한다. 제품마다 예약
`qa_agent_<slug>`(평일 07:30, 공휴일 제외)가 생기고 대시보드에 제품 선택이 나타난다.

```yaml
daily_qa:
  products: ["VXvue", "Acme Viewer"]
```

제품만 끄려면 그 제품 설정의 `qa_intelligence.enabled: false` 를 쓴다.

### 9.10 첫 실행(Baseline)

```bash
.venv/bin/python scripts/run_daily_qa.py --check --product "Acme Viewer"
.venv/bin/python scripts/run_daily_qa.py --dry-run --no-email --product "Acme Viewer"
.venv/bin/python scripts/run_daily_qa.py --no-email --product "Acme Viewer"
```

첫 정식 실행은 기준 스냅샷만 저장한다(`기준 스냅샷 생성`, Claude 0회). 스냅샷은
`data/daily_qa/snapshots/<slug>/` 에, 잠금은 `data/daily_qa/<slug>/run.lock` 에 따로 생겨 다른 제품과 섞이지 않는다.

### 9.11 동작 확인

- `/qa-agent?product=Acme Viewer` — 마지막 실행 `기준 스냅샷 생성`, 다음 자동 실행 시각
- `app.log` 의 `scheduled_job_registered id=qa_agent_acme-viewer`
- 다음 평일 실행 뒤 대시보드의 오늘 변경 요약과 `/qa-agent/runs/<실행 ID>` 의 이벤트 목록
- 설정만으로 붙는지는 `tests/test_qa_intel_products.py` 가 가짜 제품(FakeProduct)으로 확인한다

### 9.12 공통 코드를 고쳐야 하는 경우

§8 표의 "QA Agent 점검" 줄이다. 제품 이름으로 갈라지는 코드를 공통 엔진에 넣지 않는다.
