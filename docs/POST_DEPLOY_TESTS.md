# 배포 후 테스트 — 실서버에 반영한 뒤 확인할 것

> 상위: [문서 지도](README.md) · 배포 절차: [DEPLOYMENT.md](DEPLOYMENT.md)

로컬에서 검증할 수 있는 것은 자동 테스트로 덮었다(`pytest` 535건). 이 문서는 **로컬에서
확인할 수 없는 것만** 남긴다 — 실서버에만 있는 조건(경로·권한·네트워크·결제·스케줄러)에
의존하는 항목이다.

각 항목은 **무엇을 확인하는지**와 **실패하면 무엇이 원인인지**를 함께 적었다. 통과 여부만
체크하는 목록이 아니다.

---

## 0. 실행 전 준비

```bash
cd /srv/qa-verification-management-system && git pull && ./scripts/deploy.ps1 -Restart
```

로컬에서 이미 확인된 것 (다시 하지 않아도 되는 것):

| 확인됨 | 근거 |
|---|---|
| 전 화면 렌더링 (15개 경로 200 OK) | 로컬 실행 |
| 파이프라인 8단계 · Gate 판정 · ID 교차검증 | `tests/test_qa_agent_analyzer.py` |
| 마스킹 규칙 33건 | `tests/test_security_filter.py` |
| 두 제품 지식 폴더 스캔·분류·리비전 판별 | `tests/test_product_knowledge.py` (실제 폴더 opt-in 포함) |
| 실제 Issue 38건 파싱 | `tests/test_polarion_issue.py` (실제 Export opt-in) |
| 규칙 56개 절 분류 드리프트 | `tests/test_rule_capability.py` |

---

## 1. 기본 기동 — 5분

| # | 확인 | 방법 | 실패 시 원인 |
|---|---|---|---|
| 1.1 | 앱이 떴는가 | `curl -s localhost:12000/health` → `{"status":"ok"}` | systemd 유닛, `.venv` 의존성 |
| 1.2 | 새 라우트가 등록됐는가 | 브라우저에서 `/qa-agent`, `/qa-agent/guide`, `/qa-agent/rules`, `/knowledge/guide`, `/cost-dashboard/guide` | `git pull` 후 재시작 누락 |
| 1.3 | nginx가 새 경로를 넘기는가 | 외부 PC에서 `http://<서버>/qa-agent` | nginx가 `/` 전체를 프록시하면 추가 설정 불필요 |
| 1.4 | 정적 파일이 갱신됐는가 | `/qa-agent/analyses/<id>` 에서 Gate 색상 표시 확인 | 브라우저 캐시 → 강제 새로고침 |
| 1.5 | 기존 기능이 그대로인가 | `/impact-analyzer`, `/manual-review`, `/analyses` 진입 | 공용 로더 추출(`knowledge_documents`) 회귀 |
| 1.6 | 이력이 기능별로 분리됐는가 | `/analyses`에 QA Agent 분석이 섞이지 않는지 | `list_analyses(module=...)` 미적용 |

**DB 스키마.** `qa_agent_approvals` 테이블과 `analyses.module` 컬럼이 기동 시 자동 생성된다
(`CREATE TABLE IF NOT EXISTS` + 컬럼 보강). 별도 마이그레이션 명령이 없다.

```bash
sqlite3 /srv/qa-verification-management-system/data/app.db ".tables" | tr ' ' '\n' | grep qa_agent
```

---

## 2. 제품 지식 폴더 수집 — 서버에서 되는지가 관건

**로컬에서 확인할 수 없는 부분이다.** 지식 폴더는 QA 담당자 PC에 있고 서버에는 마운트로
붙인다. 마운트가 되기 전과 된 후의 동작이 다르므로 순서대로 확인한다.

| # | 확인 | 마운트 전 (기대) | 마운트 후 (기대) |
|---|---|---|---|
| 2.1 | `/knowledge` 상단에 제품별 지식 폴더 상태가 보이는가 | **"접근할 수 없습니다"** + CLI 안내 | 수집 대기 건수 표시 |
| 2.2 | "지금 수집" 버튼 | 숨어 있어야 한다 | 표시돼야 한다 |
| 2.3 | 그 상태에서 QA Agent가 어떻게 되는가 | 이미 수집된 사본이 있으면 정상. 없으면 G1 BLOCK | 정상 |

