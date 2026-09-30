# 매뉴얼 개정 검증 (`/manual-review`)

> 코드: [`app/modules/manual_review/`](../../app/modules/manual_review)
> · 앱 안 사용법: `/manual-review/guide`
> · 상위 문서: [README](../../README.md) · [문서 지도](../README.md)

연구소가 제출한 Word Track Changes(`.docx`) 개정 Manual이 최신 SRS(= Regression 영향 분석이
이미 동기화하고 있는 등록 사양서)를 정확히 반영했는지 AI로 1차 검토한다.

## 처리 흐름

```text
개정 Manual (.docx Track Changes / .pdf)
  ↓ 구조화 추출 — 삽입·삭제·이동을 항목 단위로 분해 (서식 변경은 뽑지 않는다)
Track Changes 추출
  ↓ Rule 필터 — 페이지 번호, 저작권 표기, 목차 리더 점선 등은 AI 대상에서 제외
NON_FUNCTIONAL_CHANGE 제거
  ↓ 로컬 BM25 — 이 변경과 관련된 SRS 근거만
SRS 근거 검색
  ↓ Release Note · 설계검토보고서 Scope 대조
범위 대조
  ↓ 같은 제품의 다른 매뉴얼과 BM25 대조
Cross-Manual 영향 추적
  ↓ quick 판정 → PASS면 종료, 의심되면 detail 판정
2단계 AI 판정
  ↓ QA Override 가능
결과 화면 → Word Comment 삽입 DOCX 다운로드
```

## 사람이 반드시 확인하게 만드는 지점

이 기능의 핵심 설계는 "AI가 판단할 수 없는 것을 AI가 판단하지 않게 하는 것"이다.

**이미지 변경 Human Review Gate.** DOCX Track Changes 안의 삽입·삭제된 drawing·pict와 PDF
페이지 이미지의 SHA-256 hash 변화를 감지하면 `IMAGE_CHANGE_REVIEW_REQUIRED`로 강제
표시한다. 이미지 변경은 텍스트만으로 의미를 판단할 수 없으므로, AI가 PASS로 판정해도
판정 값을 `UNABLE_TO_DETERMINE`(판정 불가)으로 바꾸고 원래 판정은 `ai_original_decision`에
남긴다. 이 변경은 Word Comment 파일에도 들어가므로 사람이 원본 이미지를 직접 확인하게 된다
(`review_signals.py`).

**PDF diff는 confidence 상한을 둔다.** 첫 PDF를 Baseline으로 등록하고 다음 PDF부터 이전
PDF와 페이지별 텍스트 추가·삭제·수정을 비교한다. 위치·레이아웃 해석 오차를 감안해
confidence를 최대 60%로 제한하고 `PDF_DIFF_REVIEW_REQUIRED`를 표시하며, PDF에는 Word
Comment를 생성하지 않고 QA가 결과 화면에서 최종 판정한다.

**Cross-Manual 영향은 후보로만 표시한다.** 같은 제품의 다른 매뉴얼을 이번 Release·설계
변경과 BM25로 대조해 관련 있어 보이는 항목을 `REVIEW_REQUIRED` 후보로 표시한다. 자동
확정하지 않고, QA가 결과 화면에서 확인 필요 / 영향 있음 / 영향 없음으로 직접 확정한다
(`cross_manual.py`).

대조 대상 매뉴얼은 세 곳에서 이 순서로 모은다.

1. 이 앱에 등록된 최신 매뉴얼 리비전 — 지금 검증 흐름에 직접 올라온 것
2. **매뉴얼 서버(`services/qa-manual-hub`)의 Current 버전** — 조직이 최신본으로 인정한 문서
3. 등록된 Knowledge 문서 중 이름이 매칭되는 것 — 하위 호환 소스

같은 매뉴얼 이름이 여러 곳에 있으면 앞 순서가 이긴다.

**이전 Round 지적사항의 상태는 자동으로 바꾸지 않는다.** 지적사항은 Word Comment 삽입
DOCX를 만들 때 넣은 Comment마다 저장한다. 연구소에 보낸 지적과 다음 회차에 확인할 지적이
같아지게 하기 위해서다. 같은 리비전으로 파일을 다시 만들면 이미 저장한 변경(변경 번호 기준)은
건너뛴다. Round 계보를 추적하며 이전 지적사항에 대해 로컬 유사도 기반 참고 판정을 제공하지만,
QA가 해결 / 미해결 / 재오픈 / 제외를 확정하기 전에는 상태를 변경하지 않는다.

**참고 문서는 같은 제품 버전 것만 재사용한다.** Release Note·설계검토보고서를 올리지 않으면
같은 제품 버전으로 등록된 가장 최근 문서를 쓰고, 없으면 Release 대조를 건너뛴다. 다른 버전
문서로 대조하면 누락 의심이 대량으로 틀리기 때문이다. 올린 문서는 버전 칸에 제품 버전을,
리비전 칸에 리비전 표기를 넣어 등록한다.

## 비용 관점의 설계

- Rule Engine이 `NON_FUNCTIONAL_CHANGE`를 먼저 걷어내므로 AI 호출 대상 자체가 줄어든다
  (`change_filter.py::is_functional_change`).
