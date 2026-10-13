# QA 검증 관리 시스템 — 작업 인수인계

(구 프로젝트명: AI Regression Impact Analyzer / `ai-regression-impact-analyzer`. 2026-09-01
표시명·GitHub repo·로컬 폴더명을 변경했다. 원격 서버 배포 경로는 운영 리스크상 의도적으로
이전 이름을 그대로 유지한다 — 아래 참고.)

## 1. 프로젝트 목적

QA 담당자가 사양·이슈의 바뀐 부분과 확인할 검증 범위를 앱에서 찾도록 돕는다. 평일 일일 점검, 단일 이슈 분석, 매뉴얼 개정 검증을 제공한다. TC 점검과 검증 TC 초안은 사람이 결과 화면에서 요청할 때 수행한다.

Regression 영향 분석 화면·API·추천 평가는 2026-10-01 제거됐다(`0fdf2f2`). 기존 DB·분석·평가 기록은 남아 있다. 기본 AI 제공자는 Claude CLI이며 Gemini도 설정으로 선택할 수 있다.

## 2. 작업 위치

- Canonical source: `C:\Users\2024980\Documents\자동화\projects\qa-verification-management-system`
- Ubuntu 배포본: `/home/ubuntu/ai-regression-impact-analyzer` (의도적으로 구 이름 유지 —
  운영 중인 systemd 유닛·venv·배포 설정의 경로 참조를 다시 확인해야 한다. `.deploy.env`의
  `DEPLOY_TARGET_DIRECTORY`로 로컬 폴더명과 독립적으로 관리된다)
- 공개 GitHub: `https://github.com/hongmin3/qa-verification-management-system`
- 서버 접속: SSH config/key를 사용하며 비밀번호를 파일에 저장하지 않는다.

**완료: 로컬 폴더 rename** (2026-09-02 확인). 로컬 폴더가 `qa-verification-management-system`로
rename됐고, Windows 작업 스케줄러 `AIRegressionAnalyzer_VXvueSpecSync`의 Execute/Arguments/
WorkingDirectory도 새 경로로 갱신되어 있음을 실제로 확인했다(`Get-ScheduledTask`로 재검증).
`akela.json`/Project Root 탐색은 폴더명이 아니라 파일 존재 여부만으로 동작하므로 영향 없다.

로컬 소스를 기준으로 개발하고 테스트를 통과한 결과만 서버에 배포한다. 서버 배포 폴더는 Git 저장소가 아닌 일반 디렉터리다.

## 3. 반드시 먼저 읽을 문서

1. `AGENTS.md`
2. `akela/PROTOCOL.md`
3. 해당 activity로 `akela compile`한 slice
4. `README.md`
5. `SECURITY.md`
6. 이 문서

## 4. 현재 구현 상태 (2026-10-13, 상세 변경·실행 근거는 `progress.md`)

- 루트(`/`)는 QA 자동화 허브이며 QA Agent(`/qa-agent`)와 매뉴얼 개정
  검증(`/manual-review`)으로 진입한다. 단일 이슈 분석은 `/qa-agent/issue-analysis`다. 옛 `/impact-analyzer` 주소는 QA Agent로 308 이동한다. 공용 HTML 골격·스타일은 `app/web/`이 소유하고,
  기능별 브랜드·내비게이션·사용법·화면·로직은 `app/modules/*`가 소유한다.
  `app/modules/knowledge/`가 사양서·TC 등록/삭제/동기화를 전담하며 두 검증 기능이 같은
  문서를 사용한다. 핵심 앱 내부의 공통 DB와 하위 서비스 분리 원칙은 `docs/SHARED_PLATFORM_ARCHITECTURE.md`.
