# QA 검증 관리 시스템 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 0.3.0 |
| Last Updated | 2026-09-28 |
| Status | draft — 기존 계약 일부의 근거 기반 사양화 |

기존 코드와 테스트의 명시적 약속을 처음으로 연결한 제한된 기준선이다. 전체 제품 사양이나 운영 검증 완료를 뜻하지 않는다. 기존 약속과 구현의 차이는 SPEC / CODE MISMATCH로 남기고 코드에 맞춰 약속을 바꾸지 않는다.

## 1. 목적

변경사항과 사양·Test Case를 대조하여 검증 대상과 근거를 제공한다.

## 2. 프로젝트 범위

포함: 추천 평가 지표 및 누락 판정, 핵심 앱의 listen 주소·포트 결정 방식, VXvue 일일 QA 자동 점검(사양 변경 영향, 이슈 수정확인 초안, 사양–TC 추적 공백, 매뉴얼 누락 후보)과 그 결과의 메일 요약·검토 대기열, QA Agent 가 읽는 Polarion 이슈 Export 폴더의 구조.

제외: 운영 데이터·비밀 설정의 열람/변경, 인증 방식 변경, listen 포트 외의 배포 절차 변경. 기존 기능은 REQ-ISSUE-001(이슈 Export 폴더 구조 대응) 외에는 동작을 바꾸지 않는다. 이 제외는 작업 경계이며 기존 제품 기능을 제거한다는 뜻이 아니다.

| 용어 | 뜻 |
|---|---|
| 일일 점검 | 서버가 평일 아침마다 스스로 돌리는 VXvue QA 점검 한 번(`daily_qa` 실행) |
| Skill | Claude Code가 읽는 작업 설명서 폴더(`SKILL.md`). 이 저장소의 `app/modules/daily_qa/skills/` 에 있다 |
| 작업 폴더 | Claude가 읽고 쓰는 격리된 폴더(`daily_qa.workspace_dir`). 저장소 밖에 둔다 |
| Finding | AI가 낸 판정 한 건. 근거 위치와 사람 검토 상태를 함께 저장한다 |
| QA 규칙 | `[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드_Rev*.md` 와 지침 프롬프트 |

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| CORE | 추천 평가 지표 |
| DEPLOY | 배포 설정 |
| DAILY | VXvue 일일 QA 자동 점검 |
| ISSUE | Polarion 이슈 Export 읽기 |
| SEC | AI 실행 보안(비기능) |

### REQ-CORE-001 평가 지표

정답 TC 집합과 추천 TC 집합의 교집합을 TP, 추천에만 있는 항목을 FP, 정답에만 있는 항목을 FN으로 센다. 정답과 추천이 각 2개이며 공통 1개이면 precision/recall/F1은 각각 0.5다.

관련 구현: `app/core/evaluation.py`. 관련 테스트: `tests/test_evaluation.py`의 `test_recommendation_metrics_counts_precision_and_recall`.

### REQ-CORE-002 누락과 비추천 구분

recommended가 참인 결정만 추천 목록에 포함한다. 정답에 있지만 추천되지 않은 ID는 missing_tc_ids로 보고하고 recommended=false인 항목은 예상외 추천으로 세지 않는다.

관련 구현: `app/core/evaluation.py`. 관련 테스트: `tests/test_evaluation.py`의 `test_evaluate_analysis_lists_missing_and_unexpected_ids`.

### REQ-CORE-003 집계 방식

여러 평가의 TP/FP/FN을 먼저 합친 뒤 micro 평균 precision/recall/F1을 산출한다. 개별 case 비율의 단순 평균으로 바꾸지 않는다.

관련 구현: `app/core/evaluation.py`. 관련 테스트: `tests/test_evaluation.py`의 `test_aggregate_evaluations_uses_micro_average`.

### REQ-DEPLOY-001 listen 주소·포트의 단일 원본