### 확정된 방식: 서버가 지식 폴더를 마운트한다

담당자 PC 와 서버가 **같은 폴더를 다른 경로로** 본다. `config/products/*.yaml` 의
`knowledge_source.dir` 은 `${ENV:-기본값}` 형태이므로 **서버에서만 환경변수를 설정**하면
되고 담당자 PC 는 아무것도 하지 않아도 된다.

| 제품 | 환경변수 |
|---|---|
| VXvue | `QA_KNOWLEDGE_DIR_VXVUE` |
| Bellalun Viewer | `QA_KNOWLEDGE_DIR_BELLALUN` |

**① 마운트** — 사내 공유 폴더를 서버에 읽기 전용으로 붙인다. 앱은 이 폴더를 쓰지 않으므로
`ro` 로 충분하다.

```bash
sudo mkdir -p /srv/knowledge/vxvue /srv/knowledge/bellalun
```

`/etc/fstab` 에 등록해 재부팅 후에도 유지한다 (자격증명은 별도 파일로 두고 `600` 권한).

```text
//<서버>/<공유>/VXvue/VXvue\040지식파일  /srv/knowledge/vxvue     cifs  ro,credentials=/etc/qa-knowledge.cred,iocharset=utf8,uid=<앱계정>,_netdev  0  0
//<서버>/<공유>/Bellalun\040Viewer/지식   /srv/knowledge/bellalun  cifs  ro,credentials=/etc/qa-knowledge.cred,iocharset=utf8,uid=<앱계정>,_netdev  0  0
```

> **한글 폴더명 주의.** `fstab` 은 경로의 공백을 `\040` 로 써야 하고, 한글 파일명이 깨지지 않게
> `iocharset=utf8` 이 필요하다. 마운트 후 `ls` 로 파일명이 정상인지 먼저 확인한다 —
> 파일명이 깨지면 분류·리비전 판별이 전부 실패한다.

**② 환경변수** — systemd 유닛에 넣는다.

```bash
sudo systemctl edit qa-verification
```

```ini
[Service]
Environment=QA_KNOWLEDGE_DIR_VXVUE=/srv/knowledge/vxvue
Environment=QA_KNOWLEDGE_DIR_BELLALUN=/srv/knowledge/bellalun
```

**③ 확인**

| # | 확인 | 기대 |
|---|---|---|
| 2.4.1 | 마운트가 붙었는가 | `ls "/srv/knowledge/vxvue"` 에 한글 파일명이 정상 표시 |
| 2.4.2 | 앱이 경로를 인식하는가 | `/qa-agent/readiness?product=VXvue` 의 응답, `/knowledge` 화면에 "지금 수집" 버튼 표시 |
| 2.4.3 | 스캔이 되는가 | `/knowledge/source/VXvue` — 분류 건수와 제외 이유 |
| 2.4.4 | 분류되지 않은 파일이 없는가 | 같은 응답의 `excluded` 에 "규약에 맞지 않음"이 없어야 한다 |
| 2.4.5 | 수집·등록이 되는가 | "지금 수집" → 알림의 등록/미변경/정리/중복 건수 |
| 2.4.6 | 주간 자동 수집 | 기동 로그의 `scheduled_job_registered id=sync_product_knowledge_*`, 다음 월요일 07:30/07:45 실행 |

마운트가 불가능해지면 CLI 방식으로 되돌릴 수 있다 — 환경변수를 빼면 기본값(PC 경로)이
되어 서버에서는 "접근할 수 없습니다"로 표시되고 화면이 CLI 절차를 안내한다.

```bash
python scripts/sync_product_knowledge.py --product VXvue --report-to http://10.13.0.222:12000
```

**검증 후 확인**

```bash
sqlite3 data/app.db "SELECT kind, product, COUNT(*) FROM documents GROUP BY kind, product;"
```

