# 제품 지식 자산 — 수집·분류·규칙 로딩

> 제품마다 사양서·매뉴얼·TC·QA 규칙 최신본을 모아 두는 폴더가 있다. 앱은 그 폴더를 읽기만
> 하고 프로젝트 안으로 수집한다. **제품별 코드 분기를 만들지 않는다.**

## 제품 간 공통 규약은 파일명이다
<!-- akela: id=filename-convention scope=product-knowledge,core-development tier=must -->

- 분류는 파일명 접두사로 한다: `(사양서)` / `(매뉴얼)` / `(TC)` / `[QA 작성 규칙]` / `*지침 프롬프트*`.
- VXvue와 Bellalun Viewer가 이미 같은 규약을 쓰고 있어 그것을 기준으로 삼았다.
- 새 제품은 `config/products/<slug>.yaml`에 `knowledge_source.dir`만 넣으면 **코드 변경 없이** 편입된다.
- 분류 우선순위가 있다: `(TC)` 접두사가 있으면 `*Checklist*` 패턴보다 먼저 testcase로 확정된다.
- 규약에 맞지 않는 파일은 **조용히 버리지 않고** `unknown`으로 보고한다.
- 제품 이름으로 분기하는 코드를 넣지 않는다. 그러면 제품이 늘 때마다 코드가 늘어난다.

## 리비전은 파일명에서만 유도한다
<!-- akela: id=revision-from-filename scope=product-knowledge tier=must -->

- **`documents.revision` 컬럼은 등록 순번(`Rev.1`, `Rev.2`)이라 문서 리비전이 아니다.** 그 값으로 비교하면 `260831`이 `260907`보다 최신으로 판정된다 (실제로 그렇게 판정됐다).
- 표기 3종: `(260907)` 날짜 / `V1.0.11`·`V1.0.12W1` 버전 / `Rev1.12`.
- 리비전이 아닌 것: `RA16-148-002`·`R-20-643` 사내 문서번호, `_KO`/`_EN` 언어, `_확인완료`·`(개정)` 상태 꼬리표.
- 문서번호 정규식은 뒤 경계를 `\b`가 아니라 **부정 룩어헤드**로 잡는다 — `RA16-148-002_VXvue`처럼 `_`가 이어지면 `\b`는 `002` 앞에서 끊겨 문서번호가 반만 잘린다.
- 리비전 표기 방식이 서로 다르면(날짜 vs 버전) **비교하지 않는다.** 확실할 때만 판정한다.
- 같은 문서의 한국어·영어본은 **서로 다른 문서**로 본다 (`logical_id`에 언어가 들어간다).

## 같은 논리 문서가 여러 개면 하나만 고른다
<!-- akela: id=logical-document-selection scope=product-knowledge tier=must -->

- 우선순위: ① 리비전이 더 최신 ② 원본 형식(PDF/DOCX/XLSX) > MD > TXT ③ 파일 수정시각.
- **원본이 추출본보다 우선한다.** 사람이 미리 뽑아둔 `.txt`가 원본보다 오래된 사례가 실제로 있었다(`사양서1(260824).txt` vs `사양서1(260907).pdf`). 추출본은 갱신을 잊기 쉽다.
- 제외한 파일과 **제외 이유**를 manifest에 남긴다. 판단을 되짚을 수 있어야 한다.

## 수집만으로는 분석에 쓰이지 않는다
<!-- akela: id=sync-then-register scope=product-knowledge tier=must -->

- `Storage.active_documents`가 보는 것은 `documents` 테이블이다. 파일 복사만으로는 분석 대상이 되지 않는다.
- `sync_and_register()`가 수집→등록을 한 번에 한다. `/knowledge` 버튼과 CLI와 스케줄러가 같은 함수를 쓴다 — 로직은 한 곳에만 있다.
- 지식 폴더는 QA 담당자 PC에 있고 서버에는 없다. 서버에서 접근 불가면 화면에 CLI 안내를 띄우고 버튼을 숨긴다. 스케줄러도 **조용히 건너뛴다**(오류가 아니다).
- sha256 비교로 변경된 파일만 재수집한다. 매주 실행해도 증분이다.

## 구버전 정리는 확실할 때만 한다
<!-- akela: id=stale-cleanup-rules scope=product-knowledge tier=must -->

자동 정리 대상:

- 같은 논리 문서의 **확실히 더 오래된** 리비전 (같은 kind, 비교 가능한 표기).
- 바이트까지 같은 파일이 두 번 등록된 것 (sha256 일치).
- **원본 파일이 사라진 등록** — 검색에 기여하지 못하면서 "사양 없음" 오판만 만든다. 파일이 이미 없으므로 지워도 잃는 것이 없다.

