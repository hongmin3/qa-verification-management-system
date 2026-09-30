# 운영 환경 로컬 메모

이 파일은 비공개 저장소(2026-09-30 전환)에 함께 올라간다. 저장소를 다시 공개로 돌리기 전에
이 폴더를 먼저 빼야 한다. `SECURITY.md` 에서 옮겨 온 사내 고유 정보를 여기에 둔다.

**비밀번호·API Key 값 자체는 이 파일에도 적지 않는다.** 값은 `secrets.txt` 같은 gitignore
대상 파일에만 두고, 여기에는 "어디에 있는지"만 적는다.

## 운영 서버

| 항목 | 값 |
|---|---|
| 호스트 | `10.13.0.222` (사내망 전용) |
| SSH 계정 | `ubuntu` (SSH key 인증) |
| 핵심 앱 배포 경로 | `/home/ubuntu/ai-regression-impact-analyzer` (구 프로젝트명 유지 — 운영 리스크상 의도적) |
| 핵심 앱 포트 | `24357` |
| Manual Hub APP_ROOT | `/opt/qa-manual-hub` |
| Manual Hub DATA_ROOT | `/srv/qa-manual-hub` |
| Manual Hub 백엔드 포트 | `9180` |
| nginx 사이트 | `/etc/nginx/sites-enabled/qa-platform.conf` |

## 같은 호스트의 다른 서비스 (건드리지 않을 것)

- `/home/ubuntu/jjhhub/` — 기존 서비스. 열람·수정하지 않는다.
- `/mnt/vhdmaster`, `/mnt/vhdmaste` — 기존 마운트. 건드리지 않는다.
- `/opt/ai-remote-hub` — 다른 자동화 서비스.

## 자격증명 위치

| 용도 | 저장 위치 | 비고 |
|---|---|---|
| Gemini API Key | 프로젝트 루트 `secrets.txt` 의 `GEMINI_API_KEY` | Git 제외 |
| 서버 sudo 비밀번호 | 프로젝트 루트 `secrets.txt` 의 `SERVER_SUDO_PASSWORD` | Git 제외. 앱이 인식하지 않는 키이므로 화면·Report에 노출되지 않음 |
| Manual Hub DB 비밀번호 | 서버 `<APP_ROOT>/.env` (권한 600) | `install.sh`가 무작위 생성 |

## 재기동 절차

```bash
# 핵심 앱
OLD_PID=$(ss -ltnp 'sport = :24357' | grep -oP 'pid=\K[0-9]+')
kill "$OLD_PID"
cd /home/ubuntu/ai-regression-impact-analyzer && nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 24357 \
  > output/logs/uvicorn.out 2>&1 & disown

# Manual Hub
sudo systemctl restart qa-manual-hub
sudo systemctl reload nginx
```

## 헬스체크

```bash
curl -fsS http://127.0.0.1/health                  # 핵심 앱 (nginx 경유)
curl -fsS http://127.0.0.1:24357/health            # 핵심 앱 (직접)
curl -fsS http://127.0.0.1/manual-hub/api/health   # Manual Hub
```

---

## 통합 배포 상태 (2026-09-02 적용·검증 완료)

nginx 사이트를 단독용 `qa-manual-hub.conf` → 통합용 `qa-platform.conf` 로 교체했다.
**이 호스트의 `/` 는 이제 매뉴얼 서버가 아니라 QA 자동화 홈이다.**

| 주소 | 결과 |
|---|---|
| `http://10.13.0.222/` | QA 자동화 홈 (카드 3개) |
| `http://10.13.0.222/manual-hub/` | 매뉴얼 서버 |
| `http://10.13.0.222/documents` 등 구 주소 | `/manual-hub/documents` 로 301 |
| `http://10.13.0.222/api/...` | 매뉴얼 서버 백엔드 (구 SPA 번들 호환) |
| `http://10.13.0.222:24357/manual-hub/` | `http://10.13.0.222/manual-hub/` 로 307 |

적용한 변경:

- `/opt/qa-manual-hub/app/frontend` — `build:platform` 산출물 (asset base `/manual-hub/`)
- `/opt/qa-manual-hub/app/backend` — `session_cookie_path` 지원 코드
- `/opt/qa-manual-hub/.env` — `SESSION_COOKIE_PATH=/manual-hub/` 추가
- `/etc/nginx/sites-enabled/qa-platform.conf` (기존 `qa-manual-hub.conf` 심볼릭 링크 해제)
- `/home/ubuntu/ai-regression-impact-analyzer` — `app/`, `config.yaml` 갱신 후 재기동

### 되돌리는 방법

```bash
TS=$(cat ~/.last-integration-backup)          # 예: 20260902-001316
sudo ln -sf /etc/nginx/sites-available/qa-manual-hub.conf /etc/nginx/sites-enabled/qa-manual-hub.conf
sudo rm -f /etc/nginx/sites-enabled/qa-platform.conf
rm -rf /opt/qa-manual-hub/app/frontend/*
cp -r ~/integration-backup-$TS/frontend-old/* /opt/qa-manual-hub/app/frontend/
sudo sed -i '/^SESSION_COOKIE_PATH=/d' /opt/qa-manual-hub/.env
sudo systemctl restart qa-manual-hub && sudo nginx -t && sudo systemctl reload nginx
```

### 재배포 시 주의

매뉴얼 서버를 다시 배포할 때는 **반드시** `BUILD_MODE=platform` 을 붙인다. 빠뜨리면 단독용
base `/` 로 빌드돼 화면이 빈 채로 뜬다 (deploy.sh 가 전송 전에 검사해서 중단시킨다).

```bash
BUILD_MODE=platform ./services/qa-manual-hub/deploy/scripts/deploy.sh ubuntu@10.13.0.222
```

`install.sh` 를 다시 돌릴 일이 생기면 `SKIP_NGINX=1` 을 붙인다. 안 붙이면 단독용 nginx
사이트가 다시 설치돼 `/` 가 매뉴얼 서버로 돌아간다.
