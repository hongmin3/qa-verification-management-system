---
name: qa-issue-spec-audit
description: 이슈 변경 기록이 없는 기간에, 지금 이슈 상태가 그 기간의 SRS 변화와 맞는지 이슈마다 판정한다. 입력 파일(runs/<실행ID>/in/AUD-*.json)을 받았을 때 쓴다. 요약 카드만 보고 빠르게 판정하고, 깊은 분석은 사람이 단일 이슈 분석으로 이어 간다.
---

# 이슈 정합성 점검 (현재 상태 기준)

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.** QA 규칙 원문(`rules/qa-guide.md`)은 전체를 읽지
않는다. 아래 절만 찾아 읽는다: §6(Issue 산출물 유형), §7(정보 우선순위), §10(분할 사양서 전수조사),
§11(사양 유효 상태), §38(연구소 Review 재검토), §43(Issue 종료 후 TC 정합성).

## 입력

`in/AUD-*.json`

- `basis`: "현재 상태 기준(기간 이력 없음)". 이슈가 기간 동안 어떻게 바뀌었는지는 모른다.
- `srs[]`: 이 작업의 SRS. `id`, `title`, `status`, `changed`(기간 안에 바뀌었는가), `added_sentences`,
  `removed_sentences`, `excerpts`(이슈 내용에 가까운 지금 SRS 문단), `found_by_search`(연결이 없어 검색으로 찾은 후보).
- `tcs[]`: 수정 완료 이슈가 있는 작업에만 있다. 그 SRS 를 가리키는 TC 후보.
- `items[]`: 이슈마다 하나. `target`(이슈 번호), `category`, `signals`, `srs_ids`, `issue`(요약 카드:
  제목·상태·연구소 결과·생성일·수정일·Expected·Actual·발생 원인·조치 내용·마지막 댓글 2개·`reference_only`).

| `category` | 볼 것 |
|---|---|
| `A-수정` | 수정 뒤 사양이 바뀌었다. 이슈의 Expected 와 관련 TC 가 새 사양과 맞는가(§43) |
| `A-사양` | 바뀐 사양이 연구소의 Spec / Not Bug 판정을 뒷받침하는가 |
| `A-기타` | 바뀐 사양이 이슈의 Expected 와 맞는가 |
| `B` | 사양은 그대로다. 지금 사양이 연구소 판정을 뒷받침하는가. `signals` 에 "판정 뒤 SRS 가 바뀌지 않음"이 있으면 연구소가 "SRS 수정 완료"라고 적었는지 특히 본다(§11) |
| `D` | 기간 안 신규 이슈. 검색으로 찾은 사양 후보와 맞는가 |

## 절차

1. 이슈마다 Expected·Actual·연구소 결과를 SRS 의 바뀐 문장과 문단에 비춰 본다.
2. `verdict` 를 고른다.

   | 값 | 뜻 |
   |---|---|
   | `CONSISTENT_WITH_SPEC` | 일치. 지금 사양이 이슈의 Expected·판정을 뒷받침한다 |
   | `PARTIALLY_CONSISTENT` | 부분 일치. 일부만 맞거나 조건이 다르다 |
   | `CONTRADICTS_SPEC` | 충돌. 지금 사양과 이슈의 Expected·판정이 어긋난다 |
   | `INSUFFICIENT_EVIDENCE` | 근거 부족. 받은 문단만으로 판단할 수 없다 |

3. `A-수정` 이슈는 `sections.tc_impact` 를 쓴다. `decision` 은 `유지` / `경미 수정` / `수정 필수` /
   `Issue Link 수정` / `신규 TC 필요` / `사양 확인 필요` 가운데 하나, `tc_ids` 는 입력 `tcs[]` 의 번호만.
4. `summary` 는 한두 문장으로, 무엇이 맞고 무엇이 어긋나는지 쓴다. `detail` 에 본 문장과 문단을 적는다.

## 지킬 것

- `reference_only` 가 참인 이슈(검증 전 상태)는 Expected 근거로 쓰지 않는다. 판정은 하되 `detail` 에
  "검증 전 상태라 참고만"을 적는다(지침 §6).
- 연구소 댓글·조치 내용만으로 `CONSISTENT_WITH_SPEC` 을 내지 않는다(§7).
- 받은 문단만 봤다는 것을 잊지 않는다. "사양 없음", "전수조사 완료"라고 쓰지 않는다(§10). 판단에 더
  필요한 문단이 있으면 `INSUFFICIENT_EVIDENCE` 로 두고 `detail` 에 무엇이 더 필요한지 적는다.
- 취소선·Deleted·Deprecated 문장은 Expected 근거로 쓰지 않는다(§11).
- 기간 동안 이슈가 어떻게 바뀌었는지 추측하지 않는다. 지금 상태만 본다.

## Finding

- 이슈마다 하나. `subject` = `target`. TC 초안을 만들지 않는다.
- `evidence` 는 `source_type: "srs"` 에 `location` 으로 입력의 SRS 번호(예: `VP-767 / 바뀐 문장 2`)를, TC 는 `source_type: "tc"` 에 입력 `tcs[]` 의 위치를 쓴다.
- `gate_status`: G1, G2, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.