지우지 않고 보고만 하는 것:

- 종류가 어긋나게 등록된 것 (매뉴얼이 `specification`으로).
- 리비전 비교가 불가능한 것.

사람이 다른 기준으로 올려둔 문서를 자동으로 없애면 그 판단 근거가 사라진다.

## 문서 로딩 실패가 분석 전체를 막지 않는다
<!-- akela: id=loader-resilience scope=product-knowledge,core-development tier=must -->

- `app/core/knowledge_documents.py`는 문서 하나가 사라졌거나 파싱에 실패해도 예외를 올리지 않고 건너뛴다. 등록된 문서 중 파일이 없는 것 하나 때문에 분석 전체가 죽은 적이 있다.
- 대신 무엇을 못 읽었는지 `failures`에 남긴다. **조용히 빠지면 "사양 없음" 오판으로 이어진다.**
- Regression 영향 분석과 QA Agent가 이 모듈을 공유한다. 두 기능이 서로 다른 문서 집합을 보면 같은 Issue에 다른 결론이 나오고 그 차이를 설명할 수 없다.

## QA 규칙 문서는 절 단위로 잘라 주입한다
<!-- akela: id=qa-rules-slicing scope=product-knowledge,qa-agent-dev tier=must -->

- 규칙 전문은 약 30KB다. 매 호출에 보내면 그것만으로 1만 토큰이 넘고 대부분 무관하다.
- 절 단위로 쪼개 Skill(S01~S11)·Gate(G1~G5)로 태깅하고 호출 시점에 그 Skill 절만 문자 예산 안에서 주입한다. `akela compile`과 같은 원리다. VXvue 실측 **85~94% 절감**.
- **하위 절은 부모 태그를 물려받는다.** `# 32. Regression 영향 범위` 아래 `## 직접`/`## 상태`는 제목만으로 주제를 알 수 없다. 상속 전 미태깅 71절 → 후 39절.
- **Skill 전용 절과 공통 절의 예산을 분리한다.** 한 예산으로 합치면 문서 앞쪽 공통 절이 예산을 다 먹는다 (S01 slice 5.8KB 중 Skill 전용이 0.4KB뿐이었다).
- Skill 태깅 키워드는 제품 고유 용어가 아니라 **QA 공통 용어**만 쓴다. 비교 시 공백을 지우므로 `자체검토`/`자체 검토` 표기 차이를 흡수한다.

## 규칙 MD는 escape 정규화가 먼저다
<!-- akela: id=qa-rules-normalization scope=product-knowledge tier=should -->

- 실제 파일은 편집 도구가 모든 특수문자를 escape 한 상태다 — `\#`, `\-`, `&#x20;`, 그리고 줄마다 빈 줄이 하나 더.
- 그대로 파싱하면 제목이 하나도 잡히지 않는다 (`grep '^#'` 결과 0건이었다).
- 본문이 없는 절은 하위 절을 묶는 컨테이너 제목이다. 태그는 물려주되 프롬프트에 넣을 내용이 없으므로 절 목록에는 담지 않는다.

## 제품마다 규칙 범위가 다른 것은 결함이 아니다
<!-- akela: id=per-product-rule-coverage scope=product-knowledge tier=should -->

- Skill별 태깅이 0절이면 **그 제품 규칙이 그 주제를 다루지 않는다**는 뜻이다. Viewer 제품에는 Generator가 없다.
- VXvue Rev1.12는 140절/30.7KB, Bellalun Viewer는 82절/23.2KB이고 S04/S06/S09/S10이 0절이다.
- 규칙이 수집되지 않으면 QA Agent의 G1이 **BLOCK**한다. 규칙 없이 낸 판정은 담당자마다 달라지고 근거를 되짚을 수 없다.

## 사내 문서는 커밋하지 않는다
<!-- akela: id=knowledge-not-committed scope=product-knowledge,deployment tier=must -->

- 수집 사본·정규화 텍스트·manifest는 `data/product_knowledge/`에 있고 `.gitignore` 대상이다.
- 저장소는 공개 포트폴리오 용도다. 코드·스키마·예제만 올라간다.
- 테스트 픽스처는 **합성 파일명·합성 규칙 텍스트**만 쓴다. 실제 폴더로 도는 확인은 `real_fixtures.local.env`가 있을 때만 동작하는 opt-in 테스트로 분리한다.
