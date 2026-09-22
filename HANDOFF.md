# QA 검증 관리 시스템 — 작업 인수인계

(구 프로젝트명: AI Regression Impact Analyzer / `ai-regression-impact-analyzer`. 2026-09-01
표시명·GitHub repo·로컬 폴더명을 변경했다. 원격 서버 배포 경로는 운영 리스크상 의도적으로
이전 이름을 그대로 유지한다 — 아래 참고.)

## 1. 프로젝트 목적

SW 변경사항과 제품 사양서/Manual(PDF 또는 Word `.docx`), Test Case Excel을 입력받아 Rule Engine과 Gemini Semantic Decision Engine으로 Regression 검증 TC를 자동 추천한다. 사용자가 ChatGPT/Gemini Web/Claude/Codex에 별도로 질문하지 않는 업무 흐름이 핵심이다.

## 2. 작업 위치

- Canonical source: `C:\Users\2024980\Documents\자동화\qa-verification-management-system`
- Ubuntu 배포본: `/home/ubuntu/ai-regression-impact-analyzer` (의도적으로 구 이름 유지 —
  운영 중인 서비스 경로를 바꾸려면 systemd 없이 nohup으로 떠 있는 프로세스를 내리고
  venv/경로 참조를 전부 재검증해야 해서 리스크 대비 실익이 낮다고 판단. `.deploy.env`의
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

## 4. 현재 구현 상태 (2026-09-02 기준 요약, 상세 변경 서사는 `git log`)

- 루트(`/`)는 QA 자동화 허브이며 Regression 영향 분석(`/impact-analyzer`)과 매뉴얼 개정
  검증(`/manual-review`)으로 진입한다. 공용 HTML 골격·스타일은 `app/web/`이 소유하고,
  기능별 브랜드·내비게이션·사용법·화면·로직은 `app/modules/*`가 소유한다.
  `app/modules/knowledge/`가 사양서·TC 등록/삭제/동기화를 전담하며 두 검증 기능이 같은
  문서를 사용한다. 공유 DB와 새 QA 모듈 확장 원칙은 `docs/SHARED_PLATFORM_ARCHITECTURE.md`.
- **Regression 영향 분석**: 제품 선택 → 등록된 사양서·TC 전체를 검색 대상으로 사용 →
  기준 사양서 전체 텍스트와 diff한 실제 변경 줄만 Rule 매칭 → BM25 후보 압축 → Gemini
  Structured Output 1회 호출로 영향도 판정 + 커버되지 않는 변경의 신규 TC 초안까지 함께
  생성 → TC ID/Chunk ID 교차검증 → HTML 리포트 + XLSX. SQLite 캐시로 동일 입력 재분석은
  무료. 완료된 분석 상세 화면에서 QA가 확정 TC ID/메모를 남기면 precision/recall/F1이
  누적 집계된다.
- **매뉴얼 개정 검증**: DOCX Track Changes/PDF Baseline-Round diff 추출 → NON_FUNCTIONAL
  필터 → 2단계(quick/detail) AI 판정(PASS면 상세 호출 생략) → SRS 근거는 impact_analyzer가
  관리하는 등록 사양서를 그대로 재사용 → QA Override → Word Comment 자동 삽입(변경 요소
  단위로 정밀 앵커링). Release Note/설계검토보고서 파서로 Release Scope Before/Now 근거와
  Pass/Fail(Fail은 Human Review 강제)을 연결하고, Reverse 검증으로 "누락 의심" 후보를
  BM25(+토큰 겹침 fallback)로 찾는다. 같은 제품의 다른 매뉴얼과 Cross-Manual 대조, DOCX/PDF
  이미지 변경은 Human Review Gate로 강제. PDF diff는 confidence 60% 상한 + Word Comment
  미생성.
- **QA Manual Hub 통합**(`services/qa-manual-hub/`): 제품 문서를 Git처럼 Revision 이력으로
  보관하는 별도 FastAPI+PostgreSQL 서비스. 코드/DB는 공유하지 않고 HTTP API로만 연동해
  Cross-Manual 대조 소스로 활용한다. 같은 호스트 nginx가 `/`(핵심 앱 :24357)와
  `/manual-hub/*`(SPA+백엔드 :9180)를 하나의 진입점으로 라우팅하며, 80은 443(self-signed
  인증서)으로 강제 리다이렉트한다(모니터링 헬스체크 경로만 예외).
- 운영: `qa-verification.service`/`qa-manual-hub.service`/`nginx` 모두 systemd로 자동
  복구. `scripts/monitor_health.py`가 10분 간격으로 핵심 앱·nginx·manual_hub를 감시하고,
  `scripts/backup_data.py`가 매일 SQLite Online Backup+SHA-256 검증을 수행한다.
  `/operations/status`에서 동시 실행 제한·stale job·백업 상태를 확인할 수 있다.
- Gemini Key는 `secrets.txt`/`secrets.json`/`.env`/OS 환경변수 어디에 넣어도 인식되고
  `/config/status`로 확인·재적용한다. VXvue 사양서는 Windows 작업 스케줄러가 매주 월요일
  자동 동기화한다(크롤러 프로젝트 output만 읽음).
- `pytest -q` 248 passed(로컬, 실제 파일 E2E 포함 시 약간 더 오래 걸림 — 경로 미설정
  환경은 자동 skip).

## 5. 현재 남은 작업

`NEXT_STEPS.md`를 우선순위 순으로 참고한다. 이 파일에는 중복해서 유지하지 않는다.

## 6. systemd

핵심 앱·매뉴얼 허브·nginx 모두 systemd 유닛으로 등록·enable돼 있다(2026-09-02 전환
완료). 유닛 파일은 `deploy/systemd/qa-verification.service`(핵심 앱)와
`services/qa-manual-hub/deploy/`(매뉴얼 허브) 참고. 재기동은
`sudo systemctl restart qa-verification`(또는 `scripts/deploy.ps1 -Restart`).

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
- Gemini API Key를 코드, Git, README, 로그, Report에 기록하지 않는다. 서버 `sudo` 비밀번호는 `secrets.txt`의 `SERVER_SUDO_PASSWORD`로만 관리하고, 명령 인자·화면·로그·Report·Git에 값 자체를 출력하지 않는다 (자세한 내용은 `SECURITY.md` 참고).

## 8. 개발 및 검증 명령

```powershell
cd "C:\Users\2024980\Documents\자동화\qa-verification-management-system"
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\run.ps1
```

배포(파일 전송 + 의존성 설치, 필요하면 재기동까지):

```powershell
.\scripts\deploy.ps1            # 재기동 안 함
.\scripts\deploy.ps1 -Restart   # + sudo systemctl restart qa-verification + 헬스체크
```

배포 스크립트는 Git에서 제외된 `.deploy.env`의 Host/User/Target을 사용한다. Password 항목은 만들지 않는다.

SSH는 이미 키 인증으로 동작한다. `ssh -o BatchMode=yes ubuntu@10.13.0.222 'echo ok'`가 성공하므로 배포에 비밀번호가 필요 없다. `~/.ssh/id_ed25519`에 passphrase가 없어 ssh-agent도 필요 없다(Windows `ssh-agent` 서비스는 Stopped/Disabled 상태이며 그대로 둔다). SSH 비밀번호는 어떤 파일에도 저장하지 않으며, 보관이 필요하면 Windows 자격 증명 관리자나 password manager를 쓴다.

서버 확인:

```bash
cd /home/ubuntu/ai-regression-impact-analyzer
.venv/bin/python -m pytest -q
curl -fsS http://127.0.0.1:24357/health
ss -ltnp 'sport = :24357'
```

## 9. 비밀정보 파일

- `secrets.txt`: Gemini API Key 입력용 기본 파일. 메모장으로 열어 `GEMINI_API_KEY=` 뒤에 붙여넣는다. Git 제외
- `secrets.json`: 같은 목적의 JSON 대안. Git 제외
- `.env`: 기존 방식. 계속 동작하며 우선순위는 가장 낮다. Git 제외
- 값 우선순위: OS 환경변수 > `secrets.json` > `secrets.txt` > `.env` > 기본값
- `.deploy.env`: SSH Host/User/Target만 저장, Git 제외
- `real_fixtures.local.env`: `tests/test_manual_review_real_files_e2e.py` 전용, 사내망 실제
  VXvue 1.1.0 예시 파일의 기준 폴더 경로 2개만 저장. Git 제외(`.example`만 공개). 비밀정보는
  아니지만 사내 서버 IP·부서 폴더 체계를 공개 repo에 노출하지 않기 위해 `.deploy.env`와
  동일하게 취급
- `secrets.example.txt`, `secrets.example.json`, `.env.example`, `.deploy.env.example`: 값 없는 공개 예제
- `SERVER_SUDO_PASSWORD`: `secrets.txt`에 저장하는 서버(`10.13.0.222`, 사내망 전용) `sudo` 비밀번호. 2026-08-31 사용자 결정으로 도입. 앱은 이 키를 읽지 않으며(`secrets_loader.py` 미인식 키), 서버 운영 자동화(SSH/`sudo -S`)에서만 로컬로 사용한다. 값은 파일 → stdin으로만 전달하고 화면/로그에 출력하지 않는다.

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