핵심 앱이 listen 하는 주소와 포트는 `config.yaml` 의 `app.host` / `app.port` 하나로 결정한다.
진입점(`scripts/run.sh`, `scripts/run.ps1`, `deploy/systemd/qa-verification.service`)은 포트를
직접 지정하지 않고 `python -m app.serve` 로 앱을 띄우며, `app.serve` 가 그 값을 읽어 uvicorn 에
넘긴다. 앱이 자기 자신을 HTTP 로 호출하는 주소와 운영 점검(`scripts/monitor_health.py`)의 기본
대상도 같은 값에서 파생한다. 키가 없거나 비었을 때만 기본값(`0.0.0.0` / `24357`)으로 떨어지고,
포트가 정수로 읽히지 않으면 기본값으로 대체하지 않고 실패시킨다.

nginx 는 YAML 을 읽지 못하므로 `deploy/nginx/qa-platform.conf` 의 upstream 포트만 이 원본을
자동으로 따라가지 못한다. 두 값이 다르면 검사에서 실패해야 한다.

운영 포트는 `24357` 이다. 1024 초과이고, 서버의 ephemeral 포트 범위(실측 `32768~60999`) 밖이며,
해당 호스트에서 다른 서비스가 점유하지 않는다.

관련 구현: `app/core/config.py` 의 `app_bind` / `app_self_url`, `app/serve.py`.
관련 테스트: `tests/test_serve_bind.py`, `tests/test_monitor_health.py` 의
`test_default_base_url_comes_from_config_yaml`.

### REQ-DAILY-001 일일 점검 실행 순서

앱 내장 스케줄러가 평일 07:30(한국 시간, `daily_qa.schedule`)에 `scripts/run_daily_qa.py` 를 웹 프로세스와 분리된 별도 프로세스로 띄운다. Polarion·Claude 자격증명이 없는 호스트에서는 띄우지 않는다. 점검은 아래 순서로 한 번 돈다.

1. 같은 점검이 이미 돌고 있으면 새 실행은 바로 끝난다(잠금 파일).
2. 사전 점검: QA 규칙 판(Rev), 필요한 자격증명, 작업 폴더 위치를 확인한다.
3. Polarion에서 SRS와 이슈를 읽어 온다(REQ-DAILY-002).
4. 사양 변경 영향(B), 이슈 수정확인 초안(C)을 매일 만든다. 추적 공백(E)과 매뉴얼 누락 후보(F)는 설정한 요일에만 만든다.
5. 각 결과를 검증하고(REQ-DAILY-007) 저장한다.
6. 요약 메일을 보낸다(REQ-DAILY-008).

실행마다 실행 기록(`daily_qa_runs`) 한 줄과 실행 폴더(`output/daily_qa/<실행ID>/`)가 생긴다. 한 단계가 실패해도 다른 단계는 계속 돌고, 실패한 단계와 이유가 실행 기록과 메일에 남는다.

> **예시** Polarion 조회가 실패하면 B·C는 `실패`로 남고, 지식 폴더만 쓰는 E는 그대로 돈다.

`--dry-run` 은 Claude를 부르지 않고 입력 묶음과 결정적 계산만 만든다.

### REQ-DAILY-002 Polarion 읽기 전용 수집

일일 점검은 Polarion에서 읽기만 한다. 수집 코드는 GET 요청만 만들 수 있고, 다른 방식의 요청을 만드는 함수가 없다.

- SRS: 설정한 조회식(`daily_qa.polarion.srs_query`)으로 전체를 읽어 그날의 스냅샷(`data/daily_qa/snapshots/<날짜>.json`)으로 저장한다. 직전 스냅샷과 비교해 신규·삭제·변경 SRS를 만든다. 직전 스냅샷이 없으면 비교 없이 기준만 저장한다.
- 이슈: 설정한 조회식(`daily_qa.polarion.issue_query`)으로 읽고, 마지막 성공 실행 이후 바뀐 이슈만 새 이슈로 본다.

토큰(`POLARION_TOKEN`)과 주소(`POLARION_HOST`)는 비밀 설정에서만 읽는다. 둘 중 하나가 없으면 수집 단계는 `건너뜀`으로 남는다.

