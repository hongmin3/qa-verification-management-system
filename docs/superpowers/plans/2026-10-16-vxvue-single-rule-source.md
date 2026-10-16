> 2026-10-16 구현 승인: 서버형 Skill 기준으로 전환한다. 구형 PC 직접 조회 호환 경로는 운영 범위에서 제외한다. 현재 구현·검증 결과는 progress.md와 CHANGELOG.md를 따른다.

# VXvue QA 지침을 MD 하나로 관리하는 전환 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 아래 체크 항목은 아직 구현하지 않았다.

**Goal:** 담당자는 QA 규칙 MD 하나만 편집한다. 서버 연결 스킬과 자동 QA는 동기화된 문서 내용을 읽으며, 문서 Rev가 바뀌었다는 이유로 설치·설정을 다시 고치지 않는다.

**Architecture:** 기존 지식 수집·업로드·질문별 원본 판을 재사용한다. 공통 규칙 선택기가 통합 MD 또는 기존 MD+TXT를 구분하고, 챗봇·서버 연결·자동 QA가 같은 선택 결과를 사용한다. 연결 도구의 이름·입출력·인증은 유지한다.

**Tech Stack:** 기존 Python/FastAPI, Markdown, 지식 자산 목록, pytest. 새 외부 라이브러리는 추가하지 않는다.

**Spec:** 현재 `SPEC.md`의 REQ-DAILY-010·023·024, REQ-KNOW-014 및 `specs/vxvue-chatbot.md`의 REQ-VXVUECHATBOT-005·006·007·008을 전환 전에 수정한다. 이 계획은 사양 변경 제안이며 현재 동작 설명이 아니다.

## 1. 조사 결과와 구현 가능성

MD 하나에 운영 지침과 상세 규칙을 함께 담는 것은 가능하다. Skill 형식 자체가 업무 지침 TXT와 QA 규칙 MD 두 개를 요구하지는 않는다. 현재의 두 파일 요구는 이 프로젝트의 로더·스킬 안내에 구현되어 있다.

| 현재 소비 경로 | 확인한 근거 | 전환에 필요한 변경 |
|---|---|---|
| 서버 챗봇 | `app/modules/vxvue_chatbot/knowledge.py`의 `prepare_knowledge()`는 `instruction_prompt`, `qa_rules`를 각각 필수로 찾는다 | 통합 MD가 유효하면 TXT 없이 준비하고 같은 본문을 한 번만 전달 |
| 자동 QA 준비 검사 | `app/modules/daily_qa/rules.py`의 `check()`, `config/products/vxvue.yaml`의 `supported_rev: "1.18"` | VXvue의 특정 판 고정을 해제하고 선택된 원문의 유효성을 검사 |
| 자동 QA 실행 폴더 | `app/modules/daily_qa/workspace.py`는 `rules/qa-guide.md`, `rules/instruction-prompt.txt`를 복사 | 통합 모드에서는 `qa-guide.md` 하나만 쓰고 재사용 폴더의 옛 TXT 제거 |
| 규칙 본문 읽기 | `app/core/qa_rules.py`의 `load_rule_set()`는 두 종류를 읽는다 | 통합 MD가 선택되면 옛 TXT와 구형 MD를 함께 주입하지 않음 |
| 자동 QA 규칙 미리 넣기 | `rule_context.py`, `guide-sections.json`은 절 번호를 사용 | 기존 번호는 탐색 힌트로 유지하되 최신 문서의 제목·기능 태그로 찾을 수 있게 보완. 누락을 숨기지 않음 |
| 서버 연결용 Skill | 설치된 연결 Skill은 `get_status` → `get_workflow`를 호출한다 | 도구 계약은 유지하고 서버가 반환하는 업무 지침만 통합 MD로 전환 |
| 로컬 파일 직접 조회 | 로컬 어댑터의 `scripts/knowledge.py`는 Rev가 붙은 MD·TXT를 각각 찾는다 | 서버 연결형과 구분해 호환 처리. 기존 로컬 설치까지 무변경 전환된다고 약속하지 않음 |
| 지식 동기화 | `product_knowledge.py`, `knowledge_push.py`, `knowledge_upload.py` | 기존 `qa_rules` 분류·해시·업로드를 재사용. 다른 제품의 TXT 지원은 유지 |

