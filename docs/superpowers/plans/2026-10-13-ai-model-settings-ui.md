# AI 모델·추론 정도 화면 설정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 운영 담당자가 서버에 SSH 터널로 접속해 화면에서 AI 모델 등급별 모델과 추론 정도(effort)를 바꾼다. 바꾼 값은 다음 AI 호출부터 모든 기능(화면 AI 기능, 일일 QA 점검, 현재 상태 점검)에 적용된다. 외부 화면은 읽기 전용이다.

**Architecture:** `config.yaml` 은 기본값으로 두고, 화면에서 바꾼 값은 DB 의 설정 덮어쓰기 표에 저장한다. `Settings.get()` 이 허용 목록에 있는 키에 한해 덮어쓰기 값을 먼저 돌려준다. 일일 QA 점검은 별도 프로세스라 실행이 시작될 때 DB 를 읽으므로 그다음 실행부터 바뀐다. 진행 중인 실행은 바뀌지 않는다.

**Tech Stack:** Python 3, FastAPI + Jinja2, SQLite, pytest.

**Spec:** `SPEC.md` 5.5절 새 요구사항 REQ-AICALL-007(설정 화면). REQ-AICALL-004(모델 등급), REQ-AICALL-005(Claude CLI), REQ-DAILY-016 순서 5(일일 QA 의 모델·추론 정도), REQ-WEB-004(설정 상태)와 연결된다.

## 현재 상태 (2026-10-13)

| 사용처 | 읽는 설정 | 현재 값 |
|---|---|---|
| 화면 AI 기능(QA Agent·매뉴얼 개정 검증) | `ai.claude.models.<등급>`, `ai.claude.effort` | `claude-opus-5-5` ×3, `medium` |
| 일일 QA 점검 분석 작업 | `ai.claude.models.standard`, `ai.claude.effort` | `claude-opus-5-5`, `medium` |
| 현재 상태 점검 작업 | `daily_qa.intelligence.audit_model`(비면 `ai.claude.models.light`), `ai.claude.effort` | `claude-opus-5-5`, `medium` |

설정은 `get_settings()` 가 `config.yaml` 을 한 번 읽어 캐시한다(`app/core/config.py`). `POST /config/reload` 가 캐시를 비운다.

## 설계 결정

### 1. 저장 위치: DB 표 (`config.yaml` 직접 수정 안 함)

- `config.yaml` 은 Git 이 관리하고 `scripts/deploy.ps1` 이 서버에 덮어쓴다. 화면에서 고친 값을 여기에 쓰면 다음 배포에 사라진다.
- 새 표 `app_setting_overrides(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)` 를 `data/app.db` 에 둔다. `data/` 는 배포 대상이 아니고 백업(`scripts/backup_data.py`)에 들어간다.
- 바꾼 기록은 별도 표 `app_setting_changes(id, key, old_value, new_value, changed_at, client)` 에 쌓는다. `client` 는 요청 IP 다(로그인 없음, 아래 5).

### 2. 바꿀 수 있는 키 (허용 목록)

| 키 | 화면 이름 | 고를 수 있는 값 |
|---|---|---|
| `ai.claude.models.light` | 가벼운 작업 모델 | 허용 모델 목록 |
| `ai.claude.models.standard` | 기본 작업 모델 (일일 QA 분석) | 허용 모델 목록 |
| `ai.claude.models.complex` | 복잡한 작업 모델 | 허용 모델 목록 |
| `ai.claude.effort` | 추론 정도 | `low` · `medium` · `high` · `xhigh` · `max` · 빈 값(CLI 기본) |
| `daily_qa.intelligence.audit_model` | 현재 상태 점검 모델 | 허용 모델 목록 · 빈 값(가벼운 작업 모델 따름) |

- 허용 모델 목록은 `config.yaml` 새 키 `ai.claude.allowed_models`(기본 `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-fable-5-1`)에서 읽는다. 목록에 없는 값은 저장을 거절한다.
- 허용 목록 밖의 키는 화면·API 어디서도 바꿀 수 없다. 비밀값·경로·포트는 대상이 아니다.

### 3. 적용 시점

- 저장하면 `reload_settings()` 로 웹 프로세스의 캐시를 비운다. 다음 화면 AI 호출부터 새 값이다.
- 일일 QA 점검·현재 상태 점검은 실행이 시작될 때 설정을 읽는다. 진행 중인 실행은 시작 때 값을 끝까지 쓴다. 화면에 "다음 실행부터 적용" 을 표시한다.
- 실행 기록(`audit.json`)에 그 실행이 쓴 모델·추론 정도를 남겨, 바꾸기 전후 결과와 사용량을 비교할 수 있게 한다.

### 4. 화면

- 위치: `/settings/ai` 새 화면. 허브 카드와 비용 대시보드(`/cost-dashboard`) 상단에서 연결한다. 기존 비용 화면과 같이 보면 모델 바꾼 효과를 바로 비교할 수 있다.
- 표 한 줄에 키마다 `화면 이름 · 지금 값 · 출처(기본값/화면에서 바꿈) · 고르는 칸` 을 보인다. [저장], [기본값으로 되돌리기] 버튼과 최근 바꾼 기록 10건을 보인다.
- 실행 중인 일일 QA 가 있으면 "진행 중인 실행 <ID> 는 이전 값으로 끝까지 돈다" 를 보인다.

