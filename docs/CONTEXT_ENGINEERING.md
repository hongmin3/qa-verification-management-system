# Context Engineering — 저장소의 기준과 Akela

> 상위 문서: [README](../README.md) · [문서 지도](README.md)

이 저장소는 AI 에이전트(Claude Code / Codex)와 함께 개발한다. 그때 실제로 발목을 잡는 것은
모델 성능이 아니라 **컨텍스트**다. 매 작업마다 저장소 문서를 통째로 넣으면 두 가지가 같이
나빠진다.

- **토큰** — 작업 1건마다 전체 문서를 읽으니 비용이 작업 수에 비례해 늘어난다.
- **정확도** — 지금 하는 일과 무관한 문서가 섞여 들어와 엉뚱한 파일을 고친다.

해결은 두 단계다. 먼저 **폴더에 기준을 만들고**, 그 기준을 이용해 **작업에 필요한 지식만
잘라서 준다.** 앞의 것이 없으면 뒤의 것을 할 수 없다.

---

## 1단계 — 폴더를 일정한 기준으로 관리한다

"어디에 무엇이 있는지"가 규칙으로 정해져 있어야, 에이전트가 전부 읽지 않고도 필요한 곳을
찾는다. 이 저장소가 지키는 기준은 다음과 같다.

| 기준 | 내용 | 근거 문서 |
|---|---|---|
| 배포 단위는 둘뿐 | `app/`(핵심 앱)과 `services/*`(하위 서비스). 새 기능은 어느 쪽인지 먼저 정한다 | [공용 아키텍처](SHARED_PLATFORM_ARCHITECTURE.md) |
| 기능은 자기 폴더가 전부 소유 | `app/modules/<name>/`이 라우터·스키마·서비스·템플릿·테스트를 갖는다. `app/web/`은 URL prefix만 결정하고 로직을 갖지 않는다 | 같은 문서 |
| 하위 서비스는 결합하지 않는다 | 코드 import·DB 공유 없음. 연결은 URL 링크와 nginx 라우팅까지 | 같은 문서 |
| 지식은 루트 한 곳 | 하위 서비스도 `knowledge/`를 따로 만들지 않고 `knowledge/<name>-*.md`로 모은다 | [CLAUDE.md](../CLAUDE.md) |
| 문서는 목적별 | `docs/README.md`가 지도. 기능별 사용법은 문서가 아니라 앱 화면 안에 둔다 | [문서 지도](README.md) |
| 테스트 경계 | 루트 `pytest`는 `testpaths`로 핵심 앱만 수집. 하위 서비스는 자기 CI에서 | `pytest.ini` |
| 프롬프트는 한 곳 | `app/prompts/*.yaml`이 본문·버전·생성 설정을 함께 갖는다 | [비용 절감 설계](COST_OPTIMIZATION.md) |

이 규칙들을 사람이 기억하는 대신 [`CLAUDE.md`](../CLAUDE.md) / [`AGENTS.md`](../AGENTS.md)에
적어 두고, 에이전트가 매 작업 전에 읽는다.

---

## 2단계 — Akela로 작업 종류별 지식만 주입한다

