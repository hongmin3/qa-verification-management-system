# QA 검증 관리 시스템 — QA Intelligence Agent 기능 사양

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `qa-intelligence` |
| Document Version | 0.1.0 |
| Last Updated | 2026-09-30 |
| Status | draft |

이 문서는 `qa-verification-management-system`의 **QA Intelligence Agent** 기능이 어떻게 동작해야 하는가를 정의한다.

공통 사양(시스템 구성·전체 흐름·비기능·데이터·설정·오류·보안)은 상위 `SPEC.md`에 있고 여기서 되풀이하지 않는다.
절 번호는 상위 문서와 같은 뜻이다. 빠진 번호(3·4·6~10)는 공통 사양이라 상위 문서에 있다.

파일 경로는 모두 프로젝트 루트 기준으로 적는다(`specs/` 기준이 아니다). 검사기도 루트 기준으로 확인한다.

> **참고** 처음 읽는다면 1절 "이 기능은"과 흐름도, 5절 "요구사항 지도"를 먼저 본다. 그다음 필요한 카드만 읽는다.
> 이 기능은 상위 SPEC 5.4절 "VXvue 일일 QA 점검"을 바탕으로 한다. 수집·잠금·작업 폴더·메일 같은 공통 부분은 그 절의 카드를 그대로 따른다.

## 1. 목적

QA 담당자가 매일 Polarion 전체를 직접 훑지 않아도, 전날 대비 실제로 의미 있는 변경만 자동으로 찾아 최신 사양과 과거 이슈를 근거로 짧게 정리해 준다.

AI 가 QA 결정을 대신하지 않는다. 자동 실행이 하는 일은 "변경 탐지 → 자료 조사 → 비교 → 근거 정리" 까지다. 판단과 반영은 사람이 한다.

TC·Checklist·매뉴얼은 사람이 손으로 넣는 자료라 최신이 아니거나 틀릴 수 있다. 그래서 자동 실행은 이 자료와 비교하지 않는다. TC 와 맞춰 보는 일(TC 점검, 검증 TC 초안)은 사람이 결과 화면의 버튼을 누를 때만 한다(사용자 결정 2026-10-02).

이유: 사양 변경마다 TC 와 비교하던 분석은 작업 하나에 토큰 176만~516만 개를 써서 Claude 주간 한도를 다 썼다(2026-10-01·02 실행 기록).

### 이 기능은

- 서버가 평일 07:30(한국 시간)에 스스로 한 번 돈다. 주말과 대한민국 공휴일에는 돌지 않는다. QA 담당자는 화면의 [지금 실행] 버튼으로 같은 일을 바로 돌릴 수도 있다.
- Polarion 에서 SRS 전체와 이슈 전체를 읽어 어제 저장한 사본(스냅샷)과 비교한다. 바뀐 것만 "변경 이벤트"로 남긴다.
- 변경 이벤트의 종류에 따라 신규 이슈 분석, 수정 완료(Fixed) 이슈 분석, Spec 판정 이슈 분석, 새 댓글 분석, 사양 변경 분석을 돌린다.
- 결과는 `/qa-agent` 화면에서 본다. 카드마다 결론 한 가지, 한두 문장 요약, QA 할 일 3줄까지가 보인다(REQ-QAINTEL-033).
- 사양 변경 분석 화면의 [TC 점검], 수정 완료 이슈 분석 화면의 [검증 TC 초안 만들기]를 누르면 그때 TC 와 비교한다.
- 바뀐 것이 하나도 없으면 AI 를 한 번도 부르지 않고 "변경사항 없음"으로 끝난다.

```flow
평일 07:30 예약 또는 [지금 실행] -> Polarion 수집(SRS 전체·이슈 전체) -> 스냅샷 비교 -> 변경 이벤트
변경 이벤트 -> 분석 대상 고르기 -> 후보 압축(Exact -> BM25, TC·매뉴얼 제외) -> Claude Skill(격리 작업 폴더) -> 근거 검증 -> SQLite -> /qa-agent 대시보드
/qa-agent 대시보드 -> 사람이 [TC 점검]·[검증 TC 초안 만들기] -> TC 와 비교(요청 실행) -> SQLite
변경 이벤트 -(바뀐 것 없음)-> 변경사항 없음(AI 호출 0회) -> /qa-agent 대시보드
Polarion 수집 -(실패·0건·급감)-> 스냅샷 기준 유지 -> 다음 실행에서 다시 비교
Claude Skill -(실패)-> 이벤트 보존 -> 다음 실행에서 다시 분석
Claude Skill -(사용량 한도)-> 남은 AI 중지·초기화 시각 표시 -> 초기화 뒤 다시 분석
```

### 이 기능에서 쓰는 말

| 용어 | 뜻 |
|---|---|
| 제품 설정 | `config/products/<slug>.yaml`. 제품마다 다른 Polarion 프로젝트·조회식·필드 이름·연구소 결과 값·Checklist 형식을 적는다 |
| 공통 모델 | Polarion 원본 필드 이름을 제품 설정으로 바꿔 만든 공통 모양의 SRS(`SpecificationItem`)·이슈(`IssueItem`)·댓글(`IssueComment`) 기록이다. 분석 엔진은 원본 필드 이름을 모른다 |
| 연구소 결과 | 이슈의 연구소 검토 결과. VXvue 원본 필드는 `rndReviewResult` 이고, 제품 설정의 대응표로 공통 값(`FIXED`, `SPEC`, `NOT_BUG`, `DUPLICATE`, `PENDING`, `NO_ACTION`)으로 바꾼다 |
| 이슈 스냅샷 | 그날 읽은 이슈 전체를 저장한 파일(`data/daily_qa/snapshots/<slug>/issues/<날짜>.json`)이다 |
| 기준 스냅샷 | 비교할 전날 스냅샷이 없어 오늘 것만 저장한 상태다. 이때는 이벤트를 만들지 않는다 |
| 변경 이벤트 | 스냅샷 비교로 찾은 변경 하나(`qa_change_events` 한 줄)다. 종류(`event_type`), 바뀌기 전·후 값, 바뀐 필드, AI 분석이 필요한지와 그 이유를 담는다 |
| 분석 종류 | 이벤트를 받아 돌리는 분석이다. 신규 이슈(`NEW_ISSUE`), 수정 완료 이슈(`FIXED_ISSUE`), Spec 판정 이슈(`SPEC_DECISION`), 새 댓글(`COMMENT`), 사양 변경(`SPEC_COVERAGE`)이 있다 |
| 요청 실행 | 사람이 버튼이나 명령으로 한 가지만 부탁한 실행이다. TC 점검(`TC_CHECK`), 검증 TC 초안(`TC_DRAFT`), 매뉴얼 점검이 있다. 이때만 TC·매뉴얼을 AI 에 보낸다 |
| 결론 단계 | 화면에 보이는 결론이다. `사양과 다름`, `검토 필요`, `근거 부족`, `참고`, `문제 없음` 다섯 가지다(REQ-QAINTEL-033) |
| 후보 압축 | 이슈 전체를 AI 에 넘기지 않고, 코드가 Exact → BM25 검색으로 관련 후보만 골라 넣는 것이다 |
| Coverage | 기존 Checklist·TC 가 바뀐 사양의 핵심 동작과 Expected Result 를 실제로 검증하는지다 |
| 변경사항 없음 | 수집은 성공했고 변경 이벤트가 하나도 없는 실행 결과(`NO_CHANGE`)다 |

## 2. 범위

### 포함

- 예약 실행(평일·공휴일 제외)과 화면의 수동 실행
- SRS·이슈 전체 수집, 스냅샷 저장·비교, 변경 이벤트 저장과 재시도
- 다섯 가지 자동 분석과 근거 검증
- 사람이 요청할 때만 도는 TC 점검·검증 TC 초안·매뉴얼 점검과 초안 Excel
- `/qa-agent` 대시보드, 분석 상세 화면, 실행 상세 화면, 기간 분석 조회 화면
- 대시보드 위쪽의 수동 업로드 지식 문서 목록(업로드 날짜 포함)
- 제품 설정으로 다른 제품을 붙이는 구조(제품별 데이터 분리)

### 제외

- Polarion 에 쓰기. 이슈 닫기, 필드 수정, 댓글 달기 같은 일은 하지 않는다(REQ-DAILY-002).
- 원본 TC·Checklist 파일 수정. 초안은 별도 Excel 로만 만든다.
- 승인·거절·근거 추가 필요·검토자 입력·질문 답변 화면. 1차 개편에서 화면에 보이지 않는다. 옛 기록은 표에 그대로 남는다.
- 사람이 이슈 하나를 골라 돌리는 QA Agent 단일 이슈 분석(상위 SPEC 5.2절). 이 기능은 `/qa-agent/issue-analysis` 로 옮겨 그대로 둔다.

## 5. 기능 요구사항

ID 규칙: `REQ-<CATEGORY>-NNN`. CATEGORY는 대문자·숫자, NNN은 세 자리다.
CATEGORY는 프로젝트 전체에서 이 문서만 쓴다. 한 번 부여한 ID는 재사용하거나 의미를 바꾸지 않는다.

| 카테고리 | 이름 | 설명 |
|---|---|---|
| QAINTEL | QA Intelligence Agent | 변경 탐지 기반 일일 QA 분석(수집·이벤트·분석·검증·대시보드) |
| NFR-QAINTEL | QA Intelligence 비기능 | AI 호출 조건, 제품 공통 엔진 |

### 요구사항 지도

- 언제 도는가: REQ-QAINTEL-001(예약·공휴일), REQ-QAINTEL-021(지금 실행), REQ-QAINTEL-027(기간을 정한 수동 실행)
- 제품 차이 흡수: REQ-QAINTEL-002(제품 설정·공통 모델), REQ-QAINTEL-023(제품별 데이터 분리), NFR-QAINTEL-002
- 과거 기록: REQ-QAINTEL-029(ALM-QA-Automation 의 과거 SRS 스냅샷 가져오기), REQ-QAINTEL-030(이슈 기록이 없는 기간의 현재 상태 기준 점검)
- 무엇이 바뀌었나: REQ-QAINTEL-003(SRS), REQ-QAINTEL-004(이슈), REQ-QAINTEL-005(이벤트), REQ-QAINTEL-006(저장·재시도), REQ-QAINTEL-008(기준 스냅샷), REQ-QAINTEL-009(수집 실패), REQ-QAINTEL-028(삭제된 SRS 를 가리키는 TC)
- AI 를 부를지: REQ-QAINTEL-007(변경 없음·상태만 바뀜), REQ-QAINTEL-010(분석 대상 고르기), REQ-QAINTEL-025(사용량 한도·실행 예외), NFR-QAINTEL-001
- 분석하기(자동): REQ-QAINTEL-011(후보 압축), REQ-QAINTEL-012 ~ REQ-QAINTEL-015, REQ-QAINTEL-031(사양 변경 분석)
- 사람이 요청할 때만: REQ-QAINTEL-016(TC 점검), REQ-QAINTEL-032(검증 TC 초안), REQ-QAINTEL-034(매뉴얼 점검)
- 결과 믿기: REQ-QAINTEL-017(근거 검증), REQ-QAINTEL-018(초안 Excel), REQ-QAINTEL-026(토큰 사용량 기록)
- 결과 보기: REQ-QAINTEL-019(대시보드), REQ-QAINTEL-020(상세 화면), REQ-QAINTEL-033(결론 단계와 짧은 카드), REQ-QAINTEL-022(지식 문서 목록), REQ-QAINTEL-024(기간 조회)

### REQ-QAINTEL-001 예약 실행과 공휴일 건너뛰기

**하는 일** 평일 아침마다 제품별로 점검 프로세스를 한 번 띄운다. 주말과 대한민국 공휴일에는 띄우지 않고 로그만 남긴다.

**언제** 앱 안의 예약 기능이 `config.yaml` 의 `daily_qa.schedule` 요일·시각(기본 `mon-fri`, `07:30`, 한국 시간)에 깨어난다.

**순서**

1. 활성 제품 목록(`daily_qa.products`, 없으면 `daily_qa.product` 하나)마다 예약을 하나씩 등록한다. 예약 이름은 `qa_agent_<slug>` 이다(예: `qa_agent_vxvue`).
2. 깨어나면 오늘 날짜가 공휴일인지 로컬 공휴일 표(`config/holidays/kr.yaml`)와 추가 휴일(`daily_qa.schedule.extra_holidays`)로 확인한다. 외부 AI 나 웹 검색으로 공휴일을 판단하지 않는다.
3. 공휴일이면 별도 프로세스를 띄우지 않는다. 앱 로그에 `qa_agent_skipped reason=holiday product=<slug> date=<날짜> name=<공휴일 이름>` 을 남긴다.
4. 공휴일이 아니면 REQ-DAILY-001 과 같이 `scripts/run_daily_qa.py --product <slug>` 를 분리된 별도 프로세스로 띄운다. FastAPI 웹 프로세스 안에서 분석을 돌리지 않는다.

> **주의** 공휴일 표에 올해가 없으면 고정 공휴일(신정·삼일절·어린이날·현충일·광복절·개천절·한글날·성탄절)만 보고, 앱 로그에 `qa_agent_holiday_table_missing year=<연도>` 경고를 남긴다. 음력 공휴일과 대체공휴일은 매년 표에 더해야 한다.

**설정**

| 키 | 기본값 | 뜻 |
|---|---|---|
| `daily_qa.products` | `[daily_qa.product]` | 예약을 등록할 제품 이름 목록 |
| `daily_qa.schedule.skip_holidays` | `true` | 공휴일에 예약 실행을 건너뛸지 |
| `daily_qa.schedule.holiday_calendar` | `kr` | 공휴일 표 이름(`config/holidays/<이름>.yaml`) |
| `daily_qa.schedule.extra_holidays` | `[]` | 회사 휴무일처럼 표에 없는 쉬는 날(`YYYY-MM-DD`) |

**지킬 것**

- 공휴일 건너뛰기는 예약 실행에만 적용한다. 사람이 [지금 실행]을 누르면 공휴일에도 돈다(REQ-QAINTEL-021).

### REQ-QAINTEL-002 제품 설정과 공통 모델

**하는 일** Polarion 원본 필드 이름과 연구소 결과 값을 제품 설정에서 읽어 공통 모델로 바꾼다. 분석 엔진·이벤트 감지·저장·대시보드는 원본 필드 이름(`oldId`, `descriptionKR`, `rndReviewResult`, `occurrenceCause`, `actionDetails` 등)을 모른다.

**입력** 제품 설정 `config/products/<slug>.yaml` 의 두 절이다.

| 절 | 담는 것 |
|---|---|
| `alm` | Polarion 프로젝트(`project_id`), 조회식(`queries.srs`, `queries.issue`), 필드 이름(`fields.srs.*`, `fields.issue.*`), 관계 이름(`relations.*`), 연구소 결과 대응표(`rd_result_mapping`) |
| `qa_intelligence` | 사용 여부, QA 규칙 판(`rules.supported_rev`), 제품 규칙 Skill(`rules.product_skill`), Regression 축, Checklist 형식(`checklist`), 무시할 댓글 모양(`comment_noise_patterns`) |

**순서**

1. 제품 설정을 읽어 제품 프로필을 만든다. `alm` 절이 없으면 `config.yaml` 의 `daily_qa.polarion.project_id`·`srs_query`·`issue_query` 를 쓴다(예전 설정과 호환).
2. 필드 이름을 적지 않은 항목은 Polarion 표준 필드 이름(`id`, `title`, `status`, `severity`, `description`, `created`, `updated`, `linkedWorkItems`, `comments`)을 쓴다. 제품 고유 필드(Legacy 번호, 연구소 결과, 재현 절차, 발생 원인, 조치 내용, 발생·목표 버전)는 제품 설정에 적은 것만 읽는다.
3. 한 항목에 이름을 여러 개 적으면 앞에서부터 값이 있는 첫 필드를 쓴다.

   > **예시** VXvue 는 SRS 본문을 `[descriptionKR, description]` 으로 적는다. 한국어 본문이 없으면 `description` 을 쓴다.

4. 연구소 결과 원본 값을 대응표로 공통 값으로 바꾼다. 대응표에 없는 값은 `OTHER`, 빈 값은 `UNSET` 이다. 원본 값도 함께 남긴다.

| 공통 값 | 뜻 | VXvue 원본 값 |
|---|---|---|
| `FIXED` | 프로그램이 고쳐졌다 | `lab_fixed` |
| `SPEC` | 사양대로 동작한다 | `lab_inspec` |
| `NOT_BUG` | 결함이 아니다 | `lab_nobug` |
| `DUPLICATE` | 다른 이슈와 같다 | `lab_duplicate` |
| `PENDING` | 보류 | `lab_pending` |
| `NO_ACTION` | 조치 없음 | `lab_noact` |

**결과** 공통 모델 기록이다.

| 기록 | 필드 |
|---|---|
| SRS | `id`, `old_id`(Legacy 번호), `title`, `status`, `updated`, `is_category`, `text` |
| 이슈 | `id`, `title`, `status`, `severity`, `rd_result`(공통 값), `rd_result_raw`, `description`, `reproduction_step`, `occurrence_cause`, `action_details`, `occurred_versions`, `target_versions`, `linked_ids`, `comment_ids`, `comments`, `created`, `updated` |
| 댓글 | `id`, `created`, `text`(2000자까지) |

> **참고** SRS 기록의 필드 이름은 이전 스냅샷 파일과 같다. 그래서 개편 전에 저장한 스냅샷도 그대로 비교 기준으로 쓸 수 있다.

