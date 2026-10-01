---
name: qa-trace-gap
description: 일일 점검 E 단계(사양–TC 추적 공백)가 코드로 계산한 "TC 없음" 목록을 사람이 요청했을 때 후속 확인한다 — Legacy 번호가 달라 번호로 못 찾은 TC 를 제목·기능명으로 찾아 준다. 무인 실행에서는 쓰지 않는다.
---

# E 후속 — 추적 공백 확인 (대화형)

E 단계 자체는 AI 없이 코드로 계산한다 (`app/modules/daily_qa/packages.py` 의 `trace_gaps`).
이 Skill 은 QA 가 "이 SRS 가 정말 TC 가 없는지 봐 줘" 라고 요청할 때만 쓴다.

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.**

## 왜 필요한가

TC 파일은 옛 Legacy SRS 번호를 쓰는 경우가 많다. 2026-09-28 실데이터에서 TC 가 쓰는 Legacy 번호
317종 가운데 239종이 현재 Polarion `oldId` 와 맞지 않았다. 그래서 번호로만 찾으면 실제로는 TC 가
있는데 `TC 없음` 으로 나오는 SRS 가 생긴다.

## 절차

1. 대상 SRS 의 제목·본문 핵심어·메뉴 경로를 뽑는다.
2. `context/tc_index.jsonl`(또는 사람이 준 TC 파일)에서 Title·Step 에 그 말이 있는 TC 를 찾는다.
3. 찾은 TC 가 그 SRS 의 동작을 실제로 검사하는지 가이드 §3.3·§61.1 기준으로 본다.
4. 결과를 대화로 알려 준다: `TC 있음(번호만 다름)` + TC 위치, 또는 `TC 없음 확인` + 찾아본 검색어.
   TC 의 SRS 번호 열을 고치라고 제안할 수는 있지만 직접 고치지 않는다.