### REQ-DAILY-003 사양 변경 → TC 영향 (B)

바뀐 SRS마다 영향을 받을 수 있는 기존 TC를 먼저 코드로 고른다. TC의 SRS 번호 열(`Old SRS ID`, `SRS No`)이 SRS의 Legacy 번호(`oldId`)나 SRS ID와 같으면 후보다. 그다음 Skill `vxvue-spec-change-impact` 가 후보 TC마다 아래 중 하나로 판정한다.

| 판정 | 뜻 |
|---|---|
| 유지 | 바뀐 사양에서도 TC 그대로 유효 |
| 경미 수정 | 문구만 고치면 된다 |
| 수정 필수 | Step 또는 Expected가 바뀐 사양과 다르다 |
| 신규 TC 필요 | 기존 TC로 검출할 수 없는 변경이다 |
| 사양 확인 요청 | 근거가 모자라 판정할 수 없어 사람에게 사양 확인을 넘긴다 |

삭제된 SRS를 아직 가리키는 TC는 AI 없이 코드로 찾아 `수정 필수` 후보로 올린다.

> **참고** 화면과 결과 파일에 쓰는 판정 이름은 QA 규칙의 표기를 그대로 따른다. 정확한 문자열 목록은 `app/modules/daily_qa/schema.py` 의 `VERDICTS` 에 있다. 위 표의 "사양 확인 요청"은 규칙 표기로 "사양 확인" 뒤에 "필요"가 붙은 이름이다.

### REQ-DAILY-004 이슈 → 수정확인·Regression 초안 (C)

새 이슈마다 Skill `vxvue-issue-verification` 이 이슈 유형을 먼저 나눈다(QA 규칙 §6의 일곱 유형). 수정확인 TC와 Regression TC 초안은 `Program Fixed` 유형에만 만든다. 다른 유형은 판정과 근거만 남긴다.

초안은 새 Excel 파일(`output/daily_qa/<실행ID>/impact_checklist_draft.xlsx`)로 만든다. 열 순서는 변경사항 영향성평가 Checklist와 같다(`Category | TC ID | 버전 | SRS No | 변경사항 | 변경사항 상세 | Title | Precondition | Test Step | Expected Result | Test Data`). AI 판단과 근거는 같은 파일의 `Review` 시트에 따로 둔다.

> **주의** 기존 Checklist와 TC 원본 파일은 읽기만 하고 고치지 않는다.

### REQ-DAILY-005 사양–TC 추적 공백 (E)

설정한 요일(`daily_qa.weekly_day`, 기본 월요일)에 코드로 계산한다.

- TC가 하나도 가리키지 않는 유효 SRS(분류용 Category 항목 제외) → `TC 없음`
- 스냅샷에 없는 Polarion ID(`VP-123` 모양)를 가리키는 TC → `삭제된 SRS 참조`

Legacy 번호(`03-10-05` 모양)가 스냅샷의 어느 `oldId`와도 맞지 않으면 삭제로 단정하지 않고 `구 번호로 확인 불가` 건수로만 요약에 적는다.

> **예시** 2026-09-28 실데이터에서 TC가 쓰는 Legacy 번호 317종 가운데 239종이 현재 `oldId`와 맞지 않았다. 옛 번호 체계로 보이며, 이것을 삭제로 올리면 오탐이 된다.

이 단계는 AI를 부르지 않는다. 결과는 Finding으로 저장한다. 같은 SRS·같은 판정의 Finding이 이미 `검토 대기`·`승인`·`근거 추가 필요` 상태로 남아 있으면 새로 만들지 않는다(B도 같다).

### REQ-DAILY-006 매뉴얼 누락 후보 (F)

설정한 요일이거나 지식 폴더의 매뉴얼 파일이 바뀐 날에 돈다. 지난 7일 동안 바뀐 SRS 가운데 매뉴얼에 들어가야 할 내용이 빠졌을 수 있는 것을 Skill `vxvue-manual-completeness` 가 후보로 낸다. 결과는 항상 `보강 권장` 이하의 후보이며 확정 판정이 아니다.

