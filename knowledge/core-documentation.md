# 저장소 — 문서 작성 규칙

## 문서 지도를 함께 갱신한다
<!-- akela: id=docs-map scope=documentation tier=must -->

- `docs/README.md`가 문서 지도다. 문서를 추가·이동·삭제하면 **같은 커밋에서 이 표를 갱신한다.**
- 문서는 목적별로 나눈다. README는 개요와 링크만 두고 상세는 `docs/`로 넘긴다.
- 기능별 상세는 `docs/modules/<name>.md`에 둔다.

## 사용법은 문서가 아니라 앱 안에 둔다
<!-- akela: id=usage-in-app scope=documentation tier=must -->

기능 사용법은 `/impact-analyzer/guide`, `/manual-review/guide`처럼 화면 안에 둔다. 화면이 바뀌면 같이 바뀌어야 하기 때문이다. 같은 내용을 `docs/`에 중복해 적지 않는다.

## 사내 고유 정보는 공개 문서에 적지 않는다
<!-- akela: id=local-only-docs scope=documentation tier=must -->

- 이 저장소는 공개 저장소다(2026-10-13 사용자 결정, NFR-PRIV-002). 서버 주소·계정·사내 경로 같은 운영 정보는 `docs/local/` 에 두고 함께 올린다. 일반 문서(`README.md`, `docs/*.md`)에는 일반 규칙만 남긴다.
- 비밀값(토큰·비밀번호·키)은 공개 여부와 관계없이 어떤 문서에도 적지 않는다. "어디에 있는지"만 적는다. 비밀 설정 파일은 `.gitignore` 대상이다.
- 일반 문서에 고유명사가 필요하면 `<서버 IP>`처럼 자리표시자를 쓰고 실제 값은 `docs/local/` 에 둔다.

## 에이전트용 지식과 사람용 문서는 다르다
<!-- akela: id=knowledge-vs-docs scope=documentation tier=should -->

- `knowledge/`는 에이전트가 `akela compile`로 잘라 쓰는 지식이고, `docs/`는 사람이 읽는 문서다. 목적이 다르므로 같은 내용을 양쪽에 중복해 두지 않는다.
- `knowledge/`의 섹션은 규칙 위주로 짧게 쓴다. 모든 줄이 매 작업의 컨텍스트 비용이 된다.
- 하위 서비스 지식도 루트 `knowledge/<name>-*.md`에 둔다. 하위 디렉터리에 `knowledge/`나 `akela.json`을 새로 만들지 않는다.
- activity는 서비스 이름 하나로 뭉뚱그리지 말고 실제 작업 단위로 나눈다(`<name>-dev`, `-auth`, `-ui`, `-deploy`, `-backup`). 하나로 묶으면 매 작업에 전부 들어와 스코핑이 무의미해진다.
- 한 섹션이 여러 작업에 필요하면 `scope`에 **쉼표로 여러 activity**를 준다 (`scope=a,b`). 판단 기준은 "그 지식이 없으면 이 작업의 결과가 달라지는가"다.
- `scope=all`은 아껴 쓴다. 모든 작업의 slice에 들어간다. 그리고 `scope=all` + `tier=should` 조합은 컴파일러가 `general-scope`로 버리므로 실질적으로 아무 작업에도 전달되지 않는다 — 특정 activity로 좁히거나 `must`로 올린다.

## 작업 기록 문서
<!-- akela: id=handoff-docs scope=documentation tier=should -->

- `NEXT_STEPS.md`는 남은 작업을 우선순위 순으로, `OPEN_QUESTIONS.md`는 결정이 필요한 항목을, `HANDOFF.md`는 인수인계 기록을 담는다.
- 배포·검증을 마치면 무엇을 어떤 순서로 확인했는지 함께 남긴다. 결과 없이 "완료"만 적지 않는다.