조사 당시 `get_workflow` 구현은 현재 master가 아니라 `codex/vxvue-claude-desktop` 작업트리의 `app/modules/vxvue_chatbot/desktop.py`에 있다. 현재 서버 도구도 이 호출을 제공한다. 구현 전에 운영 파일과 해당 브랜치를 대조하여 기존 연결 작업을 통합한다. master에 별도 연결 서버를 새로 만들지 않는다.

## 2. 권장 관리 방식

사람이 관리하는 원본은 `[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드.md` 한 파일로 한다. 파일명은 고정하고, 문서 안에 개정 번호·수정일·변경 요약을 기록한다.

개정 번호는 적용 판을 설명하는 정보다. `지원 Rev = 현재 Rev` 같은 허용 검사를 하지 않는다. 파일 내용의 SHA-256과 질문별 원본 판 번호로 어떤 지침을 적용했는지 추적한다. 같은 파일명의 내용만 바뀌어도 다음 정상 동기화 후 새 작업에 반영한다.

통합 MD 앞부분에는 운영 지침을, 뒤에는 상세 QA 규칙을 둔다. 기존 MD의 절 번호는 첫 전환에서 유지하여 기존 참조를 보존한다. TXT의 고유 규칙을 빠짐없이 옮기고, 중복은 대응표로 확인한 뒤 합친다. 프롬프트 요약을 원본 한 벌처럼 다시 관리하지 않는다.

통합 문서에는 형식 식별자 `<!-- qa-rules-format: unified-v1 -->`를 둔다. 이 값은 문서 구조를 구분하며 QA 문서의 Rev와 함께 올리지 않는다. 필수 운영 항목과 상세 규칙을 검사하여, 기존 상세 MD만 있는데 TXT가 사라진 상태를 통합 완료로 오인하지 않게 한다.

## Global Constraints

- 적용 배포 단위는 핵심 앱과 기존 서버 연결이다. `services/*`와 별도 DB는 변경하지 않는다.
- 원문 지침·TC·첨부·인증값은 공개 저장소에 커밋하지 않는다. 자동 시험은 합성 문서만 사용한다.
- 스킬 재등록·플러그인 재설치·연결 키 교체를 서버 연결형 사용자에게 요구하지 않는다.
- 서버 배포와 지식 업로드 전에는 운영 파일·문서 해시를 다시 읽고 다른 작업의 변경을 보존한다.
- 실행 중인 작업은 시작할 때의 원본 판을 계속 사용한다. 갱신 내용은 새 작업부터 적용한다.
- 문서 누락·읽기 실패·내용 충돌을 판 번호 고정 해제와 함께 없애지 않는다.
- 도구 권한, 결과 JSON 형식, 모델, 예약, 메일, 기존 TC 수행 결과는 바꾸지 않는다.

## Shared Interfaces

### Contract: QaRuleSource

`app/core/qa_rules.py`에 `resolve_rule_source(product: str, root: Path | None = None) -> QaRuleSource`를 추가하고 기존 로더들이 재사용한다.

| 필드 | 형식 | 뜻 |
|---|---|---|
| `mode` | `str` | `unified` 또는 `legacy` |
| `guide_path` | `Path` | 실제 읽을 MD |
| `prompt_path` | `Path | None` | 기존 두 문서 모드에서만 사용하는 TXT |
| `revision` | `str` | 문서 안 또는 기존 파일명에서 읽은 표시용 개정 |
| `source_hashes` | `dict[str, str]` | 선택한 파일 이름과 SHA-256 |