### REQ-DAILY-007 AI 결과 형식 검증

Skill은 정해진 JSON 형식(`app/modules/daily_qa/schema.py`)으로 결과 파일을 쓴다. 코드가 그 파일을 검사하고, 다음 중 하나라도 해당하면 그 Finding을 버리고 이유를 남긴다.

- 근거(`evidence`)가 하나도 없거나, 근거에 문서 위치(SRS ID, 시트·행, 절)가 없다.
- 판정 값이 정해진 목록에 없다.
- 이슈를 닫거나 기존 TC를 덮어쓰라는 조치를 담고 있다.

결과 파일 자체가 없거나 JSON이 아니면 한 번 더 실행하고, 그래도 안 되면 그 작업을 `실패`로 남긴다. 모든 Finding은 `검토 대기` 상태로 저장된다.

이유: QA 규칙 §55는 근거 없는 판정 저장, 자동 Close, 기존 TC 덮어쓰기를 금지한다.

### REQ-DAILY-008 요약 메일

실행이 끝나면 수신자(`DAILY_QA_EMAIL_TO`, 없으면 `NOTIFY_EMAIL_TO`)에게 요약 메일을 한 통 보낸다. 메일에는 단계별 상태, Skill별 Finding 수, 판정별 건수, 답이 필요한 질문 수, 검토 화면 링크가 들어간다. 사양이나 이슈 본문은 메일에 넣지 않는다. 메일 발송이 실패해도 실행 결과는 그대로 저장된다.

### REQ-DAILY-009 검토 대기열 화면

`/daily-qa` 화면에서 다음을 할 수 있다.

1. 실행 목록과 실행별 단계 상태, 결과 파일을 본다.
2. Finding 목록을 Skill·판정·검토 상태로 걸러 본다.
3. Finding마다 `승인`, `거절`, `근거 추가 필요` 중 하나와 메모를 남긴다. 누가 언제 남겼는지 저장된다.
4. AI가 남긴 질문에 답을 적는다. 답은 다음 실행의 입력에 들어간다.

화면의 결정은 이 시스템 안에만 저장된다. Polarion이나 원본 TC 파일로 내보내지 않는다.

### REQ-DAILY-010 QA 규칙 판 확인

Skill은 자기가 따르는 QA 규칙 판(`SUPPORTED_RULES_REV`)을 갖는다. 수집된 QA 규칙 파일 이름의 Rev가 이와 다르거나 규칙 파일이 없으면, AI 단계(B·C·F)는 돌지 않고 `규칙 판 불일치` 로 남는다. 결정적 단계(E)는 계속 돈다. 메일 첫 줄에 이 사실을 적는다.

### NFR-SEC-001 AI 실행 격리

Claude는 아래 조건에서만 실행한다.

| 항목 | 조건 |
|---|---|
| 작업 폴더 | 저장소 밖(`daily_qa.workspace_dir`). 저장소 안이면 실행을 거부한다 |
| 도구 | 읽기·검색·쓰기 도구만 허용한다. 명령 실행(Bash), 웹 조회, MCP는 금지한다 |
| 설정 | 작업 폴더의 설정만 읽는다(`--setting-sources project`, `--strict-mcp-config`) |
| 입력 | 작업 폴더에 쓰기 전에 마스킹(`app/core/security_filter.py`)을 거친다 |
| 인증 | `CLAUDE_CODE_OAUTH_TOKEN` 을 비밀 설정에서 읽어 그 프로세스 환경에만 넘긴다 |
| 외부 통신 | 원격 측정과 오류 보고를 끄는 환경변수를 넣는다 |
| 기록 | 보낸 입력, 받은 결과, 실행 시간, 사용량을 실행 폴더에 남긴다 |

이유: 사내 사양서와 이슈를 외부 AI로 보내므로, 무엇이 나갔는지 확인할 수 있어야 하고 AI가 서버의 다른 파일이나 네트워크에 손대지 못해야 한다.

### REQ-ISSUE-001 이슈 Export 폴더 읽기

