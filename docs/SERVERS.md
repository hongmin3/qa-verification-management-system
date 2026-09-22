# 이 프로젝트가 실제로 띄우는 서버 3개

이 프로젝트는 겉보기엔 "웹사이트 하나"지만, 실제 운영 서버(`10.13.0.222`)에서는 서로 다른
역할을 가진 **서버 프로그램 3개**가 함께 떠서 하나의 서비스처럼 동작한다. 이 문서는 그
3개가 각각 무엇을 하는지, 왜 나눠져 있는지, 문제가 생기면 어떤 명령으로 확인·조치하는지를
정리한다. README.md/docs/DEPLOYMENT.md가 "어떻게 설치하는지"에 초점을 맞춘다면, 이
문서는 "지금 떠 있는 각 서버가 정확히 뭘 하고 있는지"에 초점을 맞춘다.

## 큰 그림

```
                         사용자 브라우저
                               │
                               ▼
                     ① nginx (포트 80 → 443)
                     "교통 정리를 하는 관문"
                       /            \
                      /              \
                     ▼                ▼
        ② 핵심 앱 (포트 24357)   ③ QA Manual Hub 백엔드 (포트 9180)
        "QA 자동화 로직 전체"      "매뉴얼 문서 보관소"
        FastAPI + SQLite           FastAPI + PostgreSQL
```

세 서버는 **서로의 코드나 데이터베이스를 직접 들여다보지 않는다.** 핵심 앱이 QA Manual
Hub의 문서를 참고할 때도 코드를 import하는 게 아니라, 평범한 웹 API 호출(HTTP)로만
얘기한다. 이렇게 나눠 둔 이유는 두 시스템이 원래 다른 시점에 다른 목적으로 만들어졌고,
한쪽이 고장 나도 다른 쪽이 영향을 받지 않게 하기 위해서다.

---

## ① nginx — "이게 뭔가요?"

**nginx(엔진엑스)는 "요청을 받아서 어디로 보낼지 정하는" 범용 웹 서버 프로그램이다.**
아파치(Apache)와 같은 부류로, 특정 회사나 이 프로젝트만의 것이 아니라 전세계 웹사이트의
상당수가 쓰는 오픈소스 소프트웨어다. 이 프로젝트에서 nginx는 딱 세 가지 일만 한다.

1. **교통 정리(리버스 프록시)**: 사용자가 `http://10.13.0.222/`로 들어오면 "이건 핵심
   앱한테 보내야겠다"고 판단해 뒤에 있는 ②(포트 24357)로 요청을 전달한다. `/manual-hub/`로
   들어오면 ③(포트 9180)이나 그 화면 파일로 보낸다. 사용자는 포트 번호를 몰라도 되고,
   실제로는 이 서버들이 외부에서 직접 보이지 않는다(`127.0.0.1`에만 열려 있음) — nginx가
   유일한 정문이다.
2. **자물쇠 채우기(HTTPS)**: 브라우저와 서버 사이 통신을 암호화한다. 80번(암호화 없음)으로
   들어오면 443번(암호화)으로 강제로 돌려보낸다(리다이렉트). 지금은 사내망 전용이라
   공인 인증서 대신 self-signed(자체 서명) 인증서를 쓴다.
3. **정적 파일 서빙**: QA Manual Hub의 화면(React로 빌드된 정적 파일)을 직접 파일 시스템에서
   읽어 응답한다 — 이건 API 호출이 아니라 그냥 파일을 돌려주는 것이라 nginx가 직접 처리하는
   게 더 빠르다.

nginx는 **자기 스스로 "QA 로직"을 전혀 모른다.** 사양서를 분석하지도, DB를 읽지도 않는다.
그냥 "이 URL 패턴이면 이 포트로 보내라"는 규칙 목록(설정 파일)만 가지고 있다.

### 설정은 어디에 있나

- 저장소에 있는 원본: [`deploy/nginx/qa-platform.conf`](../deploy/nginx/qa-platform.conf)
- 서버에 실제로 적용된 위치: `/etc/nginx/sites-available/qa-platform.conf`
  (`/etc/nginx/sites-enabled/`에 심볼릭 링크)