**지킬 것**

- 제품 고유 필드 이름과 연구소 결과 원본 값은 제품 설정과 제품 어댑터(`app/modules/daily_qa/product_adapter.py`) 한 곳에만 둔다.

### REQ-QAINTEL-003 SRS 전체 수집과 구조화한 비교

**하는 일** 매 실행 SRS 전체를 읽고 마지막 정상 스냅샷과 비교해 신규·변경·삭제를 찾는다. 본문이 바뀌면 바뀐 문장까지 좁힌다.

**언제** 매 실행의 SRS 수집 단계(`collect_srs`).

**순서**

1. REQ-DAILY-002 의 SRS 순서대로 읽는다. 조회식은 제품 프로필의 `queries.srs` 다.
2. 비교 필드는 Legacy 번호, 제목, 상태, 본문이다. 수정 시각만 바뀐 것은 변경이 아니다.
3. 값을 비교하기 전에 공백과 줄바꿈을 한 칸으로 줄인다. 공백·줄바꿈만 다른 것은 변경이 아니다.
4. 본문은 ALM-QA-Automation `srs-spec` 이 사양서 PDF 를 만들 때와 같은 규칙으로 글자로 바꾼다. 그래야 그 도구의 과거 스냅샷(REQ-QAINTEL-029)과 비교해도 바뀌지 않은 SRS 가 바뀐 것으로 보이지 않는다.

   - 다른 Work Item 을 가리키는 표시(`polarion-rte-link`)는 `번호 - 제목` 으로 푼다. 제목은 같은 수집의 SRS 제목이다. 찾지 못하면 `번호 (참조 대상 확인 불가)` 다.
   - Polarion 서버 경로의 장식 아이콘(`/polarion/…` 이미지)은 지운다. 첨부 이미지는 `[이미지: <파일 이름>]` 으로 남긴다.

   > **예시** 본문의 `<span class="polarion-rte-link" data-item-id="VP-678">` 는 `VP-678 - Status Bar` 가 된다.

5. 본문이 바뀌었으면 문장 단위로 나눠 더해진 문장(`added_sentences`)과 빠진 문장(`removed_sentences`)을 남긴다.
6. 스냅샷을 `data/daily_qa/snapshots/<slug>/srs/<날짜>.json` 에 저장한다(시험 실행은 저장하지 않는다).

**결과** 변경마다 SRS 번호, 변경 종류(신규·변경·삭제), 바뀐 필드(`changed_fields`), 바뀌기 전·후 값, 바뀐 문장이다.

> **참고** 이 제품의 새 폴더에 스냅샷이 아직 없으면, 개편 전 위치(`data/daily_qa/snapshots/<날짜>.json`)의 스냅샷을 비교 기준으로 읽는다. 이 호환은 `daily_qa.product` 로 적힌 제품에만 적용한다. 파일은 옮기지 않는다.

### REQ-QAINTEL-004 이슈 전체 수집과 이슈 스냅샷

**하는 일** 매 실행 이슈 전체를 읽어 이슈 스냅샷으로 저장한다. 신규 여부는 수정 시각이 아니라 스냅샷 비교로 정한다.

**언제** 매 실행의 이슈 수집 단계(`collect_issues`).

**순서**

1. 제품 프로필의 `queries.issue`(VXvue 는 `type:issue`)로 모든 이슈를 쪽 단위로 읽는다. 요청 방식은 REQ-DAILY-002 와 같다(GET 만).
2. 이슈마다 공통 모델로 바꾼다(REQ-QAINTEL-002).
3. 댓글은 필요한 이슈만 읽는다.
   - 이슈 응답에 댓글 관계(`relations.comments`)의 댓글 번호가 있으면, 번호가 어제와 달라진 이슈만 댓글 본문을 읽는다.
   - 댓글 번호가 없으면 수정 시각이 어제와 다른 이슈만 읽는다.
   - 어제 스냅샷에 없던 이슈(신규)는 읽는다. 기준 스냅샷 실행은 댓글 본문을 읽지 않는다.
   - 한 실행에서 댓글을 읽는 이슈 수는 `daily_qa.intelligence.comment_fetch_limit`(기본 300)까지다. 넘은 이슈는 다음 실행에서 읽는다.
4. 댓글을 읽지 않았거나 읽다가 실패한 이슈는 어제 스냅샷의 댓글을 그대로 옮겨 적는다. 실패한 이슈는 `comments_error` 로 표시하고, 그 이슈의 댓글 비교는 이번 실행에서 하지 않는다.
5. 스냅샷을 `data/daily_qa/snapshots/<slug>/issues/<날짜>.json` 에 저장한다. 파일 머리에 수집 시각(`collected_at`)을 적는다.

**결과** 이슈 스냅샷 한 파일. 이슈 수집 단계에 전체 이슈 수, 댓글을 읽은 이슈 수, 댓글 읽기 실패 수가 남는다.

**설정**

| 키 | 기본값 | 뜻 |
|---|---|---|
| `daily_qa.intelligence.comment_fetch_limit` | `300` | 한 실행에서 댓글을 읽는 이슈 수 상한 |
| `daily_qa.intelligence.issue_drop_ratio` | `0.5` | 어제보다 이 비율 넘게 줄면 비정상으로 본다(REQ-QAINTEL-009) |

**지킬 것**

- 댓글 원문은 2000자까지만 스냅샷에 둔다. 스냅샷은 SRS 스냅샷과 같이 저장소 밖의 `data/` 에만 있고 Git 에 올리지 않는다(NFR-PRIV-002, NFR-DAILY-002).

  이유: 새 댓글 분석이 실패하면 다음 실행에서 같은 댓글로 다시 분석해야 한다.

### REQ-QAINTEL-005 변경 이벤트 만들기

**하는 일** 어제와 오늘의 공통 모델을 필드별로 비교해 변경 이벤트를 만든다. 이슈 하나에서 이벤트가 여러 개 나올 수 있다.

**순서**

1. SRS 비교 결과(REQ-QAINTEL-003)마다 이벤트 하나를 만든다.
2. 이슈는 번호로 맞대어 아래 표대로 이벤트를 만든다. 값은 공백을 줄인 뒤 비교한다.

| 이벤트 | 조건 | AI 분석 |
|---|---|---|
| `ISSUE_CREATED` | 어제 스냅샷에 없던 이슈 | 필요 |
| `ISSUE_CONTENT_UPDATED` | 제목 또는 설명이 바뀜 | 필요 |
| `ISSUE_REPRODUCTION_UPDATED` | 재현 절차가 바뀜 | 필요 |
| `ISSUE_RD_RESULT_CHANGED` | 연구소 결과 원본 값이 바뀜 | 공통 값에 따라 다름 (REQ-QAINTEL-010) |
| `ISSUE_ROOT_CAUSE_CHANGED` | 발생 원인이 바뀜 | 공통 값에 따라 다름 |
| `ISSUE_ACTION_DETAILS_CHANGED` | 조치 내용이 바뀜 | 공통 값에 따라 다름 |
| `ISSUE_COMMENT_ADDED` | 어제 없던 댓글이 생김 | 의미 있는 댓글이 있을 때만 |
| `ISSUE_STATUS_ONLY_CHANGED` | 상태만 바뀌고 위 항목은 그대로 | 필요 없음 |
| `ISSUE_METADATA_CHANGED` | 심각도·발생 버전·목표 버전·연결 항목이 바뀜 | 필요 없음 |
| `ISSUE_REMOVED` | 어제 있었는데 오늘 조회되지 않음 | 필요 없음 |
| `SRS_CREATED` | 새 SRS | 필요 |
| `SRS_UPDATED` | SRS 비교 필드가 바뀜 | 필요 |
| `SRS_REMOVED` | 어제 있었는데 오늘 없음 | 필요 없음. 삭제 SRS 를 가리키는 TC 는 코드가 찾는다(REQ-QAINTEL-028) |

3. 새 댓글이 "의미 있는 댓글"인지 코드가 먼저 거른다. 글자 수가 `daily_qa.intelligence.comment_min_chars`(기본 15)보다 짧거나, 진행 상태 문장(예: "확인했습니다", "진행 중")만 있으면 의미 없는 댓글이다.
   - 진행 상태 문장은 공통 기본 목록으로 가린다. 제품 설정의 `comment_noise_patterns` 는 이 기본 목록에 더한다(기본 목록을 대신하지 않는다).

   > **예시** VXvue 가 `재확인 부탁드립니다` 를 더하면 "재확인 부탁드립니다"와 기본 목록의 "확인 부탁드립니다" 가 모두 의미 없는 댓글이다.
4. 상태가 다른 변경과 함께 바뀌면 `ISSUE_STATUS_ONLY_CHANGED` 를 만들지 않는다. 상태 변화는 `ISSUE_METADATA_CHANGED` 의 바뀐 필드에 들어간다.

**결과** 이벤트마다 아래 값이다.

| 필드 | 뜻 |
|---|---|
| `product` | 제품 slug |
| `run_id` | 이벤트를 만든 실행 |
| `entity_type` | `issue` 또는 `srs` |
| `entity_id` | 이슈·SRS 번호 |
| `event_type` | 위 표의 이벤트 이름 |
| `before`, `after` | 바뀐 필드의 바뀌기 전·후 값 |
| `changed_fields` | 바뀐 필드 이름(공통 모델 이름) |
| `analysis_required` | AI 분석이 필요한지 |
| `reason` | 필요하거나 필요 없는 이유 한 문장 |
| `created_at` | 만든 시각 |

**지킬 것**

- 이벤트 감지는 공통 모델의 필드 이름만 쓴다. 제품 고유 필드 이름을 모른다.

### REQ-QAINTEL-006 이벤트 저장과 재시도

**하는 일** 변경 이벤트를 SQLite 표 `qa_change_events` 에 저장하고, AI 분석이 실패하거나 미뤄진 이벤트는 다음 실행에서 다시 분석한다.

**순서**

1. 스냅샷을 먼저 저장하고, 저장에 성공한 뒤에 이벤트를 저장한다. 이벤트 저장이 실패하면 방금 저장한 오늘 스냅샷을 지워 다음 실행이 같은 변경을 다시 찾게 한다.
2. 같은 제품·대상·이벤트 종류이고, 바뀌기 전·후 값과 비교 기준 스냅샷 날짜가 같은 이벤트가 이미 있으면 다시 저장하지 않는다. 같은 날 다시 돌려도 겹치지 않는다.

   > **예시** `VP-100` 이 수정됨 → 다시 열림 → 다시 수정됨으로 바뀌면, 두 번째 수정도 새 `ISSUE_RD_RESULT_CHANGED` 이벤트다. 비교 기준 스냅샷 날짜가 다르기 때문이다. 수정 완료 이슈 분석도 다시 돈다.

   이미 있던 이벤트는 이번 실행의 새 이벤트로 세지 않는다(REQ-QAINTEL-007).
3. 분석 상태(`analysis_status`)는 아래 가운데 하나다.

| 값 | 뜻 |
|---|---|
| `not_required` | AI 분석이 필요 없는 이벤트 |
| `pending` | 분석을 기다린다. 상한 초과·규칙 판 불일치·토큰 없음으로 돌리지 못한 것도 여기 남는다 |
| `done` | 분석이 끝났다. 만든 Finding 번호를 함께 남긴다 |
| `failed` | 분석 작업이 실패했다. 다음 실행에서 다시 한다 |
| `abandoned` | `daily_qa.intelligence.event_max_attempts`(기본 3)번 실패해 더 시도하지 않는다 |

4. 이벤트 하나가 분석 두 가지를 부를 수 있다. 예를 들어 수정 완료 이슈의 새 댓글은 새 댓글 분석과 수정 완료 이슈 분석을 부른다. 이벤트는 아직 끝나지 않은 분석 목록(`remaining_analyses_json`)을 가진다.

   - 분석 하나가 성공하면 그 분석만 목록에서 뺀다. 목록이 비어야 `done` 이다.
   - 분석 하나가 실패하면 이벤트는 `failed` 이고, 다음 실행은 목록에 남은 분석만 다시 돌린다. 성공한 분석은 다시 돌지 않는다.
   - 실패 횟수(`attempts`)는 실행 하나에 한 번만 올린다. 같은 실행에서 두 분석이 모두 실패해도 1이다.
5. 다음 실행은 `pending`·`failed` 이벤트를 오늘 이벤트와 함께 분석 대상으로 올린다. 분석에는 오늘 스냅샷의 최신 값을 쓴다. 대상이 오늘 스냅샷에 없으면 `abandoned` 로 바꾼다. 이때는 실패 횟수를 올리지 않는다.

   > **예외** 종료일이 지난 날인 기간 실행(REQ-QAINTEL-027)은 이 대기열을 다루지 않는다. 지난 스냅샷에 대상이 없다고 `abandoned` 로 바꾸지도 않는다.

**결과** 이벤트 표의 한 줄, 실행 폴더의 `change_events.json`(이번 실행이 찾은 이벤트 전체. 이미 저장돼 있어 다시 넣지 않은 이벤트도 들어 있다).

> **참고** 개편 전의 이슈 기준 시각(`issues_last_success_at`)·처리한 이슈 기록(`issues_processed`)은 더 쓰지 않는다. 상태 표에 남아 있어도 지우지 않는다. 미뤄 둔 사양 변경(`spec_change_pending`)이 있으면 첫 실행에서 `SRS_UPDATED` 대기 이벤트로 바꾸고 비운다.

### REQ-QAINTEL-007 변경이 없거나 상태만 바뀐 실행

**하는 일** AI 가 필요 없는 변경만 있거나 변경이 하나도 없으면 Claude 를 부르지 않는다.

**순서**

1. 이벤트가 있어도 모두 `analysis_required` 가 거짓이면 AI 작업을 만들지 않는다. 이벤트는 기록으로 저장한다.

   > **예시** 이슈 `VP-100` 의 상태만 `open` 에서 `in_progress` 로 바뀌고 댓글·연구소 결과·발생 원인·조치 내용·재현 절차·본문이 그대로면 `ISSUE_STATUS_ONLY_CHANGED` 이벤트만 남고 Claude 호출은 0회다.

2. SRS 수집과 이슈 수집이 모두 성공했고, 새 이벤트도 다시 분석할 대기 이벤트도 없으면 실행 결과는 `NO_CHANGE` 다. 화면에는 "변경사항 없음"으로 보인다. Claude 호출 수는 0이다.
   - 이미 저장돼 있어 다시 넣지 않은 이벤트는 새 이벤트가 아니다(REQ-QAINTEL-006 순서 2).
   - 수집을 건너뛰었거나(Polarion 설정 없음) 실패했으면 변경을 확인하지 못한 것이다. 실행 결과는 `PARTIAL`(일부 실패)이다.
3. 매뉴얼 누락 후보 점검(REQ-DAILY-006)이 그날 돌 차례여도 새 변경이 없는 실행(`NO_CHANGE`, `BASELINE`)에서는 돌리지 않는다. 다음 변경이 있는 실행으로 미룬다(상태 값 `<slug>:manual_check_due`).

**결과** 실행 결과 `NO_CHANGE`(변경사항 없음), 이벤트 단계 비고 "AI 분석이 필요한 변경이 없습니다".

### REQ-QAINTEL-008 첫 실행 기준 스냅샷

**하는 일** 이슈 스냅샷을 처음 만드는 실행은 기준만 만들고 분석하지 않는다. 기존 이슈 전체를 신규로 보지 않는다.

**순서**

1. 비교할 이슈 스냅샷이 없으면 오늘 이슈 스냅샷만 저장하고 이슈 이벤트를 만들지 않는다.
2. SRS 도 같다. 비교할 SRS 스냅샷이 없으면 기준만 저장한다.
3. 두 번째 정상 실행부터 변경을 분석한다.

**결과** 이번 실행에 이벤트가 없고 기준을 하나라도 새로 만들었으면 실행 결과는 `BASELINE`(기준 스냅샷 생성)이다. 단계 비고에 "기준 스냅샷만 저장(비교 대상 없음)"이 남는다.

### REQ-QAINTEL-009 수집 실패 때 기준을 옮기지 않기

**하는 일** 수집이 믿을 수 없으면 오늘 스냅샷을 저장하지 않고 이벤트도 만들지 않는다. 다음 실행은 마지막 정상 스냅샷과 비교한다.

**순서** 아래 가운데 하나면 그 수집 단계는 실패다. 스냅샷과 이벤트를 저장하지 않는다.

| 경우 | 단계 비고 |
|---|---|
| Polarion 요청 실패(쪽 넘기기 도중 실패 포함) | REQ-DAILY-002 의 오류 문장 |
| SRS 0건 | "SRS 조회 결과가 0건입니다. 조회식·권한을 확인하세요." |
| 이슈 0건 | "이슈 조회 결과가 0건입니다. 조회식·권한을 확인하세요." |
| 이슈 수가 어제의 `1 - issue_drop_ratio` 보다 적음 | "이슈 수가 어제 <어제>건에서 오늘 <오늘>건으로 급감했습니다. 조회 결과를 믿을 수 없어 기준을 옮기지 않습니다." |
| 스냅샷 파일 쓰기 실패 | "스냅샷을 저장하지 못했습니다: <오류 종류>" |

> **참고** 한 이슈의 댓글만 읽지 못한 경우는 수집 실패가 아니다. 그 이슈는 어제 댓글을 옮겨 적고 다음 실행에서 다시 읽는다(REQ-QAINTEL-004).

AI 분석만 실패한 경우는 스냅샷을 정상으로 저장하고, 이벤트를 `failed` 로 남겨 다음 실행에서 다시 분석한다(REQ-QAINTEL-006).