읽을 수 없는 필수 원문이나 같은 관리 원본의 충돌은 명확한 예외로 알린다. 정상 통합 MD가 있으면 기존 TXT를 필수로 요구하지 않는다. 통합 문서를 선언했지만 손상된 경우 구형 두 파일로 몰래 되돌아가지 않는다.

### Contract: 연결 도구

`get_status`, `get_workflow(snapshot_id, offset, limit)`의 기존 계약을 유지한다. 업무 규칙 본문은 통합 MD에서 공급하고, 도구 사용·인증·읽기 전용 경계는 기존 서버 연결 안내에 남긴다. MD를 `rules`와 별도 프롬프트 양쪽에 중복 삽입하지 않는다.

## Review Focus

| 입력·상태 | 기대 동작 |
|---|---|
| Rev가 커지거나 파일명에서 Rev가 없어짐 | 문서 내용이 유효하면 새 작업에서 사용. 판 고정 때문에 중단하지 않음 |
| 통합 MD와 오래된 MD·TXT가 함께 존재 | 통합 MD를 선택하고 옛 문서를 적용하지 않았다는 근거를 남김 |
| 통합 표시만 있고 운영 지침 누락·읽기 실패·원본 충돌 | AI 호출 전에 중단하고 이유 표시 |
| 실행 폴더 재사용·규칙 교체·동기화 실패 | 옛 TXT나 이전 규칙이 새 원문에 섞이지 않음. 이전 정상 서버 판은 실패 표시와 함께 보존 |
| 다른 제품·구형 로컬 직접 조회 | 기존 두 문서 제품은 유지. 로컬 직접 조회의 호환성은 별도 확인하고 서버 검증으로 대신하지 않음 |

## 3. 구현 순서

### Task 1: 사양과 원문 통합안 확정

**Files:** `SPEC.md`, `specs/vxvue-chatbot.md`; 사내 원문은 기존 지식 관리 위치에서 작업하고 별도로 백업한다.

- [ ] REQ-DAILY-010을 VXvue 최신 원문 선택·검사 방식으로 바꾸고, 다른 제품의 명시적 판 고정은 보존한다.
- [ ] REQ-DAILY-023·024와 REQ-VXVUECHATBOT-005·007·008의 두 파일 필수·고정 절 참조를 전환 방식에 맞춘다.
- [ ] TXT의 각 규칙을 통합 MD의 위치에 대응시켜 누락·우선순위 충돌을 검토한다. 기존 MD 절과 Gate·TC 열·완료 결과 보호 규칙을 보존한다.
- [ ] 이번 대화에서 확인한 다섯 보완을 통합한다: 요청별 검증 범위, 원인 기반 영향범위, 첨부 확인 수준, 화면 필드 구분, 수행 범위에 맞춘 종료 코멘트.
- [ ] 새 이슈의 분석·TC 요청을 이전 기록용 대표 TC로 자동 축소하지 않는다. 대표 TC 요청은 명시된 범위에 맞춰 작성하고 전체 검증 완료로 확대하지 않는다.
- [ ] 영상은 확인한 구간·프레임과 전체 재생을 구분한다. 오류 문구·조작 순서·버전이 다른 첨부를 분리하고, UI·전송 결과·로그·개발 원인 기록을 별도 근거로 다룬다.

### Task 2: 공통 선택과 판 고정 해제

**Files:** `app/core/qa_rules.py`, `app/modules/daily_qa/rules.py`, `config/products/vxvue.yaml`, `tests/test_qa_rules.py`, `tests/test_daily_qa_runner.py`, `tests/test_qa_intel_products.py`.