QA Agent 는 제품 설정의 이슈 Export 폴더(`issue_source.export_dir`)에서 이슈 목록과 이슈 본문(`backup.json`)을 읽는다. 폴더 안의 두 가지 구조를 모두 읽는다.

| 구조 | 모양 | 만든 곳 |
|---|---|---|
| 예전 구조 | `<이슈ID>/backup.json` | 통합 전 `alm-issue-export` |
| 실행 폴더 구조 | `<실행폴더>/<이슈ID>/backup.json` | ALM-QA-Automation 의 issue-export 앱 |

- 같은 이슈가 여러 실행 폴더에 있으면 가장 최근 실행 폴더의 것을 쓴다. 실행 폴더 이름이 실행 시각으로 시작하므로 이름이 뒤에 오는 쪽이 최근이다.
- 실행 폴더에도 예전 구조에도 있으면 실행 폴더 쪽을 쓴다.
- 이름이 `.` 으로 시작하거나 `.staging-`·`.previous-` 가 들어간 폴더는 내보내는 중이거나 교체 전 보관본이므로 읽지 않는다.
- 폴더가 없으면 오류 없이 빈 목록을 돌려준다.

> **예시** 2026-09-28 실데이터에서 예전 구조 20건과 실행 폴더 21건이 함께 있었다. 예전 구조만 읽으면 최신 21건이 목록에서 빠진다.

## 9. 오류 처리 정책

누락 TC를 성공으로 숨기지 않고 missing_tc_ids로 반환한다. 공집합의 지표 처리는 구현의 명시적 분기와 함께 후속 테스트 범위에서 확인한다. 외부 AI 오류·전체 분석 파이프라인 오류 정책은 이번 범위 밖이다.

## 11. 테스트 사양

루트 핵심 앱의 순수 평가 함수만 검사한다. services 하위 서비스, 실제 API/기밀 E2E fixture/운영 DB를 호출하는 명령으로 확대하지 않는다.

개발 의존성을 준비하고 프로젝트 루트에서 실행할 명령:

```text
python -m pytest tests/test_evaluation.py -q
```

이번 작업에서는 테스트 본문과 구현 연결을 확인했으며 명령을 실제 실행하지 않았다.

### TEST-CORE-001

REQ-CORE-001의 입력과 결과를 `tests/test_evaluation.py`의 `test_recommendation_metrics_counts_precision_and_recall` fixture/assertion으로 검증한다. 해당 assertion 실패는 검사 실패다.

### TEST-CORE-002

REQ-CORE-002의 입력과 결과를 `tests/test_evaluation.py`의 `test_evaluate_analysis_lists_missing_and_unexpected_ids` fixture/assertion으로 검증한다. 해당 assertion 실패는 검사 실패다.

### TEST-CORE-003

REQ-CORE-003의 입력과 결과를 `tests/test_evaluation.py`의 `test_aggregate_evaluations_uses_micro_average` fixture/assertion으로 검증한다. 해당 assertion 실패는 검사 실패다.

### TEST-DEPLOY-001

REQ-DEPLOY-001을 `tests/test_serve_bind.py`로 검증한다. 세 가지를 분리해서 본다.

- 동작 검증: 임시 루트에 다른 포트를 적은 `config.yaml` 을 놓고 실제 `build_settings()` 를
  통과시켜 `app_bind()` 의 결과가 따라오는지 본다. 빈 값일 때만 기본값으로 떨어지는지,
  정수가 아닌 포트에서 실패하는지도 같은 방식으로 본다.
- 텍스트 검사: 세 진입점이 `--port` 를 들고 있지 않고 `app.serve` 를 부르는지 본다. 이 검사는
  파일에 포트 지정이 없다는 것만 증명하며 서버가 어느 포트에 뜨는지는 증명하지 않는다.
- 텍스트 대조: nginx upstream 포트가 `config.yaml` 의 `app.port` 와 같은지 본다.

`tests/test_monitor_health.py` 의 `test_default_base_url_comes_from_config_yaml` 은 운영 점검의
기본 대상이 같은 원본에서 나오는지를 별도로 본다. 해당 assertion 실패는 검사 실패다.