### REQ-QAINTEL-010 분석 대상 고르기

**하는 일** 이벤트를 분석 종류로 나눈다. 같은 대상·같은 분석 종류는 한 번만 분석하고, 관련 이벤트 번호를 모두 묶는다.

**순서** 이슈의 오늘 연구소 결과(공통 값)를 보고 아래 표대로 고른다.

| 이벤트 | `FIXED` | `SPEC`·`NOT_BUG` | 그 밖 |
|---|---|---|---|
| `ISSUE_CREATED` | 신규 이슈 분석 | 신규 이슈 분석 | 신규 이슈 분석 |
| `ISSUE_RD_RESULT_CHANGED` | 수정 완료 이슈 분석 | Spec 판정 이슈 분석 | 기록만 |
| `ISSUE_ROOT_CAUSE_CHANGED`, `ISSUE_ACTION_DETAILS_CHANGED` | 수정 완료 이슈 분석 | Spec 판정 이슈 분석 | 기록만 |
| `ISSUE_CONTENT_UPDATED`, `ISSUE_REPRODUCTION_UPDATED` | 수정 완료 이슈 분석 | Spec 판정 이슈 분석 | 신규 이슈 분석(다시 분석) |
| `ISSUE_COMMENT_ADDED`(의미 있는 댓글) | 새 댓글 분석 + 수정 완료 이슈 분석 | 새 댓글 분석 | 새 댓글 분석 |
| `SRS_CREATED`, `SRS_UPDATED` | 사양 변경 분석(REQ-QAINTEL-031) | | |

"기록만" 인 이벤트는 `analysis_required` 가 거짓이고, 이유에 "이 연구소 결과에 맞는 분석이 없어 기록만 남김" 이 적힌다.

**결과** 분석 종류별 대상 목록. 한 실행의 작업 묶음 수 상한(NFR-DAILY-001) 안에서 신규 이슈 → 수정 완료 → Spec 판정 → 새 댓글 → 사양 변경 순으로 돌린다. 상한을 넘은 대상의 이벤트는 `pending` 으로 남는다.

### REQ-QAINTEL-011 후보 압축

**하는 일** 분석마다 AI 에 넘길 후보를 코드가 먼저 고른다. 이슈 전체·TC 전체·사양 전체를 넘기지 않는다.

> **주의** TC 후보와 매뉴얼 문단 후보는 요청 실행(REQ-QAINTEL-016·032)에만 넣는다. 자동 실행의 작업 입력과 작업 폴더 `context/` 에는 TC 색인(`tc_index.jsonl`)과 매뉴얼(`manuals/`)을 두지 않는다.

**순서**

1. 검색어를 코드가 뽑는다. 이슈·SRS 번호, Legacy 번호, 연결 항목, Command 번호, DICOM Tag, 따옴표 문구, 오류 코드 모양을 정규식으로 찾는다.
2. 검색은 기존 Exact → BM25 단계 검색(`app/retrieval/hybrid.py`)을 쓴다. 식별자가 정확히 일치한 후보가 앞자리를 차지하고, 남은 자리를 BM25 가 채운다. 점수를 섞지 않는다.
3. 후보 종류와 개수는 아래와 같다.

| 후보 | 어디서 | 개수 설정(기본) |
|---|---|---|
| 비슷한 과거 이슈 | 오늘 이슈 스냅샷(자기 자신 제외) | `daily_qa.intelligence.issue_candidates`(8) |
| 사양 | 오늘 SRS 스냅샷 | `daily_qa.intelligence.spec_candidates`(6) |
| 사양서 조각 | Knowledge 에 등록한 사양서(기존 문서 로더 `app/core/knowledge_documents.py`) | `daily_qa.intelligence.spec_doc_candidates`(4) |
| TC (요청 실행만) | TC 색인(REQ-DAILY-014) | `daily_qa.tc_candidate_limit`(15) |
| 매뉴얼 문단 (요청 실행만) | 지식 사본의 매뉴얼 텍스트 | 3 |

4. 비슷한 과거 이슈는 새 이슈와 발생 버전 또는 목표 버전이 같은 후보를 앞에 둔다. 현재 검증 중인 버전의 중복을 먼저 보기 위해서다.
5. 후보마다 "왜 걸렸는지"(`'VP-5500' 정확 일치`, `용어 유사도 0.42`, `연결 항목`)를 함께 넘긴다.
6. 사양서 문서를 읽지 못하면 SRS 후보만으로 진행하고, 읽지 못한 문서 이름을 작업 입력과 단계 비고에 남긴다.

**결과** 작업 입력 파일(`in/<작업 ID>.json`). 마스킹한 뒤 격리 작업 폴더에 쓰고, 같은 파일을 실행 폴더 `sent/` 에 남긴다(REQ-DAILY-021).

### REQ-QAINTEL-012 신규 이슈 분석

**하는 일** 새로 등록된 이슈마다 중복 여부, 사양 대비 판단, 과거 이슈와의 관계, QA 권고를 정리한다. Skill `qa-new-issue-analysis`.

**입력** 이슈(제목·설명·재현 절차·Expected·Actual·오류 문구·연결 SRS·발생·목표 버전), 비슷한 과거 이슈 후보, 사양 후보, 사양서 조각. TC 후보는 넣지 않는다.

**순서**

1. 중복 분석. 후보 가운데 같은 증상·같은 조건의 이슈를 찾는다.

| 판정 | 뜻 |
|---|---|
| `STRONG_DUPLICATE` | 조건·증상·결과가 같은 이슈가 있다 |
| `POSSIBLE_DUPLICATE` | 비슷하지만 조건이나 결과가 일부 다르다 |
| `NO_DUPLICATE_FOUND` | 후보 가운데 중복이 없다 |

   중복 후보가 있으면 이슈 번호, 중복으로 본 근거, 차이점, 그 이슈의 과거 처리 결과를 함께 적는다. 과거 비슷한 이슈가 `SPEC` 으로 처리됐으면 "기존 유사 Issue가 Spec으로 처리된 이력이 있음" 을 표시한다.

2. 사양 분석. 연결 SRS 만 믿지 않고 사양 후보 전체와 비교한다.

| 판정 | 뜻 |
|---|---|
| `SPEC_VIOLATION` | 실제 결과가 최신 사양과 다르다 |
| `CONSISTENT_WITH_SPEC` | 실제 결과가 사양대로다 |
| `SPEC_UNDEFINED` | 사양이 이 동작을 정하지 않았다. 연구소·기획에 확인할 질문을 적는다 |
| `SPEC_AMBIGUOUS` | 사양 문장이 여러 뜻으로 읽힌다 |
| `INSUFFICIENT_EVIDENCE` | 판단할 근거가 모자란다 |

   사양이 없으면 결함이나 정상 동작으로 정하지 않고 `SPEC_UNDEFINED` 로 둔다.

3. 과거 이슈 분석. 아래 표시를 후보로만 붙인다. 확정 사실처럼 쓰지 않는다.

| 표시 | 뜻 |
|---|---|
| `POSSIBLE_REGRESSION` | 과거 `FIXED` 이슈가 다시 생겼을 수 있다 |
| `HISTORICAL_SPEC_DUPLICATE` | 과거 `SPEC`·`NOT_BUG` 로 처리된 이슈가 다시 등록됐을 수 있다 |
| `HISTORICAL_DUPLICATE_REREGISTERED` | 과거 `DUPLICATE` 로 처리된 이슈가 다시 등록됐을 수 있다 |
| `HISTORICAL_DECISION_CONFLICT` | 비슷한 과거 이슈들의 처리 결과가 서로 다르다 |

4. QA 권고. 사람이 할 다음 조치와 확인할 점을 적는다.

**결과** Finding 하나(분석 종류 `NEW_ISSUE`, 대상 = 이슈 번호, 판정 = 사양 판정). 중복·사양·과거 이슈·권고는 Finding 의 구획(`sections`)에 있다.

**지킬 것**

- TC 초안을 만들지 않는다. 만들면 코드가 Finding 을 버린다(REQ-QAINTEL-017).

### REQ-QAINTEL-013 수정 완료 이슈 분석

**하는 일** 연구소가 고쳤다고 한 이슈의 원인과 조치를 검토하고, 최신 사양과 맞는지, 어디까지 다시 봐야 하는지 짧게 정리한다. QA 규칙의 Skill S04(Fix Verification)에 해당한다. Skill `qa-fixed-issue-analysis`.

> **참고** 검증 TC 초안은 자동으로 만들지 않는다. 사람이 이 분석 화면에서 [검증 TC 초안 만들기]를 누르면 REQ-QAINTEL-032 가 만든다.

**언제** REQ-QAINTEL-010 표에서 "수정 완료 이슈 분석" 인 이벤트가 있을 때.

**입력** 이슈 본문, 발생 원인, 조치 내용, 연구소 댓글(최근 20개), 사양 후보, 사양서 조각, 비슷한 과거 이슈, 제품 설정의 Regression 축. TC 후보는 넣지 않는다.

**순서**

1. 원인 검토 → 조치 검토 → 최신 사양과 맞는지 → 바뀐 범위 → 부작용·Regression 범위 → QA 할 일 순서로 쓴다.
2. 사양 판정은 아래 가운데 하나다.

| 판정 | 뜻 |
|---|---|
| `CONSISTENT_WITH_SPEC` | 조치 결과가 최신 사양과 맞다 |
| `PARTIALLY_CONSISTENT` | 일부만 맞다 |
| `CONTRADICTS_SPEC` | 조치 결과가 최신 사양과 다르다 |
| `SPEC_UNDEFINED` | 사양이 정하지 않았다 |
| `INSUFFICIENT_EVIDENCE` | 근거가 모자란다 |

3. Regression 축은 코드가 제품 설정(`qa_intelligence.regression_axes`)에서 정한다. 모델은 축마다 해당 여부와 이유만 채운다. 해당하지 않는 축도 "해당 없음" 으로 남긴다.
4. 요약은 한두 문장, QA 할 일은 3줄까지 쓴다.

**결과** Finding 하나(분석 종류 `FIXED_ISSUE`, 판정 = 사양 판정). 초안은 없다.

**지킬 것**

- 이 분석이 초안이나 TC Coverage 를 내면 코드가 그 부분만 빼고 "자동 분석이라 초안을 뺐습니다" 메모를 남긴다(REQ-QAINTEL-017).

### REQ-QAINTEL-014 Spec 판정 이슈 분석

**하는 일** 연구소가 "사양대로" 또는 "결함 아님" 으로 판정한 이슈를 사양과 과거 이슈에 비춰 다시 본다. 연구소 판정을 그대로 믿지 않는다. Skill `qa-spec-decision-analysis`.

**입력** 이슈 본문, 연구소 댓글, 발생 원인, 조치 내용, 사양 후보, 사양서 조각, 비슷한 과거 이슈.

**순서**

1. 연구소가 무엇을 근거로 사양이라고 했는지 정리한다.
2. 사양 근거를 판정한다.

| 판정 | 뜻 |
|---|---|
| `SUPPORTED_BY_SPEC` | 사양이 연구소 판정을 뒷받침한다 |
| `PARTIALLY_SUPPORTED` | 일부만 뒷받침한다 |
| `SPEC_AMBIGUOUS` | 사양 문장이 여러 뜻으로 읽힌다 |
| `SPEC_NOT_FOUND` | 뒷받침하는 사양을 찾지 못했다 |
| `CONTRADICTS_SPEC` | 사양이 연구소 판정과 다르다 |

3. 과거 비슷한 증상이 `FIXED` 였는데 이번에 `SPEC` 으로 처리됐거나, 그 반대면 `HISTORICAL_DECISION_CONFLICT` 를 표시한다.

**결과** Finding 하나(분석 종류 `SPEC_DECISION`). TC 초안을 만들지 않는다.

### REQ-QAINTEL-015 새 댓글 분석

**하는 일** 어제 없던 댓글만 읽어 QA 판단에 영향이 있는 정보인지 나눈다. 댓글 전체를 매일 다시 분석하지 않는다. Skill `qa-comment-analysis`.

**입력** 새 댓글(의미 있는 댓글만), 이슈 요약, 이전 댓글 최근 5개, 그 이슈의 가장 최근 분석 요약.

**순서**

1. 댓글마다 요약, 분류, 기존 QA 분석에 주는 영향, 추가로 확인할 점을 적는다.

| 분류 | 뜻 |
|---|---|
| `ROOT_CAUSE_INFORMATION` | 원인 정보 |
| `RESOLUTION_INFORMATION` | 조치 정보 |
| `REPRODUCTION_INFORMATION` | 재현 조건 정보 |
| `SPEC_CLAIM` | 사양이라는 주장 |
| `REQUIREMENT_INFORMATION` | 요구사항 정보 |
| `QA_ACTION_REQUIRED` | QA 가 할 일 |
| `OTHER_SIGNIFICANT_INFORMATION` | 그 밖의 의미 있는 정보 |
| `NOT_SIGNIFICANT` | 진행 상태 같은 의미 없는 내용 |

2. 모든 댓글이 `NOT_SIGNIFICANT` 면 Finding 을 저장하지 않는다.

**결과** Finding 하나(분석 종류 `COMMENT`, 판정 = 가장 앞 댓글의 분류).

### REQ-QAINTEL-016 TC 점검 (사양 변경 Coverage 분석, 요청할 때만)

**하는 일** 바뀐 SRS 하나에 대해 과거 이슈 이력과 기존 Checklist·TC 가 그 변경을 이미 충분히 다루는지 보고, 모자라면 기존 TC 수정안이나 신규 Checklist TC 초안을 낸다. Skill `qa-spec-coverage-analysis`. 상위 SPEC 의 사양 변경 영향 검토(REQ-DAILY-003)를 대신한다.

**언제** 사람이 사양 변경 분석(REQ-QAINTEL-031) 상세 화면에서 [TC 점검]을 누를 때만 돈다(`POST /qa-agent/findings/<번호>/tc-check`). CLI 는 `scripts/run_daily_qa.py --on-demand tc-check --finding <번호>` 다. 자동 실행에서는 돌지 않는다.

이유: TC 는 사람이 넣는 자료라 최신이 아닐 수 있다. 자동으로 비교하면 틀린 결론이 나오고 토큰도 많이 쓴다.

요청 실행은 아래처럼 돈다(REQ-QAINTEL-032 와 같다).

1. 저장된 최신 SRS·이슈 스냅샷을 쓴다. Polarion 을 새로 읽지 않는다.
2. 원래 분석을 만든 변경 이벤트로 작업 하나를 만든다. TC 후보와 매뉴얼 문단 후보를 넣는다.
3. 결과는 새 Finding(분석 종류 `TC_CHECK`)으로 저장하고 `sections.source_finding` 에 원래 분석 번호를 적는다. 원래 분석과 이벤트 상태는 바꾸지 않는다.
4. 메일을 보내지 않는다. 실행 잠금·사용량 한도·실행당 작업 상한은 자동 실행과 같다.

**입력**

- 변경: SRS 번호, 변경 종류, 바뀐 필드, 바뀌기 전·후 값, 바뀐 문장(REQ-QAINTEL-003)
- 관련 과거 이슈: 코드가 아래 순서로 고른 후보. 각 후보에 코드가 붙인 관계 단서(과거 `FIXED`, 과거 `SPEC`·`NOT_BUG`, 최근 `daily_qa.intelligence.recent_fixed_days`(기본 90)일 안의 `FIXED`)가 있다.
  1. SRS 번호·Legacy 번호 정확 일치(연결 항목 또는 본문)
  2. BM25(SRS 제목과 바뀐 문장)
- TC 후보: 코드가 아래 순서로 고른 후보. 각 후보에 걸린 이유가 있다. 영향성평가 Checklist 파일(`qa_intelligence.checklist.template_name_contains`)의 행을 같은 순위 안에서 앞에 둔다.
  1. SRS 번호로 연결된 TC
  2. Legacy 번호로 연결된 TC
  3. 관련 과거 이슈 번호가 적힌 TC
  4. BM25 로 찾은 같은 기능·Workflow 의 TC
- 매뉴얼 문단 후보(제목과 바뀐 문장 기준)

**순서**

1. 무엇이 바뀌었는지 정리한다.
2. 관련 이슈가 있는지, 각 이슈가 어떤 관계인지 적는다. 이슈가 있다고 TC Coverage 가 있다고 보지 않는다.

| 관계 | 뜻 |
|---|---|
| `EXISTING_DEFECT` | 그 동작의 기존 결함 |
| `PAST_FIXED` | 과거 수정 완료 이슈 |
| `PAST_SPEC` | 과거 Spec 판정 이슈 |
| `SAME_FUNCTION_REGRESSION` | 같은 기능의 Regression |

3. TC 마다 Title·Precondition·Test Step·Expected Result·Test Data 를 바뀐 사양과 비교한다. SRS 번호가 같다는 이유만으로 덮는다고 보지 않는다. TC 마다 조치를 정한다.

| 조치 | 뜻 |
|---|---|
| `KEEP` | 그대로 둔다 |
| `UPDATE_EXISTING` | 거의 덮지만 Step 이나 Expected 가 옛 사양 기준이다. 고칠 곳을 적는다 |

4. Coverage 를 판정한다.

| 판정 | 뜻 |
|---|---|
| `FULLY_COVERED` | 기존 TC 가 바뀐 요구사항의 핵심 동작과 Expected 를 검증한다 |
| `PARTIALLY_COVERED` | 관련 TC 는 있지만 새 조건·새 상태 변화·새 Expected·경곗값·연동 가운데 빠진 것이 있다 |
| `NOT_COVERED` | 바뀐 내용을 검증하는 TC 가 없다 |
| `SPEC_REVIEW_REQUIRED` | 사양이 불명확해 Expected 를 정할 수 없다 |