- [ ] 먼저 `test_unified_md_without_txt_is_selected`, `test_unified_md_beats_legacy_pair`, `test_invalid_unified_does_not_fall_back`, `test_vxvue_future_revision_does_not_block`, `test_explicit_pin_other_product_is_preserved`를 추가하고 기존 코드에서 실패하는지 확인한다.
- [ ] 위 Contract의 선택기를 구현한다. 파일 수정 시각만으로 충돌하는 통합 문서를 고르지 않는다.
- [ ] VXvue의 `supported_rev` 고정을 해제한다. 설정 키나 다른 제품의 선택권을 전역 삭제하지 않는다.
- [ ] 같은 원문을 `load_rule_set()`, 사전 검사, 챗봇이 사용하도록 연결하고 위 시험을 통과시킨다.

### Task 3: 자동 QA의 한 문서 실행 경로

**Files:** `app/modules/daily_qa/workspace.py`, `agent_runner.py`, `rule_context.py`, 공통·VXvue 제품 규칙 Skill, `guide-sections.json`; 관련 시험은 `tests/test_daily_qa_runner.py`, `tests/test_qa_intel_token_saving.py`.

- [ ] `test_unified_workspace_removes_stale_prompt`, `test_unified_rules_are_inlined_once`, `test_rule_lookup_survives_heading_renumbering`을 먼저 추가한다.
- [ ] 통합 모드는 `rules/qa-guide.md` 하나만 복사한다. 기존 두 파일 모드는 다른 제품에 유지한다.
- [ ] 제품 규칙 Skill의 고정 Rev 문구를 없애고 문서 본문을 기준으로 읽게 한다. 절 번호는 탐색 힌트로만 사용하며 제목·기능으로 다시 찾는 경로를 검증한다.
- [ ] `references/gates.md`의 고정 개정·절 번호 안내도 문서의 현재 검증 관문을 찾는 안내로 바꾼다. 문서의 판정 기준을 현재 결과 형식으로 표현할 수 없는 경우에만 구체적인 충돌을 알리고 판정을 보류한다. Rev가 달라졌다는 이유만으로 보류하지 않는다.
- [ ] 역할·우선순위·질문·원본 보호 등 공통 운영 규칙은 필요한 상세 절과 함께 전달한다. 절 발췌 때문에 앞부분 운영 규칙이 빠지지 않게 한다.
- [ ] AI 없는 실행 준비로 규칙 파일·미리 전달한 입력·캐시 지문을 읽어 새 문서가 적용되고 중복 주입되지 않는지 확인한다.

### Task 4: 챗봇과 기존 서버 연결 호환

**Files:** `app/modules/vxvue_chatbot/knowledge.py`, `agent.py`, `skill/SKILL.md`; 서버 연결 브랜치의 `desktop.py`와 관련 시험. 재사용 시험은 `tests/test_vxvue_chat_knowledge.py`, `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py`.

- [ ] `test_unified_snapshot_needs_no_txt`, `test_same_filename_edit_changes_snapshot`, `test_old_snapshot_stays_immutable`, `test_workflow_pagination_returns_unified_rules_once`를 먼저 추가한다.
- [ ] 통합 MD만으로 질문 원본 판을 준비한다. 이전 질문은 이전 규칙을 유지하고 새 질문은 새 해시를 사용한다.
- [ ] 기존 bridge 1.2.1의 도구 계약·키를 바꾸지 않고 `get_workflow`에서 전문을 이어 읽을 수 있는지 확인한다.
- [ ] 재등록하지 않은 기존 연결로 상태 조회·규칙 끝까지 읽기·합성 질문의 새 지침 적용을 확인한다. 구형 PC 직접 조회는 사용자 요청으로 운영 범위에서 제외한다.

### Task 5: 동기화와 기존 사용자 전환

**Files:** `app/core/product_knowledge.py`, `app/core/knowledge_push.py`, `app/core/knowledge_upload.py`, 필요 시 지식 상태 표시; `tests/test_product_knowledge.py`, `tests/test_knowledge_upload.py`, `tests/test_knowledge_profiles.py`를 확장한다. 업로드 송신 시험도 `test_knowledge_upload.py`에 있다.