- **QA Intelligence Agent**: 평일 07:30(한국 시간)에 운영 서버가 Polarion SRS·이슈를 수집하고 스냅샷과 비교한다. 바뀐 자료만 Claude로 분석해 결론·근거·QA 할 일을 남긴다. 일일 자동 분석은 TC·매뉴얼을 보내지 않는다. TC 점검·수정 완료 이슈의 검증 TC 초안은 상세 화면의 요청 버튼으로 수행한다.
- **단일 이슈 분석**: 이슈 한 건을 등록 지식과 대조해 사양·TC·Regression 검증 범위를 정한다. Exact와 BM25로 근거 후보를 찾고 코드가 검증 관문과 모델 응답의 번호·근거를 확인한다. 핵심 앱의 QA 승인 기록은 별도 이력으로 보존한다.
- **폐기 기능의 역사**: Regression 영향 분석은 변경 문서·등록 사양서·TC로 추천 목록과 초안을 만들고 QA 평가값을 적립하던 기능이다. 현재 새 요청을 접수하지 않으며 과거 기록은 보존한다.
- **매뉴얼 개정 검증**: DOCX Track Changes/PDF Baseline-Round diff 추출 → NON_FUNCTIONAL
  필터 → 2단계(quick/detail) AI 판정(PASS면 상세 호출 생략) → SRS 근거는 공용 Knowledge가
  관리하는 등록 사양서를 그대로 재사용 → QA Override → Word Comment 자동 삽입(변경 요소
  단위로 정밀 앵커링). Release Note/설계검토보고서 파서로 Release Scope Before/Now 근거와
  Pass/Fail(Fail은 Human Review 강제)을 연결하고, Reverse 검증으로 "누락 의심" 후보를
  BM25(+토큰 겹침 fallback)로 찾는다. 같은 제품의 다른 매뉴얼과 Cross-Manual 대조, DOCX/PDF
  이미지 변경은 Human Review Gate로 강제. PDF diff는 confidence 60% 상한 + Word Comment
  미생성.
- **QA Manual Hub 통합**(`services/qa-manual-hub/`): 제품 문서를 Git처럼 Revision 이력으로
  보관하는 별도 FastAPI+PostgreSQL 서비스. 코드/DB는 공유하지 않고 HTTP API로만 연동해
  Cross-Manual 대조 소스로 활용한다. 같은 호스트 nginx가 `/`(핵심 앱 :24357)와
  `/manual-hub/*`(SPA+백엔드 :24358)를 하나의 진입점으로 라우팅한다. 2026-09-02 적용한 self-signed HTTPS 강제 전환은 같은 날 되돌렸다. 정식 또는 사내 CA 인증서 방안은 사용자 결정 대기다.
- 운영: `qa-verification.service`/`qa-manual-hub.service`/`nginx` 모두 systemd로 자동
  복구. `scripts/monitor_health.py`가 10분 간격으로 핵심 앱·nginx·manual_hub를 감시하고,
  `scripts/backup_data.py`가 매일 SQLite Online Backup+SHA-256 검증을 수행한다.
  `/operations/status`에서 동시 실행 제한·stale job·백업 상태를 확인할 수 있다.
- 기본 AI 설정은 `ai.provider: claude_cli`이며 모델·추론 정도는 `ai.claude.models.*`·`ai.claude.effort`를 쓴다. Gemini를 선택하면 기존 API Key 설정과 제공자를 사용한다.
- VXvue SRS 수집은 담당자 PC `QA_SRS_Collect` 평일 09:00, 서버 사양서 동기화는 09:40, 지식 폴더 업로드는 10:00이다. PC의 07:30 일일 점검 예약은 껐다. PC 테스트 서버 주소는 `http://127.0.0.1:24357`이다.
- 서버 DNS 복구는 이번 세션에서 확인했다. SMTP는 처음 인증정보 없이 `530 Authentication Required`로 거절됐으나, 사용자 설정 후 인증정보 등록 상태가 확인되고 `send_report`는 `sent`·1건을 반환했다. 받은편지함 수신과 새 실제 점검 결과는 별도 확인 대상이다. 실행·배포 상태와 최신 시험 결과는 `progress.md`를 읽는다.

> **과거 확인 기록** 2026-09-02 로컬 pytest는 248개 통과였다. 이후 기능 추가·제거로 테스트 구성과 수가 바뀌었으므로 현재 검증 결과로 쓰지 않는다.