5. 아래 경우를 경고(`alerts`)로 표시한다.

| 경고 | 뜻 |
|---|---|
| `ISSUE_WITHOUT_TC` | 관련 이슈는 있는데 그것을 검증하는 TC 가 없다 |
| `TC_EXPECTED_OUTDATED` | 관련 TC 는 있지만 Expected 가 바뀌기 전 사양 기준이다 |
| `FIXED_ISSUE_WITHOUT_REGRESSION_TC` | 과거 수정 완료 이슈와 새 SRS 가 이어지는데 Regression TC 가 없다 |
| `SPEC_ISSUE_BEHAVIOR_CHANGED` | 과거 Spec 판정 이슈에서 정한 동작이 최신 SRS 와 다르다 |

6. `PARTIALLY_COVERED`·`NOT_COVERED` 이고 사양 근거가 충분할 때만 신규 TC 초안(`CREATE_NEW`)을 쓴다. 기존 TC 수정으로 충분하면 초안을 쓰지 않는다. 초안 관점은 변경과 관계있는 것만 고른다.

| 관점 | 뜻 |
|---|---|
| `DIRECT_SPEC` | 바뀐 사양 자체 |
| `STATE_TRANSITION` | 상태 변화 |
| `PERSISTENCE` | 저장·다시 불러오기 |
| `BOUNDARY` | 경곗값 |
| `NEGATIVE` | 잘못된 입력 |
| `REGRESSION` | 기존 동작 |
| `INTEGRATION` | 연동 |
| `CONFIGURATION` | 설정 |
| `ENVIRONMENT` | 환경 |

   초안마다 왜 필요한지(`rationale`)를 적는다.

7. `SPEC_REVIEW_REQUIRED` 면 초안을 만들지 않고 "Checklist TC 생성 보류" 와 확인할 점을 적는다.

**결과** Finding 하나(분석 종류 `TC_CHECK`, 판정 = Coverage). 구획에 변경 내용, 이슈 Coverage, Checklist Coverage, 부족한 점, 경고, 권고 TC 변경이 있다. 관련 이슈·TC·초안의 연결은 `related_ids` 로 복원할 수 있다.

**안 될 때**

| 경우 | 응답 |
|---|---|
| 원래 분석이 사양 변경 분석이 아님 | 400 "사양 변경 분석에서만 TC 점검을 할 수 있습니다." |
| 없는 분석 번호 | 404 "Finding 이 없습니다." |
| 다른 실행 중 | 409 "QA Agent가 이미 실행 중입니다." |

**지킬 것**

- 같은 버튼을 다시 눌러도 같은 대상·판정·초안 제목의 Finding 이 열려 있으면 저장하지 않는다(REQ-DAILY-018).
- 개편 전에 자동으로 만든 Coverage Finding(분석 종류 `SPEC_COVERAGE`, Skill `qa-spec-coverage-analysis`)은 지우지 않고 그대로 보인다.

### REQ-QAINTEL-017 분석 결과 근거 검증

**하는 일** Claude 가 돌려준 결과의 번호와 근거가 실제 자료에 있는지 코드가 확인한다. 없는 번호는 사용자에게 보이지 않는다.

**순서**

1. 결과 파일 전체의 형식은 REQ-DAILY-007 과 같이 먼저 검사한다. 형식이 틀리면 한 번 더 실행한다.
2. 이슈 번호(중복 후보·과거 이슈·관련 이슈)는 오늘 이슈 스냅샷에 있어야 한다. 대상 이슈 자신을 중복 후보로 낸 것도 뺀다.
3. SRS 번호는 오늘 SRS 스냅샷의 번호나 Legacy 번호여야 한다.
4. TC 는 TC 색인의 TC ID 나 `파일 / 시트 / N행` 위치여야 한다.
5. 사양서 조각 근거는 작업 입력으로 준 조각 번호(`ref`)여야 한다. 댓글 근거는 작업 입력의 댓글 번호여야 한다.
6. 맞지 않는 항목은 결과에서 빼고, 뺀 항목과 이유를 Finding 의 검증 기록(`sections.validation`)에 남긴다.
7. 판정을 뒷받침해야 하는데 근거가 하나도 남지 않으면 Finding 을 버린다. 근거가 없음을 뜻하는 판정(`SPEC_UNDEFINED`, `SPEC_AMBIGUOUS`, `INSUFFICIENT_EVIDENCE`, `SPEC_NOT_FOUND`, `SPEC_REVIEW_REQUIRED`, `NO_DUPLICATE_FOUND`, `SPEC_UNCLEAR`)은 근거 없이도 남기되 신뢰도를 `Review Needed` 보다 높이지 않는다.
8. 중복 판정이 `STRONG_DUPLICATE`·`POSSIBLE_DUPLICATE` 인데 남은 후보가 없으면 `INSUFFICIENT_EVIDENCE` 로 바꾼다.
9. TC 초안 규칙
   - 신규 이슈·Spec 판정·댓글·현재 상태 점검이 초안을 내면 Finding 을 버린다.
   - 자동 실행의 수정 완료 이슈 분석·사양 변경 분석이 초안이나 TC 비교 구획을 내면 그 부분만 빼고 메모를 남긴다.
   - 검증 TC 초안 요청(REQ-QAINTEL-032)에서 대상 이슈의 오늘 연구소 결과가 `FIXED` 가 아니면 Finding 을 버린다.
   - 사양 판정이 `SPEC_UNDEFINED`·`INSUFFICIENT_EVIDENCE`·`SPEC_AMBIGUOUS` 이거나 Coverage 가 `FULLY_COVERED`·`SPEC_REVIEW_REQUIRED` 면 초안을 빼고 "Checklist TC 생성 보류" 를 남긴다.
10. 조치 문장이 QA 규칙 §55 금지 조치(이슈 닫기, TC 덮어쓰기, 결과·이력 삭제)를 담으면 Finding 을 버린다(REQ-DAILY-007 과 같다).

**결과** 저장한 Finding, 버린 Finding 수(단계 비고 "규칙 위반으로 버린 Finding N건"), Finding 별 검증 기록.

### REQ-QAINTEL-018 초안 Excel

**하는 일** 이번 실행의 검증 TC 초안과 Coverage 조치를 영향성평가 Checklist 와 같은 열 순서의 Excel 한 파일(`impact_checklist_draft.xlsx`)로 만든다. REQ-DAILY-019 의 형식을 넓힌다.

**순서**

1. `Checklist 초안` 시트에는 신규 초안(검증 TC 초안 요청의 초안, TC 점검의 `CREATE_NEW`)만 넣는다. 열 이름과 순서는 제품 설정의 Checklist 형식(`qa_intelligence.checklist.headers`)이고, 원본 Checklist 가 있으면 머리글 서식과 열 너비를 복사한다.
2. `Coverage` 시트에는 SRS 변경마다 TC 별 조치(`KEEP`, `UPDATE_EXISTING`, `CREATE_NEW`, `SPEC_REVIEW_REQUIRED`), 이유, 추천 수정, 관련 이슈, Finding 번호를 넣는다.
3. `Review` 시트에는 Finding 별 판정·요약·근거 위치·신뢰도를 넣는다.

**결과** 실행 폴더의 `impact_checklist_draft.xlsx`. 이번 실행에 초안이나 TC 점검 Finding 이 하나라도 있을 때만 만든다. 자동 실행은 초안이 없으므로 보통 만들지 않는다.

### REQ-QAINTEL-019 QA Agent 대시보드

**하는 일** `/qa-agent` 한 화면에서 마지막 실행 결과, 다음 자동 실행, 오늘 바뀐 것의 요약, 최신 분석 결과를 빠르게 본다.

**언제** 브라우저로 `/qa-agent` 를 연다. 옛 주소 `/daily-qa` 는 `/qa-agent` 로 넘긴다.

**화면 구성**

1. 맨 위: 수동 업로드 지식 문서 목록(REQ-QAINTEL-022).
2. 실행: 마지막 실행(시각·결과), 실행 결과 요약, 다음 자동 실행 시각(주말·공휴일 제외), [지금 실행] 버튼.
3. 오늘 변경 요약: 마지막 실행의 신규 이슈, Fixed(연구소 결과가 `FIXED` 로 바뀐 이슈), Spec(`SPEC`·`NOT_BUG` 로 바뀐 이슈), 댓글 추가, SRS 변경 수.
4. 최신 분석 결과 카드: 결론 단계, 분석 종류, 대상 번호·제목, 한두 문장 요약, QA 할 일 3줄까지, [상세보기](REQ-QAINTEL-033). `문제 없음` 카드는 접어 둔다.
5. 최근 실행 목록: 실행마다 결과와 [실행 상세] 링크.

> **예시** `검토 필요 · 사양 변경 분석` / `[VP-516] Display X-ray Image` / "Reject 문구가 '영상 표시 마크' 표로 바뀌었다." / QA 할 일 ① Q/R 수신 영상 표시 확인 ② FTM 영상 확인

**설정** 제품이 하나면 그 제품을 고른다. 여럿이면 주소의 `?product=<이름>` 으로 고른다. 조회 함수는 모두 제품을 받는다.

**지킬 것**

- 승인·거절·근거 추가 필요·검토자 입력·질문 답변은 보이지 않는다.
- 사람이 이슈 하나를 골라 돌리는 단일 이슈 분석은 `/qa-agent/issue-analysis` 에서 연다.

> **참고** 실행 구역의 제목은 `분석 실행 · <제품>` 이다. 마지막 실행, 실행 결과, 다음 자동 실행, 분석 기간, [지금 실행]이 이 구역에 있다.

### REQ-QAINTEL-020 분석 상세 화면과 실행 상세 화면

**하는 일** Finding 하나의 결론과 할 일을 맨 위에 보이고, 분석 종류별 구획은 `자세히` 를 펼쳐야 보인다. 실행 하나의 단계·이벤트·내려받을 파일도 본다.

**화면 구성**

1. 맨 위 핵심 덩어리: 결론 단계, 판정 이름·신뢰도, 한두 문장 요약, 바뀐 사양(요구사항 5개까지), 근거 3개까지, QA 할 일 3줄까지.
2. 같은 덩어리의 버튼: 사양 변경 분석이면 [TC 점검], 수정 완료 이슈 분석이면 [검증 TC 초안 만들기]. 다른 분석에는 버튼이 없다.
3. `자세히`: 아래 표의 구획, 칩, 근거 전체, 추가 확인사항, 근거 검증 기록, 변경 이벤트.

| 분석 종류 | `자세히` 의 구획 |
|---|---|
| 신규 이슈 | Summary, Duplicate Analysis, Specification Analysis, Historical Analysis, QA Recommendation, Evidence |
| 수정 완료 이슈 | Root Cause Review, Resolution Review, Specification Consistency, Regression Risk(초안이 있을 때만 Verification TC) |
| 검증 TC 초안(요청) | 수정 완료 이슈와 같고 Verification TC 가 있다 |
| Spec 판정 이슈 | Summary, R&D Claim, Specification Evidence, Historical Decisions, QA Analysis, Evidence |
| 새 댓글 | New Comment, Classification, Impact, Evidence |
| 사양 변경 | 관련 과거 이슈 |
| TC 점검(요청)·개편 전 Coverage | Specification Change, Historical Issue Coverage, Checklist Coverage, Coverage Gap, Recommended TC Changes |

- 모든 상세 화면 아래에 이 Finding 을 만든 이벤트와 근거 검증 기록(뺀 항목과 이유)이 있다.
- 개편 전 Finding 은 판정·요약·근거·초안만 보인다.
- 실행 상세(`/qa-agent/runs/<실행 ID>`)는 처음 보는 사람이 위에서부터 읽으면 무슨 일이 있었는지 알 수 있게 아래 순서로 보인다.

  1. **결과 한 줄**: 결과 배지와 한두 문장 설명. 예: "Polarion 에서 SRS 437건·이슈 647건을 읽어 바뀐 SRS 1건을 찾았습니다. 시험 실행이라 저장·AI 분석·메일은 하지 않았습니다."
  2. **숫자 카드**: 읽은 SRS, 바뀐 SRS, 읽은 이슈, TC 행, 새 변경, 분석 결과, Claude 호출·토큰. 값이 없는 카드는 `-` 다.
  3. **진행 흐름**: `① 자료 모으기 → ② 바뀐 것 찾기 → ③ AI 분석 → ④ 결과` 네 칸. 칸마다 그 안 단계의 상태를 색(완료 초록, 주의 노랑, 실패 빨강, 건너뜀 회색)으로 보인다.
  4. **단계별 설명**: 단계마다 상태와, 숫자를 풀어 쓴 문장 한 줄("SRS 437건을 읽었고 1건이 바뀌었습니다"). 내부 이름의 숫자(`total`, `type_SRS_UPDATED` 등)는 한국어 이름으로 바꿔 `자세히` 를 펼칠 때만 보인다.
  5. 변경 이벤트, 분석 결과, 내려받기. 내려받을 파일은 뜻을 쓴다(예: `srs_diff.json` → "SRS 변경 내용").

- 화면에 내부 값을 그대로 쓰지 않는다. `dry-run` 은 "시험 실행", 메일 상태 `disabled` 는 "메일 보내지 않음", 요일 `mon` 은 "월요일" 이다.
- 시험 실행에서 AI 를 부르지 않은 작업은 "실패"가 아니라 "준비한 작업 N개(시험 실행이라 AI 를 부르지 않음)"로 보인다.
- 실행 상세의 단계 상태 이름(`오늘은 대상 아님` 등)은 한 줄로 보인다.

**안 될 때**

| 경우 | 사용자에게 보이는 것 |
|---|---|
| 없는 Finding·실행 | 404 "Finding 이 없습니다." / "실행 기록이 없습니다." |

### REQ-QAINTEL-021 지금 실행

**하는 일** 대시보드의 [지금 실행] 버튼으로 예약 실행과 같은 점검을 바로 돌린다.

**순서**

1. 버튼은 `POST /qa-agent/runs` 에 제품 이름을 보낸다.
2. 서버는 그 제품의 실행 잠금(`data/daily_qa/<slug>/run.lock`)을 확인한다. 잠금이 있고 6시간이 지나지 않았으면 409 "QA Agent가 이미 실행 중입니다." 로 답한다.
3. 잠금이 없으면 예약 실행과 같은 함수(`launch_detached`)로 분리된 별도 프로세스를 띄우고 바로 답한다. 웹 프로세스는 분석을 기다리지 않는다.
4. 화면은 `GET /qa-agent/status` 를 몇 초마다 읽어 실행 상태를 갱신한다.

**결과** 202 `{"status": "launched", "pid": <번호>}`.

**안 될 때**

| 경우 | 응답 |
|---|---|
| 이미 실행 중 | 409 "QA Agent가 이미 실행 중입니다." |
| `daily_qa.enabled` 가 false | 409 "QA Agent 가 꺼져 있습니다 (daily_qa.enabled)." |
| Polarion 설정 없음(종료일이 오늘인 실행) | 409 "Polarion 설정(POLARION_HOST / POLARION_TOKEN / 프로젝트)이 없습니다." |
| 모르는 제품 | 404 "등록된 제품이 아닙니다." |

> **참고** 종료일이 지난 날인 기간 실행은 Polarion 을 읽지 않고 저장 스냅샷만 쓴다(REQ-QAINTEL-027). 그래서 Polarion 설정이 없어도 띄운다.

**지킬 것**

- 수동 실행용 분석 코드를 따로 두지 않는다. 예약 실행과 같은 CLI·같은 파이프라인이다.
- 버튼 검사와 별개로 실행 프로세스의 잠금(REQ-DAILY-001)이 동시 실행을 막는다. 버튼과 예약이 겹치면 늦게 시작한 쪽이 종료 코드 3 으로 끝난다.

### REQ-QAINTEL-022 수동 업로드 지식 문서 목록

**하는 일** 대시보드 맨 위에 담당자가 직접 올려야 하는 지식 문서(매뉴얼, TC·Checklist, 그리고 ALM 자동 추출이 아닌 제품의 사양서)가 무엇이고 언제 서버에 올라왔는지 보여 준다.

> **예시** VXvue 는 사양서를 ALM 에서 자동 추출한다(제품 설정 `specification.source: alm_crawler`). 그래서 VXvue 목록에는 매뉴얼과 TC·Checklist 만 보인다. ALM 을 쓰지 않는 제품은 사양서도 이 목록에 보인다. 어느 종류가 자동인지는 제품 설정의 자료 출처(REQ-KNOW-018)로 정한다.

> **참고** QA 규칙(`.md`)과 지침 프롬프트 파일은 지식 문서로 관리하지 않으므로 이 목록에 보이지 않는다(사용자 결정 2026-09-30). 점검은 규칙 판 확인(REQ-DAILY-010)과 Skill 입력에 그 파일을 계속 쓴다.

> **참고** 두 파일은 지식 폴더 수집(REQ-SYNC-002)으로 계속 서버에 올라온다. 나중에 만들 챗봇이 같은 사본을 읽도록 수집 목록에서 지우지 않는다. 이 화면에만 보이지 않는다.

**입력** 제품 지식 사본의 목록 파일(`data/product_knowledge/<slug>/manifest.json`, 지식 폴더로 올린 문서)과 Knowledge 화면으로 등록한 문서(등록 문서 표).

**화면 구성**

