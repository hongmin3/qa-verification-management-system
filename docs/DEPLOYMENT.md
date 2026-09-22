# 배포 가이드 — 직접 서버에 구축하기

README는 포트폴리오용으로 핵심만 담고 있어서, 실제로 이 프로젝트를 로컬 또는 서버에
처음부터 구축하려는 사람을 위한 상세 절차를 여기에 정리한다.

## 1. 사전 준비물

- Python 3.11 이상 (서버는 3.12로 검증됨)
- Gemini API Key (https://aistudio.google.com 에서 발급, 무료 한도 존재)
- (서버에 올릴 경우) SSH로 접속 가능한 Linux 서버 — 이 프로젝트는 Ubuntu에서 검증됨
- Git

## 2. 로컬 설치

```powershell
git clone https://github.com/hongmin3/qa-verification-management-system.git
cd qa-verification-management-system
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item secrets.example.txt secrets.txt -Force
notepad secrets.txt   # GEMINI_API_KEY=여기에_키_붙여넣기
```

macOS/Linux는 `.venv/bin/python`을 쓰고, `secrets.example.txt`를 `secrets.txt`로 복사한 뒤 직접
편집하면 된다.

## 3. 로컬 실행 및 확인

```powershell
.\scripts\run.ps1        # http://localhost:24357
```

```bash
./scripts/run.sh         # Linux/macOS
```

브라우저에서 `http://localhost:24357/health`가 `{"status":"ok"}`를 반환하는지, `/impact-analyzer/guide`와 `/manual-review/guide`에서
사용법이 보이는지 확인한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q   # 59개 테스트, Gemini Mock 사용이라 비용 없음
```

## 4. 서버에 배포하기 (예시: Ubuntu + SSH)

### 4-1. 최초 1회 서버 준비

```bash
ssh your-user@your-server
mkdir -p /path/to/qa-verification-management-system
cd /path/to/qa-verification-management-system
python3 -m venv .venv
```

이 프로젝트는 서버 배포 폴더를 Git 저장소로 두지 않는다 — 로컬에서 검증한 파일만 옮긴다
(레포를 그대로 git clone 해도 무방하지만, 이 프로젝트의 운영 방식은 파일 복사 방식이다).

### 4-2. 배포 스크립트

`.deploy.env.example`을 `.deploy.env`로 복사하고 본인 서버 정보를 채운다 (Git에서 제외됨,
비밀번호는 넣지 않는다 — SSH는 키 인증만 사용):

```text
DEPLOY_SSH_HOST=your-server-ip
DEPLOY_SSH_USER=your-user
DEPLOY_TARGET_DIRECTORY=/path/to/qa-verification-management-system
```

```powershell
.\scripts\deploy.ps1
```

이 스크립트는 로컬 `pytest`를 먼저 통과시키고, `app/`, `scripts/`, `tests/`, `config/`, `docs/`,
`deploy/`, `requirements.txt`, `config.yaml`, 예제 파일들만 서버로 복사한 뒤 서버에서
`.venv/bin/pip install -r requirements.txt`까지 자동으로 실행한다(2026-09-02부터). `secrets.txt`/
`.env`/`data/` 같은 개인 설정과 운영 데이터는 복사하지 않는다 (의도적 — 서버의 실제 데이터를
덮어쓰지 않기 위함).

### 4-3. 서비스 재기동

파일 전송과 의존성 설치까지는 `deploy.ps1`이 항상 수행한다. **재기동은 기본적으로 하지
않는다** — 코드만 반영해 두고 재기동 시점을 사람이 고르고 싶을 때가 있기 때문이다.

```powershell
.\scripts\deploy.ps1            # 파일 전송 + 의존성 설치만. 재기동 안 함
.\scripts\deploy.ps1 -Restart   # 위 작업 + sudo systemctl restart qa-verification + 헬스체크
```

`-Restart`는 로컬 `secrets.txt`의 `SERVER_SUDO_PASSWORD`를 읽어 SSH로 `sudo -S`에 전달한다(화면·로그에
값을 출력하지 않음). 이 키가 없으면 아래처럼 서버에서 직접 재기동한다:

```bash
ssh your-user@your-server
sudo systemctl restart qa-verification
curl -fsS http://127.0.0.1:24357/health
```

### 4-4. 비밀정보 입력 (서버에서 1회)

```bash
cp secrets.example.txt secrets.txt
nano secrets.txt   # GEMINI_API_KEY=...
```

`secrets.txt`/`secrets.json`/`.env`는 Git에도, 배포 스크립트에도 포함되지 않으므로 서버에서
직접 만들어야 한다. 값 우선순위는 OS 환경변수 > `secrets.json` > `secrets.txt` > `.env`이다.

### 4-5. 실행 (systemd 권장)

서비스로 등록해 두면 서버가 재부팅돼도 자동으로 다시 뜬다. 절차는 §5에 있다. 등록 후에는
재배포마다 이 한 줄이면 된다.

```bash
sudo systemctl restart qa-verification
curl -fsS http://127.0.0.1:24357/health
```

**systemd 없이 임시로 띄우는 경우** (개발 서버 등). 이 방식은 재부팅에서 살아남지 않는다.

```bash
cd /path/to/qa-verification-management-system
nohup .venv/bin/python -m app.serve > output/logs/uvicorn.out 2>&1 &
disown
curl -fsS http://127.0.0.1:24357/health
```

이때 재배포 후 코드 변경을 반영하려면 같은 포트를 쓰는 기존 프로세스를 종료하고 위 명령으로
다시 띄운다:

```bash
OLD_PID=$(ss -ltnp 'sport = :24357' | grep -oP 'pid=\K[0-9]+')
kill "$OLD_PID"
# 프로세스 종료 확인 후
nohup .venv/bin/python -m app.serve > output/logs/uvicorn.out 2>&1 &
disown
```

### 4-6. 방화벽 / 외부 접속

서버가 UFW 등으로 포트별 허용 목록을 쓰는 경우, 사용할 포트를 명시적으로 열어야 한다:

```bash
sudo ufw allow 24357/tcp
sudo ufw status | grep 24357
```

사내망 전용으로 운영한다면 그 서버가 사내망에서만 라우팅되는지 네트워크 담당자에게 확인한다.

## 5. systemd로 상시 서비스화

`nohup` 프로세스는 서버가 재부팅되면 다시 뜨지 않는다. 하위 서비스인 매뉴얼 서버는 이미
systemd 로 관리되므로, 핵심 앱도 같은 방식으로 맞춘다.

유닛 템플릿은 저장소에 있다 — [`deploy/systemd/qa-verification.service`](../deploy/systemd/qa-verification.service).
플레이스홀더 2개(`__APP_ROOT__`, `__SERVICE_USER__`)만 실제 값으로 바꿔 설치한다. 포트는 유닛에 넣지 않는다 — `config.yaml` 의 `app.port` 가 단일 원본이다 (REQ-DEPLOY-001).

```bash
APP_ROOT=/path/to/qa-verification-management-system
sed -e "s|__APP_ROOT__|$APP_ROOT|g"     -e "s|__SERVICE_USER__|$(whoami)|g"     "$APP_ROOT/deploy/systemd/qa-verification.service"   | sudo tee /etc/systemd/system/qa-verification.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable qa-verification
```

이미 `nohup` 으로 띄워 둔 프로세스가 있으면 **먼저 종료해야** 포트가 겹치지 않는다.

```bash
OLD_PID=$(ss -ltnp 'sport = :24357' | grep -oP 'pid=\K[0-9]+' | head -1)
[ -n "$OLD_PID" ] && kill "$OLD_PID"
sudo systemctl start qa-verification
systemctl is-active qa-verification && systemctl is-enabled qa-verification
curl -fsS http://127.0.0.1:24357/health
```

유닛에서 눈여겨볼 점:

- `StandardOutput=append:` 로 journald 와 별개로 기존 로그 파일(`output/logs/uvicorn.out`)에도
  계속 남긴다. 운영 중 `tail -f` 하던 습관이 그대로 유지된다.
- `Restart=on-failure` — 앱이 죽으면 5초 뒤 자동 재시작한다.
- 앱은 시작할 때 재시작에 끊긴 `RUNNING` 분석을 정리하고 `QUEUED` 를 이어받으므로
  (`app/main.py` 의 `lifespan`), 강제 종료돼도 분석 상태가 깨지지 않는다.

**주의**: 다른 서비스와 같은 서버를 공유한다면, 기존 systemd 유닛·nginx·방화벽·DB 설정을
건드리지 않고 이 유닛만 새로 추가해야 한다. 운영 서버에 처음 등록할 때는 반드시 서버
관리자(또는 본인이 관리자라면 스스로)에게 영향 범위를 확인한 뒤 진행한다.

## 6. VXvue 사양서 자동 동기화 활성화 (선택)

`docs/AUTOMATION.md`에 상세 설명이 있다. 요약:

1. `config/products/vxvue.yaml`의 `specification.crawler_output_dir`를 실제 크롤러 output 경로로
   맞춘다.
2. 크롤러가 있는 Windows PC에서 `scripts/sync_vxvue_spec.py --target-url http://<서버>:24357`을
   Windows 작업 스케줄러에 매일 등록한다 (서버 자체는 크롤러 폴더에 접근할 수 없어 이 스크립트를
   서버에서 실행할 수 없다).
3. 앱 내부 스케줄러(`app/core/scheduler.py`)는 신규 systemd 없이 이미 함께 뜨며, `/knowledge`
   화면의 "지금 동기화" 버튼으로 수동 실행도 가능하다.

## 7. 업데이트 배포 체크리스트

1. 로컬에서 코드 수정 후 `pytest` 통과 확인
2. `.\scripts\deploy.ps1 -Restart` — 파일 전송, `pip install`, 재기동, 헬스체크를 한 번에 수행한다
   (재기동을 미루고 싶으면 `-Restart` 없이 실행 후 §4-3 마지막 명령으로 나중에 재기동)
3. 서버에서 `pytest` 재확인
4. 주요 페이지(`/`, `/knowledge`, `/analyses`) 확인, **다른 서비스나 포트는 건드리지 않는다**

## 8. 하위 서비스(QA Manual Hub)를 같은 서버 `/manual-hub`에 붙이기

핵심 앱과 [QA Manual Hub](../services/qa-manual-hub/README.md)는 **프로세스도 DB도 공유하지
않는 별도 배포 단위**다. 홈 화면의 "매뉴얼 서버" 카드가 동작하려면 두 서비스가 같은 origin에
있어야 하고, 그 역할을 nginx가 맡는다.

```text
브라우저 → nginx :80 ┬ /             → 핵심 앱          127.0.0.1:24357
                     ├ /manual-hub/  → Manual Hub SPA   (정적 파일)
                     └ /manual-hub/api → Manual Hub 백엔드 127.0.0.1:9180
```

### 8-1. Manual Hub 설치 (nginx는 건너뛴다)

```bash
sudo SKIP_NGINX=1 ./services/qa-manual-hub/deploy/scripts/install.sh
```

`install.sh`는 멱등이며 추가 작업만 한다. 이미 있는 DB·role은 초기화하지 않는다. 상세 옵션은
[Manual Hub README](../services/qa-manual-hub/README.md)를 참고한다.

`SKIP_NGINX=1`을 주는 이유는, 이 스크립트가 설치하는 사이트 설정
(`deploy/nginx/qa-manual-hub.conf`)이 **단독 배포용**(포트 80 전체를 Manual Hub가 차지)이기
때문이다. 통합 배포에서는 아래 §8-3의 설정을 대신 쓴다.

### 8-2. 프론트엔드를 서브패스로 빌드

```bash
# 이후 재배포는 이 한 줄로 끝난다 — 빌드 모드까지 함께 지정한다
BUILD_MODE=platform ./services/qa-manual-hub/deploy/scripts/deploy.sh user@server
```

`BUILD_MODE=platform`을 빠뜨리면 단독용(base `/`)으로 빌드된 SPA가 올라가 화면이 빈 채로
뜬다. `deploy.sh`는 전송 전에 산출물의 asset 경로가 base와 맞는지 확인하고, 다르면 중단한다.
서버가 어느 쪽인지 모르면 `ls /etc/nginx/sites-enabled/`에 `qa-platform.conf`가 있는지 본다.

수동으로 빌드만 하려면:

```bash
cd services/qa-manual-hub/frontend
npm ci
npm run build:platform      # frontend/.env.platform 의 VITE_BASE_PATH=/manual-hub/
```

`npm run build`(단독)와 `npm run build:platform`(서브패스)의 차이는 base path 하나뿐이다. 이
값이 asset URL·react-router basename·API prefix를 모두 결정하므로 다른 곳을 고칠 필요가 없다.
빌드 산출물을 서버의 `<APP_ROOT>/app/frontend`로 보낸다 (`deploy/scripts/deploy.sh`가 수행).

Manual Hub의 `.env`에 다음을 추가한다 — 세션 쿠키가 핵심 앱 요청까지 따라가지 않도록 범위를
좁힌다.

```text
SESSION_COOKIE_PATH=/manual-hub/
```

### 8-3. nginx 설정

```bash
sudo cp deploy/nginx/qa-platform.conf /etc/nginx/sites-available/qa-platform
sudo ln -sf /etc/nginx/sites-available/qa-platform /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**서버에 이미 다른 서비스의 nginx 사이트가 있다면 그 파일을 수정하지 말고**, `qa-platform.conf`
의 `location` 블록만 해당 server 블록에 옮겨 붙인다. 기존 systemd 유닛·방화벽·DB 설정은
건드리지 않는다.

### 8-4. 확인

```bash
curl -fsS http://127.0.0.1/health                    # 핵심 앱
curl -fsS http://127.0.0.1/manual-hub/api/health     # Manual Hub 백엔드
curl -fsS http://127.0.0.1/manual-hub/ | head -5     # SPA 셸
```

브라우저에서 홈(`/`)에 "매뉴얼 서버" 카드가 보이고, 클릭하면 Manual Hub가 뜨고, 사이드바의
"← QA 자동화 홈"으로 돌아오면 정상이다.

### 8-5. 통합하지 않는 선택지

nginx를 두고 싶지 않다면 Manual Hub를 단독으로(자기 호스트·포트) 배포하고, `config.yaml`의
`services.manual_hub.url`에 절대 URL을 넣으면 된다. 홈 카드는 그 주소를 그대로 연다.

```yaml
services:
  manual_hub:
    url: "http://manual.example.internal"
```

값을 빈 문자열로 두면 카드 자체가 표시되지 않는다.