## 5. 현재 남은 작업

`NEXT_STEPS.md`를 우선순위 순으로 참고한다. 이 파일에는 중복해서 유지하지 않는다.

## 6. systemd

핵심 앱·매뉴얼 허브·nginx 모두 systemd 유닛으로 등록·enable돼 있다(2026-09-02 전환
완료). 유닛 파일은 `deploy/systemd/qa-verification.service`(핵심 앱)와
`services/qa-manual-hub/deploy/`(매뉴얼 허브) 참고. 재기동은
`sudo systemctl restart qa-verification`(또는 `scripts/deploy.ps1 -Restart`).

재기동 전에 `/qa-agent/status`로 분석이 실행 중인지 확인한다. systemd 재기동은 분석 하위 프로세스도 종료한다. sudo 인증정보를 자동으로 사용하지 않는 배포는 `scripts/deploy.ps1 -Restart -ManualRestart`로 전송한 뒤, 안내된 재기동 명령을 사용자가 서버에서 수행한다.

## 7. 운영 서버 절대 보호 규칙

- 서버 reboot 금지
- **이 프로젝트가 설치하지 않은** systemd 유닛·nginx 사이트·PostgreSQL 설정·
  virtualenv/requirements는 변경 금지. 이 프로젝트가 소유한 것(`qa-verification.service`,
  `qa-manual-hub.service`, `deploy/nginx/qa-platform.conf`)은 사용자 승인 후 변경 가능—
  실제로 여러 세션에서 HTTPS 적용·구주소 블록 제거 등을 이렇게 진행했다.
- 방화벽(`ufw`)은 이 프로젝트가 쓰는 포트를 여는 등 이 프로젝트 범위 내 변경만 허용
  (2026-08-31 사용자 승인, `SECURITY.md` 반영). 그 외 규칙 변경 금지
- `/home/ubuntu/jjhhub/` 내부 열람·수정 금지
- `/mnt/vhdmaster`, `/mnt/vhdmaste` 접근·권한·마운트 변경 금지
- 신규 포트를 사용할 때 `ss -ltnp`로 먼저 재확인
- 서버 변경은 `/home/ubuntu/ai-regression-impact-analyzer` 내부(또는 이 프로젝트가 소유한
  nginx/systemd 설정 파일)로 제한
- AI·Polarion·SMTP 인증정보를 코드, Git, README, 로그, Report에 기록하지 않는다. 서버 `sudo` 비밀번호를 쓰는 기존 자동화의 관리 규칙은 `SECURITY.md`를 따른다. 이번 후속 작업은 sudo 비밀번호를 읽거나 입력하지 않으며 필요한 명령은 사용자가 서버에서 실행한다.

## 8. 개발 및 검증 명령

```powershell
cd "C:\Users\2024980\Documents\자동화\projects\qa-verification-management-system"
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\run.ps1
```

배포(파일 전송 + 의존성 설치, 필요하면 재기동까지):

```powershell
.\scripts\deploy.ps1            # 재기동 안 함
.\scripts\deploy.ps1 -Restart   # + sudo systemctl restart qa-verification + 헬스체크
.\scripts\deploy.ps1 -Restart -ManualRestart # 전송 뒤 사용자 재기동 대기
```

배포 스크립트는 Git에서 제외된 `.deploy.env`의 Host/User/Target을 사용한다. Password 항목은 만들지 않는다.

SSH 키 인증의 과거 확인 명령은 `ssh -o BatchMode=yes ubuntu@10.13.0.222 'echo ok'`다. 당시 `~/.ssh/id_ed25519`에 passphrase가 없어 ssh-agent가 필요하지 않았고 Windows `ssh-agent` 서비스는 Stopped/Disabled였다. 이 기록을 이유로 현재 인증 설정이나 서비스를 바꾸지 않는다. SSH 비밀번호는 어떤 파일에도 저장하지 않으며, 보관이 필요하면 Windows 자격 증명 관리자나 password manager를 쓴다.