| 열 | 값 |
|---|---|
| 종류 | 사양서(자동 추출 제품이 아닐 때) / 매뉴얼 / TC·Checklist |
| 파일 | 파일 이름 |
| 서버 업로드 | 서버가 받은 시각(`collected_at`, 한국 시간) |
| 파일 수정일 | 담당자 PC 의 파일 수정 시각(`modified`, 한국 시간). 화면으로 등록한 문서는 빈칸 |
| 올린 경로 | 지식 폴더 / Knowledge 화면 등록 |

- 표 위에 안내 문장 "정확한 분석을 위해 매뉴얼과 TC 가 최신이 아니라면 최신 버전을 업로드해 주세요." 를 보인다(사용자 결정 2026-10-02).
- 표 위에 마지막 수집 시각(`synced_at`)을 보인다.
- 읽지 못한 파일(`error`)은 "읽지 못함" 으로 표시한다.
- 같은 문서(REQ-KNOW-009 의 논리 문서)의 판이 여럿 올라와 있으면 가장 최신 판 하나만 보인다. 판을 비교할 수 없으면 둘 다 보인다.
- 판(리비전·버전)은 보이지 않는다. 같은 문서의 최신본 하나만 쓰므로 운영자가 판을 볼 필요가 없다(REQ-KNOW-020 과 같다).

**지킬 것**

- 종류 이름(`TC·Checklist` 등)은 화면 폭과 상관없이 한 줄로 보인다. 좁은 화면에서는 표가 가로로 스크롤된다.

**안 될 때**

| 경우 | 사용자에게 보이는 것 |
|---|---|
| 목록 파일이 없음 | "수집된 지식 문서가 없습니다. 담당자 PC 의 지식 폴더 업로드(REQ-SYNC-002)를 확인하세요." |

### REQ-QAINTEL-023 제품별 데이터 분리

**하는 일** 같은 서버·같은 DB 에서 여러 제품을 돌려도 데이터가 섞이지 않게 모든 기록에 제품을 붙인다.

**순서**

| 데이터 | 제품을 가르는 방법 |
|---|---|
| 실행 기록(`daily_qa_runs`) | `product` 열. 실행 ID 끝에 slug 를 붙인다(`20260930-073000-vxvue`) |
| 스냅샷 | `data/daily_qa/snapshots/<slug>/srs/`, `.../issues/` |
| 변경 이벤트(`qa_change_events`) | `product` 열 |
| Finding(`daily_qa_findings`)·질문 | `product` 열 |
| 상태 값(`daily_qa_state`) | 키 앞에 `<slug>:` 를 붙인다 |
| 실행 잠금 | `data/daily_qa/<slug>/run.lock` |
| 격리 작업 폴더 | `<workspace_dir>/<slug>/` |

> **참고** 개편 전 기록은 `product` 열이 비어 있다. 조회할 때 `daily_qa.product` 로 적힌 제품의 기록으로 본다. 열을 채우는 옮기기 작업은 하지 않는다.

**지킬 것**

- 한 제품의 이벤트·Finding·스냅샷을 다른 제품의 비교·분석·중복 방지에 쓰지 않는다.

### REQ-QAINTEL-024 기간 분석 조회

**하는 일** 시작일과 종료일을 골라 그 기간에 쌓인 변경 이벤트와 분석 결과를 한 화면에서 모아 본다. 검증 의뢰가 없는 기간에는 매일 확인하지 않다가, 다음 검증을 준비할 때 한 번에 몰아 보기 위해서다.

**언제** 대시보드의 기간 조회 양식에서 날짜를 고르고 [조회]를 누른다. 주소는 `/qa-agent/period?product=<이름>&start=YYYY-MM-DD&end=YYYY-MM-DD` 다.

**순서**

1. 시작일 0시부터 종료일 24시까지(한국 시간) 만든 실행·이벤트·Finding 을 모은다. 날짜를 비우면 최근 7일이다.
2. 같은 대상(이슈·SRS)의 Finding 은 대상별로 묶고 가장 최근 것을 앞에 둔다.
3. 기간 요약을 만든다. 실행 수(결과별), 이벤트 종류별 수, 분석 종류별 Finding 수, 초안 TC 수.

**결과**

| 구획 | 담는 것 |
|---|---|
| 기간 요약 | 실행 수, 변경사항 없음 실행 수, 이벤트 종류별 수, 분석 종류별 Finding 수, 초안 TC 수 |
| 대상별 분석 | 대상 번호마다 분석 카드(분석 종류·판정·요약·[상세보기]). 같은 대상의 이전 분석은 접어서 보인다 |
| 기간의 이벤트 | 이벤트 종류로 거를 수 있는 목록 |

**안 될 때**

| 경우 | 사용자에게 보이는 것 |
|---|---|
| 날짜 모양이 틀림 | 400 "날짜는 YYYY-MM-DD 로 입력하세요." |
| 시작일이 종료일보다 늦음 | 400 "시작일이 종료일보다 늦습니다." |
| 기간이 366일을 넘음 | 400 "조회 기간은 366일까지입니다." |

### REQ-QAINTEL-025 Claude 사용량 한도와 실행 예외

**하는 일** Claude 세션(5시간) 한도·주간 한도·인증 실패로 AI 분석을 못 하면 남은 AI 작업을 멈추고, 언제 초기화되는지 화면·수동 실행 응답·메일에 알린다. 한도가 풀리면 밀린 분석을 이어서 한다. 변경 감지는 한도와 상관없이 계속한다.

**언제** Claude 작업이 실패하고 실패 문장이 한도·인증 문제일 때. 예약 실행, [지금 실행], 한도가 풀린 뒤 다시 도는 실행(catch-up) 모두 같다.

**순서**

1. 실패 문장에서 한도 종류와 초기화 시각을 코드가 읽는다(`app/modules/daily_qa/claude_limits.py`).

| 종류 | 알아보는 문장(예) | 초기화 시각 |
|---|---|---|
| 세션 한도(`SESSION`) | `5-hour limit reached ∙ resets 3pm`, `usage limit reached\|<유닉스 초>`(6시간 안에 풀리는 것) | 문장의 시각 |
| 주간 한도(`WEEKLY`) | `Weekly limit reached ∙ resets Oct 6, 9am` | 문장의 날짜·시각 |
| 한도(종류 모름, `UNKNOWN`) | 시각이 없는 한도 문장 | 모름 |
| 인증 실패(`AUTH`) | `OAuth token has expired`, `Invalid API key`, `API Error: 401`, 줄 맨 앞의 `401 Unauthorized` | 없음. 토큰을 바꿀 때까지 |
| CLI 없음(`CLI_MISSING`) | Claude CLI 를 찾을 수 없음 | 없음. 설치할 때까지 |

   작업 결과 문장 안의 `HTTP 401`, `unauthorized` 같은 말은 인증 실패로 보지 않는다.

   이유: 인증 실패는 토큰을 바꿀 때까지 AI 를 막는다. DICOM·HTTP 이슈를 분석한 결과에 401 이 들어 있다고 AI 가 오래 멈추면 안 된다.

2. 한도가 걸린 작업은 다시 돌리지 않는다. 그 실행에서 아직 돌지 않은 AI 작업도 돌리지 않는다.
3. 분석 대상 이벤트는 `pending` 으로 남기고 실패 횟수를 올리지 않는다.

   이유: 한도는 이벤트 탓이 아니다. 횟수를 올리면 멀쩡한 이벤트가 포기(`abandoned`)로 바뀐다.

4. 한도 정보를 상태 값 `<slug>:claude_limit` 에 저장한다. 다음 실행은 초기화 시각 전이면 Claude 를 부르지 않고 AI 단계를 `Claude 사용량 한도` 로 남긴다. 사전 점검 단계는 `일부 실패`(`partial`)이고 비고에 한도 설명이 들어간다. 인증 실패·CLI 없음은 토큰(또는 CLI)이 바뀔 때까지 같다. 초기화 시각을 모르면 다음 실행이 한 번 다시 시도한다.

5. 세션 한도이고 초기화가 `daily_qa.intelligence.catchup_max_hours`(기본 12)시간 안이면, 초기화 `catchup_delay_minutes`(기본 5)분 뒤에 한 번 다시 돈다. 앱 안의 감시 예약(`qa_agent_limit_catchup`, 10분마다)이 시각이 지난 제품을 [지금 실행]과 같은 함수로 띄운다.

   - 그 제품의 다른 실행이 돌고 있어 띄우지 못하면 catch-up 기록을 지우지 않는다. 10분 뒤 감시 예약이 다시 띄운다.
   - 토큰 없이 Claude CLI 로그인으로 부르는 PC 는 토큰 지문이 늘 같다. 그래서 사전 점검이 로그인을 다시 확인하면 남아 있던 인증 실패 기록을 지우고 분석을 이어 간다(REQ-DAILY-001 4번).
6. 주간 한도는 다시 돌지 않는다. 평일 예약 실행이 그대로 돌며 변경 감지와 이벤트 저장만 하고, 초기화 뒤 첫 실행이 밀린 이벤트를 상한(NFR-DAILY-001) 안에서 분석한다. 상한을 넘은 것은 다음 실행으로 넘어간다.

**결과**

| 보이는 곳 | 내용 |
|---|---|
| 대시보드 맨 위 알림 | "Claude 세션(5시간) 사용량 한도 초과. 2026-09-30 15:00(한국 시간)에 초기화됩니다. 그동안 변경 감지는 계속하고 AI 분석은 대기로 남깁니다." |
| [지금 실행] 응답 | 202 에 `warning` 으로 같은 문장. 실행은 변경 감지만 한다 |
| 요약 메일 | 첫 줄에 같은 문장 |
| 실행 상세·단계 | 단계 상태 `Claude 사용량 한도`, 실행 결과 `일부 실패` |
| CLI(`--check`) | `[주의] Claude 사용량 한도 — <설명>` |

**실행 예외 한눈에 보기**

| 상황 | 동작 | 기준 스냅샷 | 이벤트 |
|---|---|---|---|
| 공휴일(예약) | 프로세스를 띄우지 않음 | 그대로 | 없음 |
| 다른 실행이 진행 중 | 409(버튼) 또는 종료 코드 3(CLI) | 그대로 | 없음 |
| Polarion 요청 실패·0건·이슈 급감 | 그 수집 단계 실패 | 옮기지 않음 | 만들지 않음 |
| 스냅샷 쓰기 실패 | 그 수집 단계 실패 | 옮기지 않음 | 만들지 않음 |
| 이벤트 저장 실패 | 오늘 스냅샷을 되돌림 | 옮기지 않음 | 없음 |
| 한 이슈의 댓글 읽기 실패 | 어제 댓글을 옮겨 적음 | 옮김 | 그 이슈 댓글 비교만 다음 실행으로 |
| 사양서 문서를 읽지 못함 | SRS 후보만으로 분석 | 옮김 | 정상 |
| QA 규칙 판 불일치·토큰 없음 | AI 단계를 돌리지 않음 | 옮김 | `pending` 유지 |
| Claude 한도·인증 실패 | 남은 AI 작업 중지 | 옮김 | `pending` 유지(횟수 그대로) |
| Claude 작업 실패(형식 오류 2회, 시간 초과) | 그 작업만 실패 | 옮김 | `failed`, 3회면 `abandoned` |
| 분석 대상이 오늘 스냅샷에 없음 | 분석하지 않음 | 옮김 | `abandoned`(횟수 그대로) |
| 결과의 번호가 실제 자료에 없음 | 그 번호만 뺌 | 옮김 | `done` |
| 메일 발송 실패 | 실행 기록의 메일 결과에 남김 | 옮김 | 정상 |

**설정**

| 키 | 기본값 | 뜻 |
|---|---|---|
| `daily_qa.intelligence.catchup_max_hours` | `12` | 세션 한도 초기화가 이 시간 안이면 다시 돈다 |
| `daily_qa.intelligence.catchup_delay_minutes` | `5` | 초기화 뒤 몇 분 뒤에 다시 돌지 |
| `daily_qa.intelligence.event_max_attempts` | `3` | 이벤트 분석 실패를 몇 번까지 다시 시도할지 |

**지킬 것**

- 한도 문장을 읽지 못해도 실패를 한도로 꾸미지 않는다. 보통 실패로 기록한다.
- 한도 정보에 토큰을 남기지 않는다. 토큰이 바뀌었는지는 지문(앞 12자리 해시)으로만 본다.

### REQ-QAINTEL-026 Claude 사용량을 비용 대시보드에 기록

**하는 일** QA Agent 점검(예약·수동·catch-up)이 Claude CLI 로 쓴 토큰을 실행마다 모아 비용 대시보드와 하루 토큰 한도에 넣는다. 화면 기능(REQ-AICALL-005)의 Claude 사용량과 같은 곳에서 본다.

**순서**

1. Claude 작업을 한 번 돌릴 때마다 CLI 결과의 사용량(`usage`: 입력·출력·캐시 생성·캐시 읽기 토큰)과 추정 비용(`total_cost_usd`)을 더한다. 형식 오류로 다시 돈 시도와 실패한 시도도 이미 쓴 토큰이라 센다.
2. 합계를 실행 기록 요약(`daily_qa_runs.summary_json` 의 `token_usage`)에 저장한다. `total_tokens` 는 네 토큰의 합이다.
3. 비용 대시보드(REQ-COST-001)는 이 합계를 기능 "QA Agent 점검(예약·수동, Claude CLI)"(`qa_agent_run`)으로 날짜별·기능별 합계와 최근 목록에 더한다. 최근 목록의 실행 ID 는 실행 상세로 이어진다.
4. 하루 토큰 한도(REQ-USAGE-002)의 "오늘 사용량"에도 더한다.

**결과** 실행 상세의 Claude 호출 수, 비용 대시보드의 `QA Agent 점검` 행.

> **참고** Claude CLI 는 응답 저장본(캐시)을 쓰지 않으므로 캐시 Hit율 계산에서는 뺀다.

### REQ-QAINTEL-027 기간을 정한 수동 실행

**하는 일** [지금 실행] 때 시작일과 종료일을 달력으로 골라 그 기간의 변경을 분석한다. 기본값은 시작일 전날, 종료일 오늘이다. 종료일만 주면 시작일은 종료일 전날이다.

**언제** 대시보드의 실행 구역에서 두 날짜를 고르고 [지금 실행]을 누른다. CLI 는 `--since YYYY-MM-DD --until YYYY-MM-DD` 다.

**순서**

1. 비교 기준은 시작일 날짜(또는 그 전 가장 가까운) 스냅샷이다. 시작일 전에 스냅샷이 하나도 없으면 가장 오래된 스냅샷을 기준으로 삼는다.
2. 종료일이 오늘이면 Polarion 을 새로 읽어 오늘 스냅샷을 저장한다(보통 실행과 같다).
3. 종료일이 지난 날이면 새로 읽지 않는다. 그날(또는 그 전 가장 가까운) 저장 스냅샷과 기준을 비교하고, 스냅샷을 저장하지 않는다.

   - 사양–TC 연결 점검(REQ-DAILY-005) 같은 다른 단계도 종료일 이전 스냅샷만 쓴다. 종료일 뒤 스냅샷은 쓰지 않는다.
   - 대기·실패 이벤트(재시도 대기열, REQ-QAINTEL-006)는 다루지 않는다. 이 실행이 새로 저장한 이벤트만 분석한다.
4. 매일 실행이 이미 찾은 끝 상태는 다시 이벤트로 만들지 않는다. 그래서 이미 분석한 변경은 다시 분석하지 않는다. 같은 대상의 저장된 이벤트와 아래처럼 비교한다.

   - 필드 변경: 바뀐 필드마다 그 대상의 가장 최근 값과 같으면 이미 안다. 제목이 이틀째, 본문이 사흘째 바뀌었어도 기간 실행의 이벤트 하나를 매일 실행의 두 이벤트로 알아본다.
   - 새 댓글: 새 댓글 번호를 모두 이미 본 댓글 이벤트가 담고 있으면 이미 안다.
   - 신규: 같은 대상의 신규 이벤트가 있으면 이미 안다. 그 뒤 상태가 바뀌었어도 같다.
   - 삭제: 그 대상의 마지막 신규·삭제 이벤트가 삭제면 이미 안다.
5. 나머지는 예약 실행과 같다(같은 CLI, 같은 파이프라인, 같은 잠금).

**안 될 때**

| 경우 | 응답 |
|---|---|
| 날짜 모양이 틀림 | 400 "날짜는 YYYY-MM-DD 로 입력하세요." |
| 종료일이 오늘보다 늦음 | 400 "종료일은 오늘보다 늦을 수 없습니다." |
| 시작일이 종료일보다 늦음 | 400 "시작일이 종료일보다 늦습니다." |
| 종료일이 지난 날인데 그날 이전 SRS 스냅샷이 없음 | 400 "<종료일> 이전에 저장된 SRS 스냅샷이 없어 이 기간은 분석할 수 없습니다. 가장 오래된 스냅샷: <날짜>" (스냅샷이 하나도 없으면 "저장된 SRS 스냅샷이 없습니다.") |
| 종료일 이전 저장 스냅샷이 없음(CLI 로 직접 실행) | 수집 단계 `실패` "<날짜> 이전 저장 스냅샷이 없습니다." |

- 이슈 스냅샷이 그 기간에 없으면 SRS 변경만 분석한다. 이슈 수집 단계는 `실패` 이고 실행 결과는 `일부 실패` 다. [지금 실행] 응답(202)의 `warning` 에 "이 기간에는 저장된 이슈 스냅샷이 없어 SRS 변경만 분석합니다." 를 붙인다.
- 화면의 시작일 달력은 가장 오래된 SRS 스냅샷 날짜보다 앞을 고를 수 없다.