- 인증서: `/etc/nginx/ssl/qa-platform/{fullchain.pem,privkey.pem}`

### 필수 명령

```bash
# 설정 파일을 고친 뒤, 문법이 맞는지 먼저 확인 (실서비스에 반영 전 필수)
sudo nginx -t

# 문법이 맞으면 무중단으로 새 설정 적용 (기존 연결을 끊지 않음)
sudo systemctl reload nginx

# 완전히 재시작해야 할 때 (reload로 안 될 때만)
sudo systemctl restart nginx

# 지금 살아 있는지, 언제부터 떠 있었는지
systemctl status nginx --no-pager

# 실시간 접속/에러 로그 보기
tail -f /var/log/nginx/qa-platform.access.log
tail -f /var/log/nginx/qa-platform.error.log

# 겉에서 정상 응답하는지 빠르게 확인
curl -k https://127.0.0.1/health                    # 핵심 앱
curl -k https://127.0.0.1/manual-hub/api/health      # QA Manual Hub
```

`nginx -t` 없이 바로 `reload`/`restart`를 하면, 설정에 오타가 있을 때 서비스 전체가 죽는다
— 반드시 먼저 `-t`로 검증한다.

---

## ② 핵심 앱 — QA 자동화 로직 전체

이 저장소의 `app/` 폴더가 곧 이 서버다. Python `FastAPI` 프레임워크로 만들었고, 데이터는
파일 하나짜리 데이터베이스인 SQLite에 저장한다(별도 DB 서버가 필요 없음).

**하는 일**: 이 프로젝트의 핵심 기능 전부.
- Regression 영향 분석(`/impact-analyzer`) — SW 변경사항을 분석해 다시 검증해야 할 Test
  Case를 추천
- 매뉴얼 개정 검증(`/manual-review`) — Word/PDF 매뉴얼의 변경사항이 사양서와 맞는지 AI로
  1차 검토
- Knowledge(`/knowledge`) — 사양서·TC 파일 등록/관리
- 비용 대시보드(`/cost-dashboard`) — Gemini API 사용량·비용 확인

**실행 방식**: `qa-verification.service`라는 이름의 systemd 유닛으로 등록돼 있다.
systemd는 Linux가 기본으로 제공하는 "프로세스 자동 관리자"다 — 서버가 재부팅되거나
프로세스가 죽으면 자동으로 다시 띄워준다(수동으로 터미널에 명령을 계속 입력하고 있을
필요가 없다는 뜻).

```
ExecStart = .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 24357
```

`uvicorn`은 FastAPI 코드를 실제로 실행시켜 웹 요청을 받을 수 있게 해주는 프로그램이다
(파이썬으로 웹서버를 돌리려면 이런 실행기가 하나 필요하다).

### 필수 명령

```powershell
# 로컬(내 PC)에서 실행
.\scripts\run.ps1                     # http://localhost:24357

# 로컬 테스트 (Gemini Mock 사용, 비용 없음)
.\.venv\Scripts\python.exe -m pytest -q

# 서버로 배포 (파일 전송 + 의존성 설치, 재기동은 별도)
.\scripts\deploy.ps1
.\scripts\deploy.ps1 -Restart         # 위 작업 + 서버 재기동 + 헬스체크까지 한 번에
```

```bash
# 서버에서 직접 다룰 때
sudo systemctl restart qa-verification    # 코드를 새로 배포한 뒤 재기동
systemctl status qa-verification --no-pager
journalctl -u qa-verification -n 50 --no-pager   # 최근 로그 50줄
curl -fsS http://127.0.0.1:24357/health           # {"status":"ok"} 가 나와야 정상
```

---

## ③ QA Manual Hub 백엔드 — 매뉴얼 문서 보관소

`services/qa-manual-hub/`에 있는 **완전히 별도의 프로젝트**다. 제품 매뉴얼·기술문서를
Git처럼 "리비전(개정 버전) 이력을 지우지 않고" 보관하는 문서 관리 서비스다. 이것도
FastAPI로 만들었지만, 데이터는 SQLite가 아니라 PostgreSQL(정식 관계형 데이터베이스)에
저장한다 — 사용자 계정·권한·업로드 파일 이력처럼 더 복잡하고 오래 보관해야 하는 데이터를
다루기 때문이다.