실행 명령:

```text
python -m pytest tests/test_serve_bind.py tests/test_monitor_health.py -q
```

### TEST-DAILY-001

일일 점검 전체 흐름을 `tests/test_daily_qa_pipeline.py` 로 검증한다. 가짜 Polarion 응답과 가짜 Claude 실행기(실제 CLI 대신 정해진 결과 파일을 쓰는 함수)를 넣어 실행 기록, 단계 상태, 잠금, 단계 하나가 실패해도 나머지가 도는지 본다.

### TEST-DAILY-002

`tests/test_daily_qa_polarion.py` 로 수집 코드가 GET만 쓰는지, 스냅샷 비교가 신규·삭제·변경을 맞게 나누는지, 이슈를 마지막 성공 시각 이후로 거르는지 본다.

### TEST-DAILY-003

`tests/test_daily_qa_packages.py` 로 TC 색인과 후보 선택(B), 삭제 SRS 참조 TC 탐지, 추적 공백 계산(E)을 본다.

### TEST-DAILY-004

`tests/test_daily_qa_outputs.py` 로 결과 형식 검증(REQ-DAILY-007)과 초안 Excel 열 순서·Review 시트 분리(REQ-DAILY-004)를 본다.

### TEST-DAILY-005

`tests/test_daily_qa_router.py` 로 검토 화면의 목록·필터·결정 저장·질문 답변을 본다.

### TEST-DAILY-006

`tests/test_daily_qa_runner.py` 로 Claude 실행 명령의 도구 허용·금지 목록, 설정 출처 제한, 작업 폴더가 저장소 안이면 거부하는지, 입력 마스킹, 비밀값이 로그에 남지 않는지, 규칙 판 불일치 시 AI 단계를 건너뛰는지, 메일 본문에 사양 본문이 없는지 본다.

실행 명령:

```text
python -m pytest tests/test_daily_qa_pipeline.py tests/test_daily_qa_polarion.py tests/test_daily_qa_packages.py tests/test_daily_qa_outputs.py tests/test_daily_qa_router.py tests/test_daily_qa_runner.py -q
```

### TEST-ISSUE-001

REQ-ISSUE-001을 `tests/test_polarion_issue.py` 로 검증한다.

- 합성 폴더: 두 구조가 섞인 목록, 같은 이슈가 여러 실행에 있을 때 최근 실행 우선, `.staging-`·`.previous-` 폴더 제외.
- 실데이터(이 PC에 Export 폴더가 있을 때만): 목록이 디스크의 `backup.json` 을 목록 코드와 다른 방법으로 센 값과 같은지 본다. "목록이 비어 있지 않다"만 확인하면 예전 구조만 읽어도 통과하기 때문이다.

실행 명령:

