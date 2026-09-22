# QA 검증 관리 시스템 사양서

<!-- spec-template: v1 -->

| 항목 | 값 |
|---|---|
| Document Version | 0.2.0 |
| Last Updated | 2026-09-22 |
| Status | draft — 기존 계약 일부의 근거 기반 사양화 |

기존 코드와 테스트의 명시적 약속을 처음으로 연결한 제한된 기준선이다. 전체 제품 사양이나 운영 검증 완료를 뜻하지 않는다. 기존 약속과 구현의 차이는 SPEC / CODE MISMATCH로 남기고 코드에 맞춰 약속을 바꾸지 않는다.

## 1. 목적

변경사항과 사양·Test Case를 대조하여 검증 대상과 근거를 제공한다.

## 2. 프로젝트 범위

포함: 추천 평가 지표 및 누락 판정, 핵심 앱의 listen 주소·포트 결정 방식.

제외: 운영 데이터·비밀 설정의 열람/변경, 기능 수정, 인증·예약 실행 변경, listen 포트 외의 배포 절차 변경. 이 제외는 작업 경계이며 기존 제품 기능을 제거한다는 뜻이 아니다.

## 5. 기능 요구사항

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

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-CORE-001 | `app/core/evaluation.py` | TEST-CORE-001: `tests/test_evaluation.py` | implemented |
| REQ-CORE-002 | `app/core/evaluation.py` | TEST-CORE-002: `tests/test_evaluation.py` | implemented |
| REQ-CORE-003 | `app/core/evaluation.py` | TEST-CORE-003: `tests/test_evaluation.py` | implemented |
| REQ-DEPLOY-001 | `app/core/config.py`, `app/serve.py` | TEST-DEPLOY-001: `tests/test_serve_bind.py` | verified |

implemented는 구현·테스트 소스 연결을 확인했다는 뜻이며 실제 실행 통과를 의미하지 않는다. verified는 테스트를 실제로 실행해 통과했고, 고의 파손으로 실패하는 것까지 확인했으며, 대표 실행 경로(`python -m app.serve` 로 기동 후 `/health` 조회)를 실제로 돌렸다는 뜻이다.

## 13. 미확정 사항

- 분석·매뉴얼 검토·저장·웹 UI·독립 하위 서비스의 전체 사양 추적성과 실제 운영 검증은 확인 필요다.
- 미확정 범위 검토 전에는 전체 프로젝트 readiness 완료로 보고하지 않는다.
- REQ-DEPLOY-001 은 2026-09-22 에 운영 서버(10.13.0.222)에 실제로 반영했다. 방화벽·nginx·systemd 는 저장소 밖의 조치이므로 사양의 검증 범위에 들어가지 않는다 — 수행 절차와 검증 결과는 `CHANGELOG.md` 와 `progress.md` 에 있다. nginx upstream 포트는 `config.yaml` 을 자동으로 따라가지 못하므로 배포마다 사람이 맞춰야 하고, 어긋남은 TEST-DEPLOY-001 이 잡는다.