- [ ] `test_unified_sync_preserves_other_products`, `test_failed_unified_upload_keeps_previous_snapshot`, `test_archived_legacy_rules_are_not_reingested`를 먼저 추가한다.
- [ ] 수집·업로드 경로를 통합 MD에 연결한다. 구형 파일을 삭제하기 전에 해시·대응표와 복구본을 남긴다.
- [ ] 백업은 재귀 수집되는 지식 루트 밖에 둔다. 구형 TXT와 MD를 운영 입력에서 제외하되 다른 제품·일반 문서는 유지한다.
- [ ] 구형 PC 직접 조회 의존성을 제거하고 짧은 로컬 호출명은 서버형 Skill로 연결한다. 구형 TXT 호환 사본은 생성하지 않는다.
- [ ] 먼저 읽기 코드, 다음 통합 MD를 적용한다. 운영 서버의 추가 변경·실행 중 작업·문서 해시를 확인한 뒤 전환한다. 문제 시 코드와 문서를 짝으로 이전 상태로 되돌린다.

### Task 6: 검증·문서·완료

- [ ] 관련 규칙·지식·챗봇·자동 QA 시험을 먼저 통과시키고, 구현 범위에 맞춰 루트 전체 `pytest`를 실행한다.
- [ ] AI 없는 대표 준비 경로, 기존 연결의 실제 조회 경로, 한 번의 새 질문을 구분해 확인한다. 정적 검사나 테스트 성공을 실제 답변 품질 검증으로 표시하지 않는다.
- [ ] `CHANGELOG.md`, `progress.md`, 필요한 사용자 문서와 `docs/README.md`를 갱신한다. 사양 변경 시 `node .project-check/render-spec-html.js .`로 HTML을 다시 만든다.
- [ ] 키트의 완료 정리 후 `node .project-check/project-readiness.js .`를 실행한다. 관계없는 변경을 포함하지 않고 커밋·푸시한다.

## 4. 완료 조건과 이번 작업의 경계

완료 조건은 원본 MD 한 번의 수정·정상 동기화만으로 기존 서버 연결과 자동 QA의 다음 작업에 반영되고, Rev 변경을 이유로 설정·스킬을 다시 배포하지 않아도 되는 것이다. 어떤 원문을 읽었는지는 문서 해시와 적용 판으로 남긴다.

사용자 승인에 따라 서버형 구현을 진행한다. 전환 전 원본을 보관하고 현재 서버의 별도 변경과 예약 설정은 보존한다. 자세한 적용·검증 상태는 progress.md를 따른다.

## 5. 실행 결과

위 체크리스트는 구현 전 시험 후보를 포함한다. 실제 시험 이름과 완료 근거는 다음 표와 progress.md를 기준으로 한다.

| 작업 | 결과 | 근거 |
|---|---|---|
| 규칙 통합 | 적용 | 비공개 원문 129개 상세 제목 유지, 운영 15절 추가, 이전 MD·TXT 별도 백업 |
| 공통 선택·판 고정 해제 | 적용 | tests/test_unified_qa_rules.py, tests/test_daily_qa_runner.py |
| 자동 QA 입력 | 적용 | 운영 절·검증 모드 TC·동일 하위 제목 보존 시험, 실제 원문의 AI 없는 실행 준비 |
| 기존 서버 연결 | 적용 | 기존 bridge 1.2.1로 전문 6페이지 읽기, update_required=false |
| 동기화·전환 | 적용 | 운영 서버 신규 판 생성, 기존 일반 자료 19개 유지, 예약용 내보내기 해시 일치 |
| 답변 품질 | 미실행 | 서버 AI 새 질문은 호출하지 않음. 규칙 전달 검증과 제품 검증·TC 품질 평가는 구분 |

원래 계획의 별도 시험 함수명은 공통 실패 차단·이전 판 보존 시험과 기존 수집·업로드 시험으로 묶어 검증했다. 배포 및 PC 변경은 백업 후 필요한 파일만 적용했다.