### REQ-QAINTEL-028 삭제된 SRS 를 가리키는 TC 찾기

**하는 일** 오늘 SRS 에서 없어졌는데 아직 TC 가 가리키는 SRS 를 찾아, 고쳐야 할 TC 를 알린다. AI 를 부르지 않는다.

**언제** 매 실행. SRS 수집이 성공하고 TC 색인이 있을 때.

**순서**

1. SRS 비교 결과(REQ-QAINTEL-003)의 삭제 목록을 TC 색인과 맞춘다. 새 번호와 옛 번호를 모두 본다.
2. 삭제 SRS 를 가리키는 TC 행마다 Finding 하나를 만든다. 같은 SRS 를 가리키는 TC 가 여럿이면 TC 마다 남는다(REQ-DAILY-018).
3. 오늘 `SRS_REMOVED` 이벤트가 있는 SRS 면 Finding 에 "오늘 SRS 에 없습니다" 변경 구획을 붙인다.

**결과** Finding: Skill 이름 `qa-spec-coverage-analysis`, 작업 ID `COV-removed`, 분석 종류 `SRS_REMOVED`, 판정 `UPDATE_EXISTING`, 관련 번호(SRS·TC). 대시보드와 초안 Excel 의 검토 목록에 보인다.

**지킬 것**

- 이 계산은 개편 전 사양 변경 영향 검토(REQ-DAILY-003, deprecated)의 삭제 계산을 이어받은 것이다. 시험 실행(`--dry-run`)은 Finding 을 저장하지 않는다.

### REQ-QAINTEL-029 ALM-QA-Automation 의 과거 SRS 스냅샷 가져오기

**하는 일** ALM-QA-Automation 의 사양서 자동화(`apps/srs-spec`)가 날짜마다 남긴 SRS 스냅샷을 이 시스템의 SRS 스냅샷으로 바꿔 넣는다. 그러면 이 시스템을 쓰기 전 기간도 기간 실행(REQ-QAINTEL-027)으로 분석할 수 있다.

> **예시** `srs-spec/snapshots/2026-08-31/VXvue/VP-1277.json` 같은 파일 437개가 `data/daily_qa/snapshots/vxvue/srs/2026-08-31.json` 하나가 된다. 그 뒤 [지금 실행]에서 2026-08-31 ~ 2026-09-22 를 고르면 그 사이 SRS 변경을 분석한다.

**언제** 담당자가 ALM-QA-Automation 이 있는 PC 에서 `scripts/import_alm_srs_history.py` 를 실행할 때. 여러 번 돌려도 결과가 같다.

**입력**

| 값 | 뜻 |
|---|---|
| `--source` | `srs-spec` 의 스냅샷 폴더. 날짜 폴더(`YYYY-MM-DD`) 아래에 ALM 프로젝트 폴더가 있다. 비우면 `daily_qa.alm_history.srs_snapshot_dir` |
| `--product` | 제품 이름. 그 제품 설정의 `alm.project_id` 폴더만 읽는다 |
| `--dry-run` | 쓰지 않고 날짜마다 무엇을 할지만 보인다 |

**순서**

1. 날짜 폴더마다 그 제품 프로젝트 폴더의 JSON(SRS 하나에 파일 하나)을 모두 읽는다.
2. 원본 필드(`description_kr_raw`·`description_raw`·`old_id`·`title`·`status`·`updated`·`is_category`)를 Polarion 응답 모양으로 되돌린다. 그 뒤 매일 실행과 같은 함수로 공통 모델로 바꾼다(REQ-QAINTEL-002·003). 본문 링크 풀기도 같다.
3. 끝에 다른 글자가 붙어 읽지 못하는 파일은 되살릴 수 있을 때만 되살린다.

   - 파일 앞부분이 온전한 JSON 하나이고, 그 뒤에 붙은 글자가 앞 JSON 의 끝부분과 글자 그대로 같으면 앞 JSON 을 쓴다.
   - 그 밖의 깨진 파일은 되살리지 않는다.

   > **예시** 2026-09-07·09-21 은 `srs-spec` 이 같은 날 두 번 동시에 돌아, 짧은 쓰기 뒤에 긴 쓰기의 마지막 100여 글자(`"type": "srs", "uid": …}`)가 남았다. 남은 글자가 앞 JSON 의 끝과 같아 앞 JSON 이 그날의 온전한 기록이다.

4. 그 날짜의 `manifest.json` 이 적은 개수(`expected_total`)와 읽은 개수가 다르거나, 되살리지 못한 파일이 하나라도 있으면 그 날짜를 건너뛴다.

   이유: 일부만 넣으면 빠진 SRS 가 그날 삭제된 것으로 보인다.

5. 이 시스템에 그 날짜 스냅샷이 이미 있으면 덮어쓰지 않는다. 매일 실행이 직접 읽은 것을 먼저 믿는다.
6. 수집 시각은 `manifest.json` 의 `generated_at` 이다. 스냅샷에 출처 `alm_qa_automation` 을 적는다. 되살린 파일이 있으면 그 이름을 스냅샷의 `restored_files` 에 적는다.

**결과** 날짜마다 `가져옴`·`이미 있음`·`건너뜀(<이유>)` 한 줄과 합계. 되살린 날짜는 `가져옴 437건 (되살린 파일 10개)` 처럼 보인다. 종료 코드는 건너뛴 날짜가 있어도 0, 설정·폴더 오류면 2 다.

**지킬 것**

- `srs-spec` 폴더에는 쓰지 않는다. 읽기만 한다.
- 이슈 기록은 가져오지 않는다. ALM-QA-Automation 의 이슈 내보내기(`apps/issue-export`)는 요청한 이슈의 지금 상태만 남기고 날짜별 이슈 전체를 남기지 않는다. 이슈 스냅샷은 이 시스템의 첫 매일 실행부터 쌓인다(REQ-QAINTEL-008).
- 서버에서 기간 실행을 하려면 가져온 스냅샷 파일을 서버의 같은 폴더에 둬야 한다(배포 절차는 `docs/modules/daily-qa.md`).

### REQ-QAINTEL-030 이슈 기록이 없는 기간의 현재 상태 기준 점검

**하는 일** 이슈 스냅샷이 없는 기간에도 사양 변화와 이슈가 맞는지 본다. 기간 동안 이슈가 어떻게 바뀌었는지는 알 수 없으므로, 지금 이슈 전체를 기간의 SRS 변화와 맞춰 본다. 결과에는 "이슈 변경 기록이 없어 현재 상태 기준으로 점검했다"는 알림이 붙는다.

> **예시** 2026-08-31 ~ 2026-09-22 를 고르고 [현재 상태 점검]을 누른다. 그 기간에 SRS 96건이 바뀌었고 그 SRS 에 연결된 이슈가 371건이다. 이슈 371건과 Spec 판정 이슈 26건이 점검 대상이 된다.

**언제** 대시보드 실행 구역의 [현재 상태 점검] 버튼(`POST /qa-agent/issue-audit`, `product`·`since`·`until`). CLI 는 `scripts/run_daily_qa.py --issue-audit --since YYYY-MM-DD --until YYYY-MM-DD` 다.

이 절의 용어:

| 용어 | 뜻 |
|---|---|
| 점검 대상 묶음 | 코드가 이슈 하나를 아래 표의 A·B·D 가운데 하나로 나눈 것 |
| 요약 카드 | AI 에 보내는 이슈 요약. 제목·상태·연구소 결과·생성일·수정일·Expected·Actual·발생 원인·조치 내용(앞부분)과 의미 있는 마지막 댓글 2개 |

**순서**

1. 이슈 전체를 지금 상태로 읽는다. 종료일이 오늘이면 Polarion 에서 새로 읽어 오늘 스냅샷으로 저장하고, 지난 날이면 그날(또는 그 전 가장 가까운) 저장 이슈 스냅샷을 쓴다.
2. 시작일 기준 SRS 스냅샷과 종료일 SRS 를 비교해 기간 안에 바뀐 SRS 를 찾는다(REQ-QAINTEL-027 과 같은 기준).
3. 이슈마다 연결된 SRS 를 찾는다. 이슈의 연결 항목(`linked_ids`)과, 제목·본문·재현 절차에 정확히 적힌 SRS 번호만 쓴다. 제목이 비슷하다고 연결하지 않는다.
4. 코드로 이슈를 나눈다. AI 를 쓰지 않는다. 위에서부터 처음 맞는 줄로 정한다.

   | 묶음 | 조건 | 볼 것 |
   |---|---|---|
   | A-수정 | 연결 SRS 가 기간 안에 바뀌었고 연구소 결과가 `FIXED` | 수정 뒤 사양이 바뀌어 이슈의 Expected 가 새 사양과 맞는지(QA 규칙 §43). TC 는 보지 않는다 |
   | A-사양 | 연결 SRS 가 기간 안에 바뀌었고 연구소 결과가 `SPEC`·`NOT_BUG` | 바뀐 사양이 연구소 판정을 뒷받침하는지 |
   | A-기타 | 연결 SRS 가 기간 안에 바뀌었고 그 밖의 연구소 결과 | 바뀐 사양이 이슈의 Expected 와 맞는지 |
   | B | 연결 SRS 가 바뀌지 않았고 연구소 결과가 `SPEC`·`NOT_BUG` | 지금 사양이 연구소 판정을 뒷받침하는지. SRS 마지막 수정이 이슈 마지막 수정보다 앞서면 "판정 뒤 SRS 가 바뀌지 않음" 신호를 붙인다(QA 규칙 §11) |
   | D | 기간 안에 생성된 이슈(위에 해당하지 않음) | 관련 사양 후보와 맞는지 |
   | 대상 아님 | 나머지 | AI 에 보내지 않고 건수만 남긴다 |

5. 이슈마다 점검 이벤트(`ISSUE_AUDIT`) 하나를 저장한다. 바뀐 뒤 값에 묶음, 연결 SRS, 기간 안 SRS 변화(더한 문장·뺀 문장)를 적는다.

   - 같은 이슈·같은 상태·같은 SRS 변화의 이벤트는 다시 넣지 않는다. 그래서 같은 기간을 다시 눌러도 바뀐 조합만 분석한다.
   - 사용량 한도에 걸린 이벤트는 대기로 남고, 다음 실행(예약·[지금 실행]·한도 초기화 뒤 다시 실행)이 이어서 분석한다. 실행마다 남은 점검 이벤트 수를 단계 비고에 보인다.

6. AI 분석(`qa-issue-spec-audit` Skill)은 토큰을 아끼려고 다음처럼 묶는다.

   - SRS 하나와 그 SRS 에 연결된 이슈 요약 카드를 한 작업에 넣는다. SRS 내용은 작업마다 한 번만 넣는다. 한 작업의 이슈는 `daily_qa.intelligence.audit_batch_size`(기본 10)건까지다.
   - SRS 는 바뀐 문장과 이슈 내용에 가까운 문단 3개만 넣는다. 바뀌지 않은 SRS 도 가까운 문단만 넣는다.
   - TC 후보는 넣지 않는다(사용자 결정 2026-10-02). 모델이 TC 영향을 쓰면 코드가 빼고 기록을 남긴다.
   - QA 규칙은 Skill 이 정한 절(§6·7·10·11·38·43)만 읽는다.
   - 점검 작업은 `daily_qa.intelligence.audit_model` 모델로 부른다. 비우면 `ai.claude.models.light` 다.
   - 결과가 `충돌`·`부분 일치` 인 이슈는 상세 화면에서 단일 이슈 분석(REQ-QAAGENT-001)으로 깊게 볼 수 있다.

7. 판정은 이슈마다 하나다.

   | 판정 값 | 화면 이름 |
   |---|---|
   | `CONSISTENT_WITH_SPEC` | 일치 |
   | `PARTIALLY_CONSISTENT` | 부분 일치 |
   | `CONTRADICTS_SPEC` | 충돌 |
   | `INSUFFICIENT_EVIDENCE` | 근거 부족 |

   A-수정 이슈의 TC 영향은 이 점검에서 정하지 않는다. 필요하면 사람이 관련 사양 변경 분석에서 [TC 점검]을 누른다.

**결과**

- 실행 상세와 요약 메일 맨 위에 알림 한 줄이 보인다. "이 기간에는 이슈 변경 기록이 없어 이슈 변화를 현재 상태 기준 점검으로 대신했습니다. 기간 중 상태 변화와 중간 댓글은 알 수 없습니다."
- 그 아래에 이슈 전체 수, 기간 안 생성 수, 기간 안 수정 수, 묶음별 수, 대상 아님 수가 보인다.
- 판정 카드마다 `현재 상태 기준(기간 이력 없음)` 표시가 붙는다.

**안 될 때**

| 경우 | 사용자에게 보이는 것 |
|---|---|
| 기간에 SRS 스냅샷이 없음 | REQ-QAINTEL-027 과 같은 400 문구 |
| 종료일이 오늘인데 Polarion 설정이 없음 | "Polarion 설정(POLARION_HOST / POLARION_TOKEN / 프로젝트)이 없습니다."(409) |
| 종료일이 지난 날인데 그날 이전 이슈 스냅샷이 없음 | "<종료일> 이전 이슈 스냅샷이 없어 현재 상태 점검을 할 수 없습니다. 종료일을 오늘로 고르세요."(400) |
| 다른 실행 중 | "QA Agent가 이미 실행 중입니다."(409) |

**지킬 것**

- verified·closed 이슈가 판정 기준이다. `in_review`·`in_progress`·`open`·`reopened` 이슈는 카드에 "참고만" 표시를 붙이고 Expected 근거로 쓰지 않는다(지침 §6).
- 연구소 댓글이나 조치 내용만으로 `일치` 를 내지 않는다(QA 규칙 §7). 실제로 전체를 보지 않았으면 "전수조사 완료"라고 쓰지 않는다(QA 규칙 §10).
- 실행당 작업 상한(`daily_qa.max_tasks_per_run`)은 이 점검에도 적용된다. 넘은 묶음은 대기로 남아 다음 실행이 이어 간다. 점검 전체 건수에는 따로 상한을 두지 않는다.

### REQ-QAINTEL-031 사양 변경 분석 (자동)

**하는 일** 새로 생기거나 바뀐 SRS 마다 무엇이 바뀌었는지, QA 가 확인할 것이 있는지, 관련 과거 이슈가 있는지 짧게 정리한다. TC·Checklist·매뉴얼은 보지 않는다. Skill `qa-spec-change-summary`.

> **예시** VP-767 에 "Reload 뒤에도 목록 상태를 유지한다." 가 더해졌다. 카드에는 `검토 필요` 와 "Reload 뒤 목록 유지 조건이 생겼다." 가, 할 일에는 "Reload 뒤 목록 유지 확인" 이 보인다.

**언제** REQ-QAINTEL-010 표에서 `SRS_CREATED`·`SRS_UPDATED` 이벤트가 있을 때. 예약·[지금 실행]·기간 실행이 모두 같다.

**입력** SRS 번호·본문, 바뀐 필드, 바뀌기 전·후 값, 더한 문장·뺀 문장, 관련 과거 이슈 후보(REQ-QAINTEL-016 입력의 이슈 후보와 같은 순서), 연관 사양·사양서 조각.

**순서**

1. 바뀐 것(`sections.change`): 한두 문장 요약과 바뀐 요구사항 문장 5개까지.
2. 관련 과거 이슈(`sections.related_issues`): 후보 가운데 관계있는 것만 5개까지. 관계는 `EXISTING_DEFECT`·`PAST_FIXED`·`PAST_SPEC`·`SAME_FUNCTION_REGRESSION` 이다.
3. 판정은 아래 넷 가운데 하나다.

   | 판정 | 뜻 | 결론 단계 |
   |---|---|---|
   | `NO_QA_IMPACT` | 서식·오탈자·표현만 바뀌었다 | 문제 없음 |
   | `QA_CHECK_NEEDED` | 동작·조건·값이 바뀌어 QA 가 확인할 것이 있다 | 검토 필요 |
   | `CONFLICTS_WITH_PAST_DECISION` | 과거 Spec 판정이나 수정 결과가 새 사양과 다르다 | 사양과 다름 |
   | `SPEC_UNCLEAR` | 바뀐 문장이 여러 뜻으로 읽혀 Expected 를 정할 수 없다 | 검토 필요 |

4. 요약은 한두 문장, QA 할 일은 3줄까지 쓴다.

**결과** SRS 변경마다 Finding 하나(분석 종류 `SPEC_COVERAGE`, Skill `qa-spec-change-summary`). 화면에서 [TC 점검]을 누를 수 있다(REQ-QAINTEL-016).

**지킬 것**

- 작업 입력에 TC 후보·매뉴얼 후보를 넣지 않고, 작업 폴더 `context/` 에 TC 색인·매뉴얼을 두지 않는다.
- 관련 과거 이슈는 오늘 이슈 스냅샷에 있는 번호만 남긴다. TC 비교 구획이나 초안이 오면 빼고 메모를 남긴다(REQ-QAINTEL-017).
- 바뀐 것의 요약(`sections.change.summary`)이 비었거나 문장이 아니면 결과를 버린다.

### REQ-QAINTEL-032 검증 TC 초안 (요청할 때만)

**하는 일** 수정 완료 이슈 하나에 대해 기존 TC 가 원인을 다시 잡는지 보고, 잡지 못하는 것만 검증 TC 초안으로 만든다. Skill `qa-verification-tc-draft`.