- quick 판정이 PASS면 비용이 큰 detail 호출을 생략한다 (PASS short-circuit).
- 근거 SRS 후보는 `manual_review.max_srs_candidates`(기본 6)건으로 제한한다.
- 오늘 쓴 토큰이 하루 한도(`analysis.daily_token_limit`)를 넘으면 새 검증 요청을 429로 거절한다.
  다른 기능과 같은 공용 계산을 쓴다.

자세한 내용은 [비용 절감 설계](../COST_OPTIMIZATION.md).

## 결과물

| 산출물 | 내용 |
|---|---|
| 결과 화면 | 항목별 판정·근거·Confidence, QA Override |
| Word Comment 삽입 DOCX | 판정 결과를 원본 문서에 코멘트로 삽입해 연구소에 회신 |
| Round 이력 | 이전 Round 지적사항과 현재 Round의 계보 |
| Claude 대화 검증 결과 | `output/manual_review_local/<매뉴얼>_<실행 시각>/` 의 Excel·Comment DOCX·`comments.json` (아래 절) |

## 관련 설정 (`config.yaml`)

| 키 | 기본값 | 의미 |
|---|---|---|
| `manual_review.max_srs_candidates` | 6 | 근거 SRS 후보 개수 상한 |
| `services.manual_hub.api_url` | 통합 배포 기준 주소 (`config.yaml` 참고) | 매뉴얼 서버 API 주소. 비우거나 비밀 값 두 개(`MANUAL_HUB_USER`, `MANUAL_HUB_PASSWORD`) 중 하나라도 없으면 연동 비활성 |
| `storage.manual_hub_cache_dir` | `data/manual_hub_cache` | 매뉴얼 서버에서 받아 온 대조용 사본 |
| `storage.manual_revision_dir` | `data/manual_revisions` | 개정 Manual 원본 보관 |
| `storage.manual_review_comment_dir` | `output/manual_review_comments` | Comment 삽입 DOCX 산출 |

프롬프트는 `app/prompts/manual_revision_quick.yaml`, `manual_revision_detail.yaml` 에 버전과
함께 들어 있다.

## 매뉴얼 서버 연동 (선택)

매뉴얼 서버에 보관된 Current 매뉴얼을 Cross-Manual 대조에 자동으로 끌어온다. 켜면 다른
매뉴얼을 이 앱에 따로 올리지 않아도 된다.

**켜는 방법** — 셋이 모두 있어야 동작한다. 하나라도 없으면 연동은 꺼진 채 기존 동작을
그대로 유지한다.

1. 매뉴얼 서버에서 **읽기 전용 용도의 일반 User 계정**을 하나 만든다 (Admin 금지).
2. `secrets.txt`에 `MANUAL_HUB_USER` / `MANUAL_HUB_PASSWORD`를 넣는다.
3. `config.yaml`의 `services.manual_hub.api_url`에 API 주소를 넣는다
   (통합 배포 기준: `http://127.0.0.1/manual-hub/api`).

**경계** — 두 시스템은 프로세스도 DB도 공유하지 않는다. 이 연동은 **HTTP API만** 사용하며
매뉴얼 서버의 코드를 import 하거나 PostgreSQL·문서 저장소를 직접 읽지 않는다
(`app/core/manual_hub_client.py`).

**장애 격리** — 매뉴얼 서버가 내려가 있거나 로그인에 실패해도 이 단계만 건너뛰고 검증은
계속된다. 저장소를 합친 대가로 장애가 전파되면 안 되기 때문이며, 테스트로 고정해 두었다
(`tests/test_manual_hub_client.py`, `tests/test_cross_manual.py`).

## Claude 대화로 돌리기 (REQ-MANUAL-018)

변경마다 판정을 보며 바로 고쳐야 하므로 예약 실행 대신 대화로 돌리는 방법도 있다. 이 저장소에서
Claude Code를 열고 "이 경로의 매뉴얼 분석해 줘"라고 말하면 작업 설명서
`.claude/skills/vxvue-manual-revision-review/SKILL.md`가 순서를 정한다.

```text
extract (scripts/manual_review_local.py) -> changes.json · decisions.json 틀
 -> Claude 판정 · QA 수정 요청 반영 (decisions.json)
 -> finish -> manual_review.xlsx · <매뉴얼>_QA_Comment.docx · comments.json
```

- 도구는 외부 AI를 부르지 않는다. 변경 추출·단순 변경 거르기·SRS 근거 찾기·Release 대조·
  Comment 쓰기는 이 모듈 코드를 그대로 쓴다. 사양서는 지식 사본(`data/product_knowledge/<제품>/`)에서 읽는다.
- `finish`는 판정 값이 목록에 없거나, 변경 번호·근거 번호가 `changes.json`에 없으면 종료 코드 1로 멈춘다.
  이미지 변경의 PASS는 화면 검증과 같은 규칙으로 판정 불가로 바뀐다.
- 결과는 핵심 앱 DB에 저장하지 않는다. `output/manual_review_local/`은 git에 올리지 않는다.