[Akela](https://github.com/TimothyHan/akela)는 지식 문서를 **섹션 단위로 쪼개고, 각 섹션에
`scope`(어떤 작업에 필요한가)와 `tier`(얼마나 중요한가)를 태깅**해 두었다가, 작업 종류에 맞는
부분만 뽑아 컨텍스트로 주는 도구다. **런타임 의존성이 아니다** — 앱 실행·배포 동작에 전혀
영향을 주지 않는다.

### 태깅

```markdown
## Product → Document → Revision 계층 구조
<!-- akela: id=manual-hub-hierarchy scope=manual-hub-dev,manual-hub-ui tier=must -->
```

- `scope=all` — 모든 작업에 들어간다
- `scope=manual-hub-dev` — 그 activity 작업에만 들어간다
- **쉼표로 여러 activity를 줄 수 있다.** 위 예시는 매뉴얼 서버의 백엔드 작업과 프론트엔드
  작업 양쪽에 필요한 섹션이다
- `tier=must` / `should` — 컨텍스트가 빠듯할 때 무엇을 먼저 버릴지의 순서

### 작업 흐름

```text
knowledge/*.md
  ↓  akela compile --activity <activity> --task <task-id>
.akela/runs/<run>/slice.md      이 작업에 필요한 섹션만 (나머지는 dropped 로 기록)
  ↓  에이전트가 slice 만 읽고 작업
akela log applied <source-id>          근거로 실제 사용한 규칙
akela log contradicted <source-id>     결과가 뒤집은 규칙 (틀린 내용을 원문 인용과 함께)
akela log outcome --status DONE
  ↓  akela stats / akela/CURATE.md
지식 유지보수      안 쓰이는 규칙은 좁히거나 버리고, 틀린 규칙은 고친다
```

절차의 정본은 [`akela/PROTOCOL.md`](../akela/PROTOCOL.md)(작업), 
[`akela/ONBOARD.md`](../akela/ONBOARD.md)(새 지식 편입),
[`akela/CURATE.md`](../akela/CURATE.md)(정기 검토)다.

### 실제로 주입되는 slice

컴파일 결과에는 **쓰인 것과 버려진 것, 그리고 버린 이유**가 함께 남는다
(`.akela/runs/<run>/slice.md`).

```yaml
compiler: akela 0.1.4   domain: default
budget: {max: 0, used: 82}
sources:
  - id: REF-core-architecture#module-boundaries      tier: must   lines: 9
  - id: REF-core-architecture#active-documents-rule  tier: must   lines: 7
  - id: REF-core-ai-integration#id-cross-validation  tier: must   lines: 9
  ...                                                             # 21개
dropped: []
```

`dropped`에는 이유가 함께 적힌다. 예를 들어 `reason: general-scope`는 그 섹션이 특정 작업에
매이지 않아 이번 slice에서 빠졌다는 뜻이다 — 아래 "측정해서 알게 된 것" 참고.

---

## 이 저장소의 실제 구성

| 항목 | 값 |
|---|---|
| 지식 파일 | 14개 (`knowledge/*.md`) |
| 섹션 | 100개 · 약 69KB (핵심 앱 24KB / QA Agent·제품 지식 20KB / 매뉴얼 서버 25KB) |
| scope 분포 (중복 포함) | `core-development` 23 · `manual-hub-dev` 13 · `qa-agent-dev` 13 · `manual-hub-deploy` 10 · `product-knowledge` 10 · `deployment` 9 · `manual-hub-auth` 9 · `web-ui` 7 · `documentation` 6 · `testing` 6 · `manual-hub-backup` 6 · `manual-hub-ui` 5 · `all` 2 |
| tier 분포 | `must` 56 · `should` 44 |
| activity | 핵심 앱 5종(`core-development`, `web-ui`, `testing`, `deployment`, `documentation`) + QA Agent 2종(`qa-agent-dev`, `product-knowledge`) + 매뉴얼 서버 5종(`manual-hub-dev`, `-auth`, `-ui`, `-deploy`, `-backup`) |
| 기록된 작업 | 31건 (`.akela/runs/`) |
| 근거 사용 기록 | `applied` 56건 · `contradicted` 1건 (`akela/learnings-log.jsonl`) |

작업 종류별로 실제 컴파일한 slice 크기다. 69KB 전체를 매번 넣는 대신 필요한 만큼만 들어간다.

| activity | 섹션 | slice |
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

## 측정해서 알게 된 것 — 태깅해 뒀다고 전달되는 것이 아니다

`akela stats`와 컴파일 로그를 확인하다 발견한 사실이다.

`scope=all` + `tier=should`로 태깅한 섹션 4개(`components`, `known-issues`, `check-order`,
`rerun-caveats`)가 **30번의 컴파일에서 단 한 번도 slice에 들어가지 않았다.** 매번
`reason: general-scope`로 버려지고 있었다. 컴파일러는 `scope=all`에서 `must`만 남기고
`should`는 활동별 지식에 자리를 내주기 때문이다.

즉 이 저장소의 핵심 앱 지식은 "작지만 효율적"이었던 게 아니라 **사실상 2개 섹션뿐**이었다.

고친 방법:

1. 네 섹션을 실제로 필요한 activity로 좁혔다 (`core-development`, `deployment`).
2. 코드에서 확인한 규칙으로 핵심 앱 지식을 6개 파일 · 40여 섹션으로 보강하고, 각각을
   activity 단위로 태깅했다.
3. 그 결과 `core-development` slice가 2개 섹션(1.2KB)에서 21개 섹션(12KB)이 됐다.

교훈은 하나다. **지식을 써 두고 태그를 붙였다고 에이전트에게 전달되는 것이 아니다.**
`akela stats`의 dormant / dropped 기록을 주기적으로 확인해야 한다
([`akela/CURATE.md`](../akela/CURATE.md)).

## 저장소를 합칠 때 이 기준을 어떻게 적용했나

QA Manual Hub를 `services/qa-manual-hub/`로 병합할 때, 원래 저장소에 있던
`akela.json`·`knowledge/`·`akela/`를 **그대로 가져오지 않았다.** 하위 폴더에 별도 Project
Root가 생기면 에이전트가 어느 Context를 써야 하는지 모호해지고, 두 지식 베이스가 서로를
모른 채 쌓인다.

대신:

1. `services/qa-manual-hub/knowledge/*.md` → 루트 `knowledge/manual-hub-*.md`로 이동
2. 31개 섹션 전부에 `scope`와 `tier`를 부여
3. `akela.json`의 `activities`에 이 서비스의 작업 단위를 추가
4. 하위의 `akela.json` / `akela/` 삭제, `AGENTS.md` / `CLAUDE.md`는 루트를 가리키는 안내로 대체

결과적으로 지식이 25KB 늘었는데도 **회귀 분석 코드 작업의 slice에는 그 25KB가 한 줄도
들어가지 않는다.** 저장소가 커진 만큼 매 작업의 토큰이 늘지 않는다. 새 하위 서비스를
추가할 때도 같은 순서를 따른다
([공용 아키텍처](SHARED_PLATFORM_ARCHITECTURE.md)의 하위 서비스 체크리스트).

## activity 입자 크기 — 굵게 잡으면 스코핑이 무의미해진다

병합 직후에는 매뉴얼 서버 지식 31개 섹션을 `manual-hub` 하나로 묶었다. 태깅은 돼 있었지만
**백업 스크립트 한 줄을 고치는 작업에도 인증 설계·프론트엔드 구조·데이터 모델이 전부 따라
들어와 slice가 27KB**였다. 스코핑을 해놓고도 효과가 없었던 셈이다.

그래서 실제 작업 단위로 쪼갰다. 기준은 "이 저장소에서 실제로 반복되는 작업 종류"다.

| activity | 언제 쓰나 | slice |
|---|---|---|
| `manual-hub-dev` | 백엔드 코드·데이터 모델 변경 | 14KB |
| `manual-hub-deploy` | 설치·배포·nginx·설정 | 8.7KB |
| `manual-hub-auth` | 인증·권한·감사 로그 | 7.4KB |
| `manual-hub-backup` | 백업·복구·관리 CLI | 6.0KB |
| `manual-hub-ui` | React 프론트엔드 | 5.0KB |

한 섹션이 여러 작업에 필요하면 `scope`에 **쉼표로 여러 activity**를 준다. 예를 들어
`manual-hub-hierarchy`(제품→문서→버전→파일 4계층)는 백엔드와 프론트엔드 양쪽에 필요하므로
`scope=manual-hub-dev,manual-hub-ui`다. 반대로 `manual-hub-sort-rules`(자연 정렬·빈 값 처리)는
표를 그리는 쪽에만 필요하므로 `manual-hub-ui` 하나다.

판단 기준은 하나다. **그 지식이 없으면 이 작업의 결과가 달라지는가.** 달라지지 않으면 넣지
않는다.

## 새 지식을 추가할 때

1. 관련 `knowledge/*.md`에 섹션을 쓰고 `<!-- akela: id=… scope=… tier=… -->`를 붙인다.
2. scope를 못 정하겠으면 태그 없이 두어도 된다. `akela stats`가 `unscoped`로 보고하고,
   [`akela/ONBOARD.md`](../akela/ONBOARD.md) 절차로 나중에 범위를 정한다.
3. `scope=all`은 아껴 쓴다. 모든 작업의 slice에 들어가므로 매 작업마다 비용을 낸다.
4. 새 하위 서비스라면 `<name>-*.md` 이름과 `scope=<name>`, 그리고 `akela.json`에 activity 추가.

## 한계

- Akela는 **에이전트용 지식**만 다룬다. 사람이 읽는 문서는 `docs/`에 따로 있고, 둘은 목적이
  다르다. 같은 내용을 양쪽에 중복해 두지 않는다.
- 태깅이 실제와 어긋나면 잘못된 지식이 계속 주입된다. 그래서 `applied` / `contradicted`
  기록을 남기고 [`akela/CURATE.md`](../akela/CURATE.md)의 정기 검토로 교정한다.
- 지식이 늘수록 `scope=all` 섹션이 늘어나기 쉽다. 검토에서 가장 먼저 보는 항목이다.
- `scope=all` + `tier=should`는 컴파일러가 `general-scope`로 버린다. 모든 작업에 필요한
  규칙이면 `must`로 올리고, 아니면 activity로 좁힌다. 이 조합으로 두면 아무 데도 안 간다.
- activity를 너무 굵게 잡으면 scope를 붙여도 효과가 없다. 아래 "activity 입자 크기" 참고.