**언제** 사람이 수정 완료 이슈 분석(REQ-QAINTEL-013) 상세 화면에서 [검증 TC 초안 만들기]를 누를 때(`POST /qa-agent/findings/<번호>/tc-draft`). CLI 는 `scripts/run_daily_qa.py --on-demand tc-draft --finding <번호>` 다.

**순서**

1. REQ-QAINTEL-016 의 요청 실행 순서 1~4 와 같다. 입력은 수정 완료 이슈 분석의 입력에 TC 후보를 더한 것이다.
2. 기존 TC 마다 원인을 덮는지와 이유(`sections.tc_coverage`)를 적는다.
3. 검증 TC 초안은 기존 형식(`DraftTc`: `kind`, `srs_no`, `change`, `change_detail`, `title`, `precondition`, `test_step`, `expected_result`, `test_data`)을 쓴다. `kind` 는 `수정확인` 또는 `Regression` 이다. 관점(`perspective`)은 `DIRECT_FIX`·`REGRESSION`·`STATE_TRANSITION`·`BOUNDARY`·`NEGATIVE`·`INTEGRATION` 가운데 하나다.

**결과** Finding 하나(분석 종류 `TC_DRAFT`, `sections.source_finding` = 원래 분석 번호, 초안 목록). 초안 Excel(REQ-QAINTEL-018)이 생긴다.

**안 될 때**

| 경우 | 응답 |
|---|---|
| 원래 분석이 수정 완료 이슈 분석이 아님 | 400 "수정 완료 이슈 분석에서만 검증 TC 초안을 만들 수 있습니다." |
| 다른 실행 중 | 409 "QA Agent가 이미 실행 중입니다." |
| 사용량 한도 중 | AI 를 부르지 않고 단계가 `limit` 으로 끝난다. 원래 분석은 그대로다 |

**지킬 것**

- 사양 판정이 `SPEC_UNDEFINED`·`INSUFFICIENT_EVIDENCE` 면 초안을 만들지 않는다. 대신 "Checklist TC 생성 보류" 와 확인할 점을 남긴다.
- 오늘 연구소 결과가 `FIXED` 가 아닌 이슈에 초안을 내면 코드가 Finding 을 버린다.
- 최신 SRS·사양서 근거가 없으면 초안을 빼고 "Checklist TC 생성 보류" 를 남긴다. 기존 TC만으로 Expected 를 확정하지 않는다.
- 과거 자동 분석 결과와 요청 결과는 분석 종류·원래 분석 번호로 구분한다. 같은 버튼에서 같은 대상·판정·초안이 열려 있으면 다시 저장하지 않는다(REQ-DAILY-018).

### REQ-QAINTEL-033 결론 단계와 짧은 카드

**하는 일** 분석마다 다른 판정 값을 다섯 가지 결론으로 줄여, 카드 한 장만 보고 다음 행동을 정하게 한다.

**순서**

1. 판정 값을 아래 표로 결론 단계에 맞춘다. 표에 없는 값은 `검토 필요` 다.

   | 결론 단계 | 판정 값 |
   |---|---|
   | 사양과 다름 | `SPEC_VIOLATION`, `CONTRADICTS_SPEC`, `CONFLICTS_WITH_PAST_DECISION` |
   | 검토 필요 | `PARTIALLY_CONSISTENT`, `PARTIALLY_SUPPORTED`, `SPEC_UNDEFINED`, `SPEC_AMBIGUOUS`, `SPEC_NOT_FOUND`, `QA_CHECK_NEEDED`, `SPEC_UNCLEAR`, `PARTIALLY_COVERED`, `NOT_COVERED`, `SPEC_REVIEW_REQUIRED`, `QA_ACTION_REQUIRED`, `SPEC_CLAIM`, `REQUIREMENT_INFORMATION`, 표에 없는 값 |
   | 근거 부족 | `INSUFFICIENT_EVIDENCE` |
   | 참고 | `ROOT_CAUSE_INFORMATION`, `RESOLUTION_INFORMATION`, `REPRODUCTION_INFORMATION`, `OTHER_SIGNIFICANT_INFORMATION` |
   | 문제 없음 | `CONSISTENT_WITH_SPEC`, `SUPPORTED_BY_SPEC`, `FULLY_COVERED`, `NO_QA_IMPACT`, `NOT_SIGNIFICANT` |

2. QA 할 일은 권고(`sections.recommendation.actions`·`checks`) → 확인할 점(`sections.qa_analysis.checks`) → 조치 문장(`action`, 줄마다 하나) 순서로 찾아 3줄까지 보인다.
3. 목록은 결론 단계 순서(사양과 다름 → 검토 필요 → 근거 부족 → 참고 → 문제 없음)로, 같은 단계는 최신이 먼저다. `문제 없음` 은 접어 둔다.

**지킬 것**

- 카드에 칩 줄(기존 Issue 수, 조치 수, 경고 이름 등)을 두지 않는다. 그 정보는 상세 화면의 `자세히` 에 있다.
- 조치 문장(`action`)은 Finding 표에 칸이 없어 `sections.action` 에 함께 저장한다. 저장하지 않으면 카드의 할 일이 비어 보인다.

### REQ-QAINTEL-034 매뉴얼 점검 (요청할 때만)

**하는 일** 최근 7일 사양 변경이 매뉴얼에 반영됐는지 본다(REQ-DAILY-006 의 점검과 같은 Skill `qa-manual-completeness`).

**언제** 사람이 `scripts/run_daily_qa.py --on-demand manual-check` 를 실행할 때만 돈다. 화면 버튼은 두지 않는다(사용자 결정 2026-10-02). 예약·[지금 실행]·주간 요일·매뉴얼이 바뀐 날에도 자동으로 돌지 않는다.

이유: 매뉴얼은 사람이 넣는 자료라 최신이 아닐 수 있다.

**결과** 단계 `F` 의 결과와 Finding. 자동 실행의 단계 `F` 는 늘 `not_due` 이고 비고는 "매뉴얼 점검은 자동으로 돌지 않습니다." 로 시작한다. 예전의 미룬 점검 상태(`manual_check_due`)는 읽지도 쓰지도 않는다.

### NFR-QAINTEL-001 AI 는 변경이 있을 때만 부른다

- 수집과 스냅샷 비교는 매 실행 한다. AI 는 `analysis_required` 가 참인 이벤트가 있을 때만 부른다.
- 변경 없는 실행과 상태만 바뀐 실행의 Claude 호출 수는 0이다.
- 테스트는 가짜 실행기(`FakeRunner`)로 호출 수를 세어 확인한다. 테스트가 실제 Claude 를 부르지 않는다.

### NFR-QAINTEL-002 제품 공통 엔진

- 수집·이벤트 감지·분석·저장·대시보드는 제품마다 복사하지 않는다. 새 제품은 제품 설정, 제품 QA 규칙, 제품 지식, Checklist 형식, 제품 규칙 Skill 만 더해 붙인다.
- 제품 고유 필드 이름과 값은 제품 설정·제품 어댑터·제품 규칙 Skill(`config/products/<slug>/skills/`)에만 둔다.
- 절차는 `docs/PRODUCT_ONBOARDING.md` 에 있다. 공통 코드를 고쳐야 하는 경우도 그 문서에 적는다.

## 11. 테스트 사양

ID 규칙: `TEST-<CATEGORY>-NNN`. 모든 테스트는 가짜 Polarion·가짜 Claude 실행기를 쓴다. 실제 Claude 비용이 들지 않는다.

### TEST-QAINTEL-001 공휴일과 예약

#### 검증 대상
REQ-QAINTEL-001

#### 절차
1. 2026-09-25(추석), 2026-10-05(개천절 대체공휴일), 주말, 평일로 공휴일 판정을 확인한다.
2. 공휴일에 예약 함수를 부르고 프로세스가 뜨지 않는지 본다.
3. 제품 두 개로 예약을 등록해 작업 이름이 `qa_agent_<slug>` 인지 본다.

#### Expected Result
공휴일은 `holiday` 로 끝나고 프로세스를 띄우지 않는다. 평일은 띄운다.

### TEST-QAINTEL-002 제품 설정과 공통 모델

#### 검증 대상
REQ-QAINTEL-002, REQ-QAINTEL-023, NFR-QAINTEL-002

#### 절차
VXvue 필드(`rndReviewResult`, `occurrenceCause`)와 가상 제품 필드(`devResult`, `rootCause`)의 원본을 각각 공통 모델로 바꾸고, 같은 이벤트 감지·스냅샷·상태·이벤트 표를 두 제품으로 돌린다.

#### Expected Result
두 제품의 공통 모델이 같은 모양이고 연구소 결과가 같은 공통 값이 된다. 스냅샷 폴더·상태 키·이벤트·Finding 이 제품별로 나뉜다.

### TEST-QAINTEL-003 이슈 수집과 기준 스냅샷

#### 검증 대상
REQ-QAINTEL-004, REQ-QAINTEL-008

#### 절차
이슈 전체를 조회하는지, 첫 실행이 기준만 만들고 이벤트·AI 호출이 없는지, 두 번째 실행에서 신규 이슈가 `ISSUE_CREATED` 가 되는지 본다.

#### Expected Result
첫 실행 결과 `BASELINE`, AI 호출 0. 두 번째 실행에 신규 이슈 이벤트 1건.

### TEST-QAINTEL-004 이벤트 종류

#### 검증 대상
REQ-QAINTEL-005, REQ-QAINTEL-010

#### 절차
상태만 변경, 새 댓글, 연구소 결과 → `FIXED`, → `SPEC`, 발생 원인 변경, 조치 내용 변경, SRS 변경과 이슈 변경 동시를 각각 만든다.

#### Expected Result
표의 이벤트와 분석 종류가 나온다. 상태만 바뀐 경우 AI 호출이 없다.

### TEST-QAINTEL-005 변경 없음

#### 검증 대상
REQ-QAINTEL-007, NFR-QAINTEL-001

#### 절차
같은 자료로 두 번 돌린다.

#### Expected Result
두 번째 실행 결과 `NO_CHANGE`, Claude 호출 0.

### TEST-QAINTEL-006 수집 실패와 재시도

#### 검증 대상
REQ-QAINTEL-006, REQ-QAINTEL-009

#### 절차
이슈 조회 도중 실패, 0건, 급감, AI 실패를 만든다.

#### Expected Result
수집 실패면 스냅샷·이벤트가 저장되지 않는다. AI 실패면 이벤트가 `failed` 로 남고 다음 실행에서 다시 분석된다.

### TEST-QAINTEL-007 근거 검증

#### 검증 대상
REQ-QAINTEL-017

#### 절차
없는 이슈·SRS·TC 번호, 자기 자신 중복 후보, `FIXED` 가 아닌 이슈의 초안, 근거 없는 판정을 담은 결과를 검증한다.

#### Expected Result
없는 번호는 빠지고 검증 기록에 남는다. 규칙을 어긴 Finding 은 버려진다.

### TEST-QAINTEL-008 사양 변경 Coverage

#### 검증 대상
REQ-QAINTEL-016, REQ-QAINTEL-018

#### 절차
자동 실행으로 사양 변경 분석을 만든 뒤 [TC 점검] 요청 실행을 돌린다. 신규 SRS + TC 없음, 기존 TC 완전 커버, Expected 만 옛 사양, 과거 이슈만 있음, 사양 불명확, 같은 버튼 다시 누름을 만든다.

#### Expected Result
각각 `NOT_COVERED`+초안, `FULLY_COVERED`+초안 없음, `UPDATE_EXISTING`, 이슈 Coverage 있음+`NOT_COVERED`, 초안 없음, 두 번째 실행에 새 초안 없음. 초안 Excel 에 `Coverage` 시트가 있다.

### TEST-QAINTEL-009 대시보드와 지금 실행

#### 검증 대상
REQ-QAINTEL-019, REQ-QAINTEL-020, REQ-QAINTEL-021, REQ-QAINTEL-022, REQ-QAINTEL-024

#### 절차
대시보드·상세·실행 상세·기간 조회를 열고, 지금 실행을 잠금 없음·잠금 있음으로 부른다. 기간 조회에 잘못된 날짜를 넣는다.

#### Expected Result
화면에 요약·카드·지식 문서 업로드 날짜가 보이고 승인·거절 양식이 없다. 지금 실행은 202, 잠금이 있으면 409 "QA Agent가 이미 실행 중입니다.".

### TEST-QAINTEL-010 분석 종류별 입력과 결과

#### 검증 대상
REQ-QAINTEL-011, REQ-QAINTEL-012, REQ-QAINTEL-013, REQ-QAINTEL-014, REQ-QAINTEL-015, REQ-QAINTEL-003

#### 절차
신규·수정 완료·Spec 판정·새 댓글 분석의 작업 입력에 후보가 압축돼 들어가는지, 결과가 구획과 함께 저장되는지, SRS 본문 변경이 문장 단위로 좁혀지는지 본다.

#### Expected Result
후보 수가 설정 상한 이하이고 걸린 이유가 있다. Finding 에 분석 종류와 구획이 있다. 공백만 바뀐 SRS 는 변경이 아니다.

### TEST-QAINTEL-011 사용량 한도와 공휴일 예외

#### 검증 대상
REQ-QAINTEL-025

#### 절차
1. 세션·주간·옛 판(유닉스 초)·인증 실패 문장을 분류한다.
2. 파이프라인에서 첫 작업이 세션 한도로 끝나게 한다. 같은 날 한 번 더 돌린다.

#### Expected Result
한도 종류와 초기화 시각이 맞게 읽힌다. 첫 한도에서 남은 작업이 멈추고 이벤트는 `pending`·실패 횟수 0이다. catch-up 시각이 저장된다. 한도가 살아 있는 동안 다음 실행은 Claude 를 부르지 않는다.

### TEST-QAINTEL-012 기간 실행과 토큰 기록

#### 검증 대상
REQ-QAINTEL-026, REQ-QAINTEL-027

#### 절차
1. 사흘치 스냅샷을 만든 뒤 시작일을 첫날로 두고 기간 실행한다. 이미 분석한 변경이 있는 상태에서 다시 기간 실행한다. 지난 날 종료일로도 실행한다.
2. 가짜 실행기가 사용량을 돌려주게 하고 실행 요약·비용 대시보드 집계·하루 사용량을 본다.

#### Expected Result
기간의 변경이 이벤트가 되고, 이미 분석한 끝 상태는 다시 이벤트가 되지 않는다. 지난 날 종료일은 스냅샷을 저장하지 않는다. 토큰 합계가 실행 요약과 비용 대시보드 `qa_agent_run`, 하루 사용량에 들어간다.

### TEST-QAINTEL-013 수집·저장·한도 예외

#### 검증 대상
REQ-QAINTEL-004, REQ-QAINTEL-006, REQ-QAINTEL-009, REQ-QAINTEL-025

#### 절차
자동 테스트 `tests/test_qa_intel_failures.py` 가 가짜 Polarion·가짜 실행기로 다음을 하나씩 만든다.

1. 이슈 스냅샷 쓰기가 `OSError` 로 실패한다. 변경 이벤트 저장이 실패한다.
2. 이슈 댓글 읽기가 하루·이틀 실패한다. `comment_fetch_limit` 를 넘어 댓글 읽기를 미룬다. 댓글 번호 목록이 없는 이슈도 실패시킨다.
3. 같은 이벤트의 분석이 `event_max_attempts` 번 실패한다. 대기 이벤트의 대상 이슈가 오늘 조회에서 사라진다.
4. 인증 실패 뒤 토큰이 그대로인 실행과 바뀐 실행, 주간 한도 뒤 실행, 작업 상한을 넘는 이벤트, 옛 `spec_change_pending` 상태, 변경 없는 날의 매뉴얼 점검을 만든다.

#### Expected Result
1. 스냅샷을 쓰지 못하면 이벤트가 없고 기준이 앞으로 가지 않는다. 이벤트를 저장하지 못하면 오늘 스냅샷을 되돌린다.
2. 읽지 못한 댓글은 전날 값을 옮겨 적고, 다음 실행이 다시 읽어 새 댓글을 한 번만 이벤트로 만든다. 미룬 이슈도 다음 실행에 한 번 읽는다.
3. 상한에 닿은 이벤트와 대상이 사라진 이벤트는 `abandoned` 가 된다.
4. 인증 실패는 토큰이 바뀔 때까지 AI 를 막는다. 주간 한도는 catch-up 을 잡지 않고 초기화 뒤 이어서 분석한다. 상한을 넘은 이벤트는 `pending` 으로 남아 다음 실행이 처리한다. 옛 대기 상태는 대기 SRS 이벤트가 된다. 매뉴얼 점검은 다음 변경 실행으로 미뤄진다.

### TEST-QAINTEL-015 과거 SRS 가져오기와 본문 링크

**목적** 가져온 과거 스냅샷과 매일 실행이 같은 글자를 만드는지, 믿을 수 없는 날짜를 건너뛰는지 확인한다.

**절차** `tests/test_qa_intel_alm_history.py` 를 돌린다. 합성 `srs-spec` 폴더(정상 날짜, 되살릴 수 없는 깨진 JSON 날짜, 끝에 같은 글자가 남은 날짜, 개수가 모자란 날짜, 이미 있는 날짜)를 쓴다.

1. 본문 링크가 `번호 - 제목` 으로, 모르는 번호는 `(참조 대상 확인 불가)` 로 풀린다. 장식 아이콘은 지워진다.
2. 정상 날짜만 가져오고, 다시 돌려도 결과가 같고, 이미 있는 날짜는 덮어쓰지 않는다.
3. 끝에 남은 글자가 앞 JSON 의 끝과 같은 파일은 되살리고 `restored_files` 에 적는다. 끝 글자가 다른 파일은 되살리지 않는다.
4. 가져온 날짜를 기준으로 기간 실행이 SRS 변경을 찾는다. Polarion 설정 없이 지난 날 기간 실행을 띄울 수 있다.
5. 스냅샷이 없는 기간은 400 으로 거절한다.