```text
python -m pytest tests/test_polarion_issue.py -q
```

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-CORE-001 | `app/core/evaluation.py` | TEST-CORE-001: `tests/test_evaluation.py` | implemented |
| REQ-CORE-002 | `app/core/evaluation.py` | TEST-CORE-002: `tests/test_evaluation.py` | implemented |
| REQ-CORE-003 | `app/core/evaluation.py` | TEST-CORE-003: `tests/test_evaluation.py` | implemented |
| REQ-DEPLOY-001 | `app/core/config.py`, `app/serve.py` | TEST-DEPLOY-001: `tests/test_serve_bind.py` | verified |
| REQ-DAILY-001 | `app/modules/daily_qa/pipeline.py`, `app/modules/daily_qa/scheduled_jobs.py`, `scripts/run_daily_qa.py` | TEST-DAILY-001: `tests/test_daily_qa_pipeline.py` | implemented |
| REQ-DAILY-002 | `app/modules/daily_qa/polarion.py`, `app/modules/daily_qa/srs_snapshot.py` | TEST-DAILY-002: `tests/test_daily_qa_polarion.py` | implemented |
| REQ-DAILY-003 | `app/modules/daily_qa/packages.py`, `app/modules/daily_qa/skills/vxvue-spec-change-impact/SKILL.md` | TEST-DAILY-003: `tests/test_daily_qa_packages.py` | implemented |
| REQ-DAILY-004 | `app/modules/daily_qa/checklist_xlsx.py`, `app/modules/daily_qa/skills/vxvue-issue-verification/SKILL.md` | TEST-DAILY-004: `tests/test_daily_qa_outputs.py` | implemented |
| REQ-DAILY-005 | `app/modules/daily_qa/packages.py` | TEST-DAILY-003: `tests/test_daily_qa_packages.py` | implemented |
| REQ-DAILY-006 | `app/modules/daily_qa/packages.py`, `app/modules/daily_qa/skills/vxvue-manual-completeness/SKILL.md` | TEST-DAILY-001: `tests/test_daily_qa_pipeline.py` | implemented |
| REQ-DAILY-007 | `app/modules/daily_qa/schema.py` | TEST-DAILY-004: `tests/test_daily_qa_outputs.py` | implemented |
| REQ-DAILY-008 | `app/modules/daily_qa/report.py` | TEST-DAILY-006: `tests/test_daily_qa_runner.py` | implemented |
| REQ-DAILY-009 | `app/modules/daily_qa/router.py`, `app/core/daily_qa_storage.py` | TEST-DAILY-005: `tests/test_daily_qa_router.py` | implemented |
| REQ-DAILY-010 | `app/modules/daily_qa/rules.py` | TEST-DAILY-006: `tests/test_daily_qa_runner.py` | implemented |
| NFR-SEC-001 | `app/modules/daily_qa/agent_runner.py`, `app/modules/daily_qa/workspace.py` | TEST-DAILY-006: `tests/test_daily_qa_runner.py` | implemented |
| REQ-ISSUE-001 | `app/parsers/polarion_issue.py` | TEST-ISSUE-001: `tests/test_polarion_issue.py` | implemented |

implemented는 구현·테스트 소스 연결을 확인했다는 뜻이며 실제 실행 통과를 의미하지 않는다. DAILY·SEC 항목은 2026-09-28 에 테스트를 실제로 실행해 통과했지만, 이 저장소에는 테스트를 돌리는 게이트(`botyard.json` 의 `verify`)가 없어 implemented 로 둔다. verified는 테스트를 실제로 실행해 통과했고, 고의 파손으로 실패하는 것까지 확인했으며, 대표 실행 경로(`python -m app.serve` 로 기동 후 `/health` 조회)를 실제로 돌렸다는 뜻이다.

## 13. 미확정 사항

- (TBD) 새 이슈 조회식 `daily_qa.polarion.issue_query` 의 기본값은 `verified` 상태 또는 연구소 검토 결과 `lab_fixed` 인 이슈다. 실제 운영에서 쓰는 조회식은 확인 필요.
- (TBD) TC의 옛 Legacy SRS 번호와 현재 `oldId` 를 잇는 대응표가 있는지 확인 필요. 대응표가 있으면 B의 후보 선택과 E의 `TC 없음` 정확도가 올라간다.
- (TBD) Codex로 결과를 한 번 더 확인하는 교차 검증(G7)은 이후 고도화 범위다.

- 분석·매뉴얼 검토·저장·웹 UI·독립 하위 서비스의 전체 사양 추적성과 실제 운영 검증은 확인 필요다.
- 미확정 범위 검토 전에는 전체 프로젝트 readiness 완료로 보고하지 않는다.
- REQ-DEPLOY-001 은 2026-09-22 에 운영 서버(10.13.0.222)에 실제로 반영했다. 방화벽·nginx·systemd 는 저장소 밖의 조치이므로 사양의 검증 범위에 들어가지 않는다 — 수행 절차와 검증 결과는 `CHANGELOG.md` 와 `progress.md` 에 있다. nginx upstream 포트는 `config.yaml` 을 자동으로 따라가지 못하므로 배포마다 사람이 맞춰야 하고, 어긋남은 TEST-DEPLOY-001 이 잡는다.