서버 확인:

```bash
cd /home/ubuntu/ai-regression-impact-analyzer
.venv/bin/python -m pytest -q
curl -fsS http://127.0.0.1:24357/health
ss -ltnp 'sport = :24357'
```

## 9. 비밀정보 파일

- `secrets.txt`: AI 인증·Polarion·메일 설정을 넣는 기본 파일. Gemini를 선택할 때는 `GEMINI_API_KEY=`를 넣는다. Git 제외
- `secrets.json`: 같은 목적의 JSON 대안. Git 제외
- `.env`: 기존 방식. 계속 동작하며 우선순위는 가장 낮다. Git 제외
- 값 우선순위: OS 환경변수 > `secrets.json` > `secrets.txt` > `.env` > 기본값
- `.deploy.env`: SSH Host/User/Target만 저장, Git 제외
- `real_fixtures.local.env`: `tests/test_manual_review_real_files_e2e.py` 전용, 사내망 실제
  VXvue 1.1.0 예시 파일의 기준 폴더 경로 2개만 저장. Git 제외(`.example`만 공개). 비밀정보는
  아니지만 사내 서버 IP·부서 폴더 체계를 공개 repo에 노출하지 않기 위해 `.deploy.env`와
  동일하게 취급
- `secrets.example.txt`, `secrets.example.json`, `.env.example`, `.deploy.env.example`: 값 없는 공개 예제
- `SERVER_SUDO_PASSWORD`: 2026-08-31 사용자 결정으로 도입한 기존 서버 운영 자동화용 키. 앱은 이 키를 읽지 않으며(`secrets_loader.py` 미인식 키), 기존 SSH/`sudo -S` 자동화는 값이 화면·로그에 나오지 않게 파일에서 표준입력으로만 전달한다. 이번 후속 작업은 이 값을 읽거나 입력하지 않는 `-ManualRestart` 방식을 사용한다.

SSH 접속 자체는 여전히 key 인증을 사용한다. 위 `SERVER_SUDO_PASSWORD`는 접속이 아니라 접속 이후의 `sudo` 실행에만 쓰인다.

2026-08-31 기준 로컬과 서버 모두 `secrets.txt` Key 설정이 확인됐다. 서버 파일은 `/home/ubuntu/ai-regression-impact-analyzer/secrets.txt`, 소유자 `ubuntu`, 권한 `600`이다. Key 값 자체는 확인·출력하지 않는다.

## 10. 작업 완료 시 확인

- `pytest` 통과
- `git diff --check` 통과
- 비밀정보 및 업로드/DB/로그 추적 여부 확인
- 서버 반영 전 포트 재검사
- 배포 후 기존 보호 서비스 `active/running` 재확인
- `akela log applied` 및 `akela log outcome` 수행
- GitHub push 전 공개 가능한 파일만 포함됐는지 재검사

## 11. 알려진 한계

- Akela CLI `0.1.4`가 전역 설치되어 compile/applied/outcome 기록이 정상 동작한다.
- 완료된 분석과 요청 입력은 SQLite에 저장된다. 재시작 시 QUEUED 작업은 원본이 있으면 자동
  재제출하고, 이미 실행 중이던 RUNNING 작업은 FAILED로 안전하게 전환한 뒤 UI에서 재실행한다.
- 등록 사양서·TC의 파싱 결과는 `data/indexes/<document_id>.json`에 캐시하며, 사양서 전체 원문은
  `<document_id>.text`에 별도 캐시한다. BM25 객체는 버전 호환성을 위해 직렬화하지 않고 캐시된
  Pydantic 데이터로 매 분석마다 재구성한다. 캐시가 없거나 손상되면 원본을 다시 파싱해 자동 복구한다.
- 현재 복구 Queue는 단일 uvicorn 인스턴스용 thread 기반이다. 다중 서버·대규모 부하가 실제로
  필요해지면 PostgreSQL과 외부 Queue로 전환한다.