**핵심 앱과의 관계**: 매뉴얼 개정 검증 기능이 "이 제품의 다른 매뉴얼들과 비교해봐야
하는데, 그 매뉴얼들이 어디 있지?"라고 물을 때 참고하는 대상 중 하나다. 하지만 코드를
공유하지도, 같은 DB를 보지도 않는다 — 핵심 앱이 QA Manual Hub의 HTTP API를 그냥 "웹
브라우저처럼" 호출해서 필요한 정보만 받아온다. 그래서 QA Manual Hub가 죽어도 핵심 앱의
나머지 기능(Regression 분석 등)은 멈추지 않는다.

**실행 방식**: `qa-manual-hub.service` systemd 유닛, `uvicorn`으로 실행.

```
ExecStart = venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 9180 --workers 2 ...
```

`--workers 2`는 요청을 동시에 처리할 프로세스를 2개 띄운다는 뜻 — 핵심 앱보다 더 많은
동시 사용자를 상정한 설정이다.

### 필수 명령

```bash
# 서버에서 직접 다룰 때
sudo systemctl restart qa-manual-hub
systemctl status qa-manual-hub --no-pager
journalctl -u qa-manual-hub -n 50 --no-pager
curl -s http://127.0.0.1:9180/api/health

# 관리용 CLI (계정 생성, 데이터 정합성 검사 등)
<APP_ROOT>/scripts/qamh check-storage       # DB 기록과 실제 파일이 일치하는지 검사
<APP_ROOT>/scripts/qamh bootstrap-admin     # 최초 관리자 계정 생성(1회)

# 백업/복구 (서비스 자체 스크립트, cron이 매일 자동 실행)
sudo -u ubuntu /opt/qa-manual-hub/scripts/backup.sh
sudo <APP_ROOT>/scripts/restore.sh <백업 폴더 경로>
```

```bash
# 로컬 개발 (실제 PostgreSQL 필요 — SQLite로 대체 불가)
cd services/qa-manual-hub/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head                        # DB 스키마 최신화
uvicorn app.main:app --reload --port 9180

cd services/qa-manual-hub/frontend
npm install
npm run dev                                 # http://localhost:5173

# 테스트 (테스트용 DB 필요)
createdb qa_manual_hub_test
cd services/qa-manual-hub/backend
export TEST_DATABASE_URL="postgresql+psycopg://user:pass@127.0.0.1:5432/qa_manual_hub_test"
pytest tests -q
```

상세한 배포·백업·트러블슈팅 절차는 [`services/qa-manual-hub/README.md`](../services/qa-manual-hub/README.md)에 훨씬 자세히 정리돼 있다 — 이 문서는 "무엇을 하는 서버인지" 큰 그림만 담는다.

---

## 한눈에 보는 명령 요약

| 하고 싶은 일 | 명령 |
|---|---|
| 핵심 앱 재기동 | `sudo systemctl restart qa-verification` |
| Manual Hub 재기동 | `sudo systemctl restart qa-manual-hub` |
| nginx 설정 반영(무중단) | `sudo nginx -t && sudo systemctl reload nginx` |
| 핵심 앱 살아있는지 확인 | `curl -fsS http://127.0.0.1:24357/health` |
| Manual Hub 살아있는지 확인 | `curl -s http://127.0.0.1:9180/api/health` |
| 셋 다 밖에서 정상인지 확인 | `curl -k https://127.0.0.1/health`, `.../manual-hub/api/health` |
| 핵심 앱 최근 로그 | `journalctl -u qa-verification -n 50 --no-pager` |
| Manual Hub 최근 로그 | `journalctl -u qa-manual-hub -n 50 --no-pager` |
| nginx 접속/에러 로그 | `tail -f /var/log/nginx/qa-platform.{access,error}.log` |
| 세 서버 모두 감시 | `scripts/monitor_health.py` (cron이 10분 간격 자동 실행) |