**기대 결과** 모든 테스트 통과. 실제 `srs-spec` 12개 날짜로 돌린 결과는 `progress.md` 에 적는다.

### TEST-QAINTEL-017 현재 상태 기준 이슈 정합성 점검

**목적** 이슈 기록이 없는 기간에 이슈를 코드로 나누고, SRS 단위로 묶어 AI 를 부르며, 알림이 붙는지 확인한다.

**절차** `tests/test_qa_intel_issue_audit.py` 를 돌린다. 합성 이슈·SRS 스냅샷과 가짜 실행기(`FakeRunner`)를 쓴다.

1. 묶음 나누기: A-수정·A-사양·A-기타·B·D·대상 아님이 표대로 나뉜다. 비슷한 제목만으로는 연결하지 않는다. 판정 뒤 SRS 가 바뀌지 않은 B 에 신호가 붙는다.
2. 작업 묶기: 같은 SRS 의 이슈가 한 작업에 들어가고, SRS 내용은 작업에 한 번만 들어간다. 어느 작업에도 TC 후보가 없다.
3. 같은 기간을 다시 돌리면 새 점검 이벤트가 생기지 않고 Claude 호출이 0회다.
4. 점검 작업은 점검 모델로 부른다. 결과 판정이 허용 목록 밖이면 버리고, 모델이 쓴 TC 영향은 뺀다.
5. 실행 상세에 알림 문장과 묶음별 수가 보인다. `POST /qa-agent/issue-audit` 은 202, 실행 중이면 409 다.

**기대 결과** 모든 테스트 통과.

### TEST-QAINTEL-018 자동은 핵심만, TC·매뉴얼은 요청할 때만

**목적** 자동 실행이 TC·매뉴얼을 쓰지 않고, 요청 실행만 TC 와 비교하며, 화면이 결론 단계와 짧은 카드로 보이는지 확인한다.

**절차** `tests/test_qa_intel_core_mode.py` 와 `tests/test_qa_intel_dashboard.py` 의 결론 단계·버튼 테스트를 돌린다. 가짜 Polarion·가짜 실행기를 쓴다.

1. 자동 실행의 모든 작업 입력에 TC·매뉴얼 후보가 없고, `context/` 에 `tc_index.jsonl`·`manuals/` 가 없다.
2. 사양 변경은 `qa-spec-change-summary` 로 분석하고, 없는 이슈 번호는 빠지며, 할 일이 카드까지 이어진다.
3. 자동 수정 완료 이슈 분석의 초안은 빠지고 메모가 남는다. 주간 요일·미룬 점검 상태가 있어도 매뉴얼 점검은 돌지 않는다.
4. [TC 점검]은 TC 후보와 함께 Coverage Skill 을 한 번 부르고 `TC_CHECK` 로 저장하며 이벤트 상태·메일을 바꾸지 않는다. [검증 TC 초안]은 `TC_DRAFT` 로 초안을 저장한다. 맞지 않는 분석·한도 중이면 Claude 를 부르지 않는다.
5. 결론 단계 표, 할 일 3줄, 칩 없는 카드, 결론 순 정렬, 개편 전 Coverage Finding 의 새 상세, 분석별 버튼, 지식 문서 안내 문장, 매뉴얼 점검 버튼 없음.

**기대 결과** 모든 테스트 통과.

### TEST-QAINTEL-016 Claude CLI 로그인 사용

**목적** 토큰이 없는 PC 에서 Claude CLI 로그인으로 AI 분석을 부르는지 확인한다.

**절차** `tests/test_qa_intel_login.py` 를 돌린다. 실제 Claude 는 부르지 않는다(로그인 확인과 실행기를 가짜로 바꾼다).

1. `claude auth status` 출력 앞에 다른 줄이 있어도 JSON 을 읽고, 이메일·조직 번호는 돌려주지 않는다.
2. 로그인돼 있으면 토큰 없이 CLI 를 부르고 사전 점검 비고에 로그인 방식이 남는다.
3. 로그인도 없으면 AI 단계를 건너뛴다. 로그인이 확인되면 남아 있던 인증 실패 기록을 푼다.

**기대 결과** 모든 테스트 통과.

### TEST-QAINTEL-014 재시도·중복 제거·기간 실행 경계

**목적** 코드 리뷰(2026-10-01)에서 찾은 결함과 사용자 결정 9가지가 고쳐진 채로 남는지 확인한다.

**절차** 가짜 Polarion·가짜 Claude 로 `tests/test_qa_intel_review_fixes.py` 를 돌린다.

1. 지난 날 기간 실행 뒤에도 최신 이슈의 실패 이벤트가 그대로이고 다시 돌지 않는다.
2. 한 이벤트의 두 분석 가운데 실패한 것만 다시 돈다. 한 실행에서 둘 다 실패해도 횟수는 1이다.
3. 이틀에 걸친 변경을 기간 실행이 다시 만들지 않는다. 매일 실행이 보지 못한 값은 찾는다.
4. 수정됨 → 다시 열림 → 다시 수정됨은 새 이벤트이고, 같은 날 다시 돌리면 겹치지 않는다.
5. Polarion 설정 없이 돈 실행은 `PARTIAL` 이다. 종료일만 준 기간은 종료일 전날부터다.
6. 지난 날 기간의 사양–TC 연결 점검이 종료일 뒤 스냅샷을 쓰지 않는다. 제품 잡음 규칙이 기본 규칙에 더해진다.
7. 댓글 읽기 실패 때 수정 완료 분석이 스냅샷 댓글을 받는다. 매뉴얼 점검이 남은 작업 상한을 나눠 쓴다.
8. 작업 결과 속 401 은 인증 실패가 아니다. 다른 실행 중이면 catch-up 기록을 남긴다. 답변은 제품별로 넣고 옛 Skill 이름 답변도 넣는다. 멈춘 실행도 쓴 토큰을 남긴다.

**기대 결과** 모든 테스트 통과.

## 12. 요구사항 추적성

이 표는 이 문서가 정의한 ID만 담는다. 상위 `SPEC.md`의 비기능 요구사항은 그 문서의 12절이 추적한다.

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-QAINTEL-001 | `app/modules/daily_qa/scheduled_jobs.py`, `app/modules/daily_qa/holidays.py`, `config/holidays/kr.yaml` | TEST-QAINTEL-001: `tests/test_qa_intel_schedule.py` | verified |
| REQ-QAINTEL-002 | `app/core/product_config.py`, `app/modules/daily_qa/product_adapter.py`, `config/products/vxvue.yaml` | TEST-QAINTEL-002: `tests/test_qa_intel_products.py` | verified |
| REQ-QAINTEL-003 | `app/modules/daily_qa/srs_snapshot.py`, `app/modules/daily_qa/snapshots.py`, `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/product_adapter.py`, `app/modules/daily_qa/collector.py` | TEST-QAINTEL-010: `tests/test_qa_intel_events.py`, TEST-QAINTEL-015: `tests/test_qa_intel_alm_history.py` | verified |
| REQ-QAINTEL-004 | `app/modules/daily_qa/collector.py`, `app/modules/daily_qa/snapshots.py` | TEST-QAINTEL-003: `tests/test_qa_intel_pipeline.py`, TEST-QAINTEL-013: `tests/test_qa_intel_failures.py` | verified |
| REQ-QAINTEL-005 | `app/modules/daily_qa/change_events.py` | TEST-QAINTEL-004: `tests/test_qa_intel_events.py`, TEST-QAINTEL-014: `tests/test_qa_intel_review_fixes.py` | verified |
| REQ-QAINTEL-006 | `app/modules/daily_qa/pipeline.py`, `app/core/daily_qa_storage.py`, `app/modules/daily_qa/change_events.py` | TEST-QAINTEL-006: `tests/test_qa_intel_pipeline.py`, TEST-QAINTEL-013: `tests/test_qa_intel_failures.py`, TEST-QAINTEL-014: `tests/test_qa_intel_review_fixes.py` | verified |
| REQ-QAINTEL-007 | `app/modules/daily_qa/pipeline.py` | TEST-QAINTEL-005: `tests/test_qa_intel_pipeline.py`, TEST-QAINTEL-014: `tests/test_qa_intel_review_fixes.py` | verified |
| REQ-QAINTEL-008 | `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/collector.py` | TEST-QAINTEL-003: `tests/test_qa_intel_pipeline.py` | verified |
| REQ-QAINTEL-009 | `app/modules/daily_qa/collector.py`, `app/modules/daily_qa/pipeline.py` | TEST-QAINTEL-006: `tests/test_qa_intel_pipeline.py`, TEST-QAINTEL-013: `tests/test_qa_intel_failures.py` | verified |
| REQ-QAINTEL-010 | `app/modules/daily_qa/change_events.py` | TEST-QAINTEL-004: `tests/test_qa_intel_events.py` | verified |
| REQ-QAINTEL-011 | `app/modules/daily_qa/intelligence.py`, `app/retrieval/hybrid.py` | TEST-QAINTEL-010: `tests/test_qa_intel_analysis.py` | verified |
| REQ-QAINTEL-012 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/skills/qa-new-issue-analysis/SKILL.md` | TEST-QAINTEL-010: `tests/test_qa_intel_analysis.py` | verified |
| REQ-QAINTEL-013 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/evidence_validation.py`, `app/modules/daily_qa/skills/qa-fixed-issue-analysis/SKILL.md` | TEST-QAINTEL-010: `tests/test_qa_intel_analysis.py`, TEST-QAINTEL-018: `tests/test_qa_intel_core_mode.py` | verified |
| REQ-QAINTEL-014 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/skills/qa-spec-decision-analysis/SKILL.md` | TEST-QAINTEL-010: `tests/test_qa_intel_analysis.py` | verified |
| REQ-QAINTEL-015 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/skills/qa-comment-analysis/SKILL.md` | TEST-QAINTEL-010: `tests/test_qa_intel_analysis.py` | verified |
| REQ-QAINTEL-016 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/pipeline.py`, `app/modules/qa_agent/router.py`, `app/modules/daily_qa/skills/qa-spec-coverage-analysis/SKILL.md` | TEST-QAINTEL-008: `tests/test_qa_intel_coverage.py`, TEST-QAINTEL-018: `tests/test_qa_intel_core_mode.py` | verified |
| REQ-QAINTEL-017 | `app/modules/daily_qa/evidence_validation.py`, `app/modules/daily_qa/schema.py` | TEST-QAINTEL-007: `tests/test_qa_intel_validation.py` | verified |
| REQ-QAINTEL-018 | `app/modules/daily_qa/checklist_xlsx.py`, `app/modules/daily_qa/pipeline.py` | TEST-QAINTEL-008: `tests/test_qa_intel_coverage.py` | verified |
| REQ-QAINTEL-019 | `app/modules/qa_agent/dashboard.py`, `app/modules/qa_agent/router.py`, `app/modules/qa_agent/templates/dashboard.html` | TEST-QAINTEL-009: `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-020 | `app/modules/qa_agent/router.py`, `app/modules/qa_agent/run_view.py`, `app/modules/qa_agent/templates/finding_detail.html`, `app/modules/qa_agent/templates/run_detail.html` | TEST-QAINTEL-009: `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-021 | `app/modules/qa_agent/router.py`, `app/modules/daily_qa/scheduled_jobs.py` | TEST-QAINTEL-009: `tests/test_qa_intel_dashboard.py`, TEST-QAINTEL-015: `tests/test_qa_intel_alm_history.py` | verified |
| REQ-QAINTEL-022 | `app/modules/qa_agent/dashboard.py`, `app/modules/qa_agent/templates/dashboard.html` | TEST-QAINTEL-009: `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-024 | `app/modules/qa_agent/dashboard.py`, `app/modules/qa_agent/router.py`, `app/modules/qa_agent/templates/period.html`, `app/core/daily_qa_storage.py` | TEST-QAINTEL-009: `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-023 | `app/modules/daily_qa/settings.py`, `app/core/daily_qa_storage.py`, `app/modules/daily_qa/snapshots.py` | TEST-QAINTEL-002: `tests/test_qa_intel_products.py` | verified |
| REQ-QAINTEL-025 | `app/modules/daily_qa/claude_limits.py`, `app/modules/daily_qa/agent_runner.py`, `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/scheduled_jobs.py`, `app/core/claude_cli.py` | TEST-QAINTEL-011: `tests/test_qa_intel_schedule.py`, `tests/test_qa_intel_pipeline.py`, TEST-QAINTEL-013: `tests/test_qa_intel_failures.py`, TEST-QAINTEL-014: `tests/test_qa_intel_review_fixes.py`, TEST-QAINTEL-016: `tests/test_qa_intel_login.py` | verified |
| REQ-QAINTEL-026 | `app/modules/daily_qa/pipeline.py`, `app/core/storage.py`, `app/modules/cost_dashboard/router.py` | TEST-QAINTEL-012: `tests/test_qa_intel_period.py` | verified |
| REQ-QAINTEL-027 | `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/snapshots.py`, `scripts/run_daily_qa.py`, `app/modules/qa_agent/router.py`, `app/modules/qa_agent/templates/dashboard.html`, `app/modules/qa_agent/dashboard.py` | TEST-QAINTEL-012: `tests/test_qa_intel_period.py`, TEST-QAINTEL-014: `tests/test_qa_intel_review_fixes.py`, TEST-QAINTEL-015: `tests/test_qa_intel_alm_history.py` | verified |
| REQ-QAINTEL-029 | `app/modules/daily_qa/alm_history.py`, `scripts/import_alm_srs_history.py`, `app/modules/daily_qa/product_adapter.py` | TEST-QAINTEL-015: `tests/test_qa_intel_alm_history.py` | verified |
| REQ-QAINTEL-030 | `app/modules/daily_qa/issue_audit.py`, `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/evidence_validation.py`, `app/modules/qa_agent/run_view.py`, `app/modules/daily_qa/skills/qa-issue-spec-audit/SKILL.md`, `app/modules/qa_agent/router.py` | TEST-QAINTEL-017: `tests/test_qa_intel_issue_audit.py`, `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-028 | `app/modules/daily_qa/packages.py`, `app/modules/daily_qa/pipeline.py` | `tests/test_daily_qa_packages.py`, `tests/test_daily_qa_fixes.py` | verified |
| REQ-QAINTEL-031 | `app/modules/daily_qa/intelligence.py`, `app/modules/daily_qa/evidence_validation.py`, `app/modules/daily_qa/workspace.py`, `app/modules/daily_qa/skills/qa-spec-change-summary/SKILL.md` | TEST-QAINTEL-018: `tests/test_qa_intel_core_mode.py`, `tests/test_qa_intel_validation.py` | verified |
| REQ-QAINTEL-032 | `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/scheduled_jobs.py`, `app/modules/qa_agent/router.py`, `scripts/run_daily_qa.py`, `app/modules/daily_qa/skills/qa-verification-tc-draft/SKILL.md` | TEST-QAINTEL-018: `tests/test_qa_intel_core_mode.py`, `tests/test_qa_intel_pipeline.py`, `tests/test_qa_intel_dashboard.py` | verified |
| REQ-QAINTEL-033 | `app/modules/qa_agent/dashboard.py`, `app/modules/qa_agent/templates/dashboard.html`, `app/modules/qa_agent/templates/finding_detail.html`, `app/core/daily_qa_storage.py` | TEST-QAINTEL-018: `tests/test_qa_intel_dashboard.py`, `tests/test_qa_intel_core_mode.py` | verified |
| REQ-QAINTEL-034 | `app/modules/daily_qa/pipeline.py`, `scripts/run_daily_qa.py` | TEST-QAINTEL-018: `tests/test_qa_intel_core_mode.py`, `tests/test_qa_intel_failures.py`, `tests/test_daily_qa_pipeline.py` | verified |
| NFR-QAINTEL-001 | `app/modules/daily_qa/pipeline.py` | TEST-QAINTEL-005: `tests/test_qa_intel_pipeline.py` | verified |
| NFR-QAINTEL-002 | `app/modules/daily_qa/product_adapter.py`, `docs/PRODUCT_ONBOARDING.md` | TEST-QAINTEL-002: `tests/test_qa_intel_products.py` | verified |

Status 값: `draft` (사양만 있음) / `implemented` / `verified` (게이트가 실행하는 자동 테스트로 확인) /
`deprecated`. 수동 절차만 있으면 `implemented`로 둔다.

## 13. 미확정 사항

- 확인 필요: Polarion 이슈 응답에 댓글 관계(`relationships.comments`)의 댓글 번호가 실제로 오는지. 오지 않으면 수정 시각이 바뀐 이슈만 댓글을 읽는다(REQ-QAINTEL-004). 서버 첫 실행의 이슈 수집 단계 비고로 확인한다.
- 확인 필요: 이슈 전체 조회(`type:issue`)의 건수와 걸리는 시간. 수천 건이면 조회식을 최근 N일 수정분으로 좁히는 선택지를 검토한다.
- 확인 필요: 공휴일 표(`config/holidays/kr.yaml`)는 2025~2027년만 있다. 해마다 음력 공휴일·대체공휴일·선거일을 더해야 한다.
- (TBD) 근로자의 날(5월 1일)과 회사 휴무일은 표에 넣지 않았다. 필요하면 `daily_qa.schedule.extra_holidays` 에 적는다.
