# 운영 환경 로컬 메모 (템플릿)

이 파일을 `OPERATIONS_LOCAL.md`로 복사해 실제 값을 채운다. `docs/local/`은 `.gitignore`
대상이므로 실제 파일은 저장소에 올라가지 않는다 (이 예제 파일만 공개된다).

**비밀번호·API Key 값 자체는 이 파일에도 적지 않는다.** 값은 `secrets.txt` 같은 gitignore
대상 파일에만 두고, 여기에는 "어디에 있는지"만 적는다.

## 운영 서버

| 항목 | 값 |
|---|---|
| 호스트 | `<서버 IP 또는 호스트명>` |
| SSH 계정 | `<user>` |
| 핵심 앱 배포 경로 | `<경로>` |
| 핵심 앱 포트 | `24357` |
| Manual Hub APP_ROOT | `/opt/qa-manual-hub` |
| Manual Hub DATA_ROOT | `/srv/qa-manual-hub` |
| Manual Hub 백엔드 포트 | `9180` |
| nginx 사이트 | `<사이트 파일명>` |

## 같은 호스트의 다른 서비스 (건드리지 않을 것)

- `<경로>` — `<서비스 설명>`

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
cd <배포 경로> && nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 24357 \
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