기대값(수집 완료 시): VXvue specification 6 · testcase 4 · manual 5 / Bellalun Viewer
specification 2 · testcase 4 · manual 5.

`/qa-agent/readiness?product=VXvue` 가 `rules_available: true`, `rule_revision: "Rev1.12"` 를
돌려주면 규칙까지 정상이다.

---

## 3. Gemini API — 실호출

**로컬에서 실패했다.** 파이프라인은 Gate 통과·검색·payload 조립·마스킹까지 정상 수행했고
API 호출 지점에서만 막혔다.

```text
429 RESOURCE_EXHAUSTED — Your prepayment credits are depleted.
```

| # | 확인 | 방법 | 실패 시 |
|---|---|---|---|
| 3.1 | API Key가 서버에 있는가 | `/config/status` (값은 반환하지 않고 설정 여부·길이·출처만) | `secrets.txt`를 서버에서 직접 넣는다 (배포되지 않음) |
| 3.2 | **결제 상태가 정상인가** | [AI Studio](https://ai.studio/projects) 에서 크레딧·결제 확인 | 소진 시 모든 분석이 `RESOURCE_EXHAUSTED`로 실패 |
| 3.3 | 기본 모델이 호출되는가 | QA Agent 분석 1건 실행 → `AI 호출 1회` | 모델 ID 확인 |
| 3.4 | 상위 등급 모델 정책 | `models.complex` 값 확인 | `gemini-2.5-pro`는 신규 계정에 미제공(실측). 미제공이면 기본 모델로 폴백되고 결과에 기록된다 |
| 3.5 | 폴백이 결과에 남는가 | 결과 화면 감사 영역의 `model_fallback` | 조용히 바뀌면 안 된다 |
| 3.6 | 회사 정책 승인 | 외부 생성형 AI API 사용 가능 여부 | **사용자 확인 필요** — 사내 문서 조각이 외부로 나간다 |

---

## 4. QA Agent 실동작 — 골든 케이스

실제 Issue로 유형별 대표 케이스를 돌려 결과를 기록한다. 규칙 §24가 요구하는 자체 테스트다.

| 케이스 | Issue 유형 | 확인할 것 |
|---|---|---|
| C1 | `lab_fixed` (Program Fixed) | Gate 전부 통과, TC Coverage 판정 생성, Regression 축 8개 전부 표시 |
| C2 | `lab_inspec` / `lab_nobug` (Spec/Not Bug) | **Runtime TC 자동생성 차단** 표시, G5가 Runtime TC를 막는지 |
| C3 | `lab_noact` (유형 미확정) | **G1이 NEED_INPUT으로 멈추고 AI 호출 0회** |
| C4 | `lab_duplicate` | 유형 분류가 Duplicate로 나오는지 |
| C5 | 검증 환경 미입력 | G4가 NEED_INPUT, AI 호출 0회 |
| C6 | 필수 관찰 수단 미확보 | G4가 BLOCK, AI 호출 0회 |
| C7 | "초안으로 작성" 체크 | 진행되고 상단에 "초안 모드" 표시 |
| C8 | 같은 Issue 재실행 | **캐시 적중** 표시, 토큰 증가 없음 |

각 케이스마다 기록:

| Case | Issue | Gate 결과 | AI 호출 | 토큰 | QA 수정 필요 | 실패 원인 | 규칙 보강 필요 |
|---|---|---|---|---|---|---|---|

> 이 표가 규칙 §22 Cognitive Debt 관리의 입력이다. AI 답변을 고치는 것으로 끝내지 말고
> **왜 틀렸는지를 규칙/Gate 문제로 환원**한다.

---

## 5. 마스킹 — 실제 payload 확인

**RAG를 쓴다고 외부 전송이 0이 되는 것은 아니다.** 실제로 무엇이 나갔는지 눈으로 확인한다.

| # | 확인 | 방법 |
|---|---|---|
| 5.1 | 실제 전송본을 볼 수 있는가 | 분석 결과 → 감사 영역 → **"보낸 입력 JSON"** |
| 5.2 | 원본 문서가 나가지 않았는가 | 사양 Chunk 발췌만 있고 전체 문서 텍스트가 없어야 한다 |
| 5.3 | 마스킹이 적용됐는가 | 감사 영역 `masking` 의 건수. 0건이면 마스킹할 값이 없었다는 뜻 |
| 5.4 | QA 식별자가 살아 있는가 | 전송본에 SRS/Issue ID, 버전, DICOM Tag가 **그대로** 있어야 한다 |
| 5.5 | 전송량이 예상 범위인가 | 입력 JSON 글자 수. 실측 기준 약 30,000자 |
| 5.6 | 로그에 원본 민감정보가 없는가 | `output/logs/` 에서 환자 ID·경로 패턴 확인 |

마스킹을 끄려면 `config.yaml` `security.mask_outbound: false` — **운영에서는 켠 채로 둔다.**

---

## 6. 스케줄러 — 다음 주 월요일 확인

| # | 확인 | 방법 |
|---|---|---|
| 6.1 | job이 등록됐는가 | 기동 로그의 `scheduled_job_registered id=sync_product_knowledge_*` |
| 6.2 | 마운트가 붙은 뒤 실제로 수집되는가 | 다음 월요일 `/knowledge` 의 마지막 수집 시각과 sync 로그 |
| 6.3 | 마운트가 끊겼을 때 조용히 건너뛰는가 | 로그 `knowledge_sync_skipped reason=지식_폴더_접근_불가` (오류가 아님) |
| 6.4 | ALM 사양서 동기화가 계속 도는가 | `/knowledge` 의 ALM 동기화 상태 (기존 기능) |

시간대는 `Asia/Seoul` 고정이다. 서버 TZ와 무관하다.

---

## 7. 운영 확인

| # | 확인 | 방법 |
|---|---|---|
| 7.1 | 동시 실행 제한 | 분석 3건 동시 실행 → 3번째가 429 |
| 7.2 | 재시작 복구 | 분석 중 재시작 → `RUNNING`이 실패로 정리되고 `QUEUED`가 이어짐 |
| 7.3 | stale 감지 | `/operations/status` |
| 7.4 | 백업 | `scripts/backup_data.py` 실행 후 `data/app.db` 포함 확인 |
| 7.5 | 모니터 | `scripts/monitor_health.py` |
| 7.6 | 죽은 등록 정리 | `/knowledge` → "죽은 등록 정리" → 반환 목록 확인 |

---

## 8. 5명 동시 사용

파트원 5명이 같은 서버를 쓴다. 현재 구조의 한계를 미리 확인한다.

| # | 확인 | 현재 상태 |
|---|---|---|
| 8.1 | 동시 분석 | `analysis.max_concurrent_jobs` 기본 2. 5명이 동시에 누르면 3명은 429 → 값 조정 필요 여부 판단 |
| 8.2 | 사용자 식별 | **핵심 앱에 로그인이 없다.** QA 승인 기록에 "누가" 승인했는지 남지 않는다 |
| 8.3 | SQLite 동시 쓰기 | 5명 규모에서는 문제 없으나, 승인 기록이 늘면 WAL 모드 검토 |
| 8.4 | 일일 토큰 한도 | 기본 0(비활성). 5명 × 10건 기준으로 상한을 정할지 판단 |

> **결정 대기 (8.2).** 승인 이력에 사용자를 남기려면 인증이 필요하다. Manual Hub에는 이미
> 세션 인증이 있으므로 그 계정을 재사용하는 방법과, 핵심 앱에 별도 인증을 붙이는 방법이 있다.
> 어느 쪽이든 인증 없이 평문 HTTP로 운영하는 현재 상태와 함께 결정해야 한다.

---

## 9. 미해결 인프라 항목 (이전부터 열려 있음)

| 항목 | 상태 |
|---|---|
| HTTPS 미적용 | self-signed 적용 후 브라우저 경고로 롤백. 정식 CA 또는 사내 CA 배포 결정 필요 |
| 매뉴얼 서버 백업이 원본과 같은 디스크 | 별도 볼륨/NAS 추가라는 인프라 결정 필요 |

자세한 경위: [NEXT_STEPS.md](../NEXT_STEPS.md)