### 5. 접근 제한 (사용자 결정 완료: 서버 안에서만 저장)

핵심 앱에는 로그인이 없다(OPEN_QUESTIONS 1). 아래 안을 비교한 뒤 사용자가 (나)를 선택했다. TCP 접속 상대가 loopback이고 프록시 전달 헤더가 없을 때만 저장한다. nginx를 거친 요청은 읽기 전용이며 SSH 터널은 앱 포트에 직접 연결한다.

| 안 | 내용 | 비고 |
|---|---|---|
| (가) 관리 토큰 | `secrets.txt` 의 `ADMIN_SETTINGS_TOKEN` 과 같은 값을 화면에 넣어야 저장 | 구현 작음. HTTP 평문이라 토큰이 네트워크에 보인다 |
| (나) 서버 안에서만 | 요청 IP 가 `127.0.0.1` 일 때만 저장, 나머지는 읽기 전용 | 서버에 SSH 터널로 들어와야 바꿀 수 있다 |
| (다) 제한 없음 + 기록 | 누구나 저장, 바꾼 기록(IP·시각)과 메일 알림 | 가장 편하다. 사내망 전제 |

결정: (나)를 구현한다. 관리 토큰을 두지 않는다.

## Tasks

### Task 1: SPEC 먼저

- [x] `SPEC.md` 5.5절에 `REQ-AICALL-007 화면에서 AI 모델·추론 정도 바꾸기` 카드(하는 일·언제·순서·결과·설정·안 될 때·지킬 것)를 더한다. 5절 기능 그룹 표와 12절 추적성 행, 11절 `TEST-AICALL-006` 을 더한다.
- [x] REQ-AICALL-004·005, REQ-DAILY-016 의 설정 표에 "화면에서 바꾼 값이 이긴다(REQ-AICALL-007)" 한 줄을 더한다.
- [x] `node .project-check/render-spec-html.js .`

### Task 2: 덮어쓰기 저장소와 `Settings.get()`

**Files:** `app/core/config.py`, `app/core/setting_overrides.py`(새), `app/core/storage.py`(표 생성), `tests/test_setting_overrides.py`(새)

- [x] 실패 테스트: 허용 키 덮어쓰기가 `Settings.get()` 에 보인다. 허용 밖 키는 무시한다. 허용 모델 밖 값·잘못된 effort 는 `ValueError`. 되돌리면 `config.yaml` 값으로 돌아온다. 바꾼 기록이 쌓인다.
- [x] 구현: `build_settings()` 가 DB 덮어쓰기를 읽어 `Settings.overrides` 에 넣고, `get()` 이 허용 키일 때 먼저 본다. DB 가 없거나 읽기 실패면 `config.yaml` 값만 쓰고 경고 로그를 남긴다(앱 시작을 막지 않는다).
- [x] 일일 QA 별도 프로세스(`scripts/run_daily_qa.py`)도 같은 `get_settings()` 를 쓰는지 테스트로 확인한다.

### Task 3: API

**Files:** `app/web/settings_router.py`(새) 또는 `app/main.py`, `tests/test_settings_api.py`(새)

- [x] `GET /settings/ai/values` — 키별 지금 값·기본값·출처·고를 수 있는 값.
- [x] `POST /settings/ai` — `{key: value}` 묶음 저장. 하나라도 틀리면 전부 거절(422). 저장 뒤 `reload_settings()`.
- [x] `POST /settings/ai/reset` — 지정 키 덮어쓰기 삭제.
- [x] 접근 제한(설계 5에서 고른 안)과 거절 테스트.

### Task 4: 화면

**Files:** `app/web/templates/settings_ai.html`(새), 허브 내비게이션, `app/modules/cost_dashboard/` 링크, `tests/test_settings_screen.py`(새)

- [x] 표·고르는 칸·저장·되돌리기·바꾼 기록·"다음 실행부터 적용" 안내.
- [x] 390px·1920px 에서 가로 스크롤 없음(REQ-WEB-006).

### Task 5: 실행 기록에 모델 남기기

**Files:** `app/modules/daily_qa/pipeline.py`, `app/modules/qa_agent/run_view.py`

- [x] `audit.json` 과 실행 상세 화면에 분석 모델·점검 모델·추론 정도를 보인다.

### Task 6: 검증과 마무리

- [x] 전체 pytest, `node .project-check/project-readiness.js .` (1341 passed·7 skipped, 준비 검사 오류 0건)
- [x] 운영 서버 반영 뒤 [지금 실행]의 실제 Claude 프로세스 인수에서 `--model claude-opus-5-5 --effort medium`을 확인했다. 담당자 PC 테스트 서버는 `127.0.0.1:24357`이다. 격리 서버에서는 저장·적용·복구와 390px·1920px 화면을 확인했다.
- [x] `CHANGELOG.md`(REQ-AICALL-007), `progress.md`, `docs/README.md` 문서 지도, 사용법 화면 갱신. 커밋·push(`b001827`, `codex/qa-followup-20261013`).

## 확인 필요

- 접근 제한은 서버 안에서만 저장으로 확정했다.
- 허용 모델 목록은 계획의 네 개로 구현했다. 실제 선택한 모델의 사용 가능 여부는 실행 결과로 확인한다.
- 이번 운영 배포에 함께 반영한다. 최신 검증·운영 확인 결과는 `progress.md`에 남긴다.
