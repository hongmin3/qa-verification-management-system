# 비밀정보 및 운영 서버 안전 규칙

이 문서는 저장소에 포함된다(2026-09-30 부터 비공개). **운영 서버 주소, 계정, 경로 같은 사내 고유 정보는 여기에
적지 않고** 비공개 저장소의 `docs/local/OPERATIONS_LOCAL.md`에 둔다
(템플릿: `docs/local/OPERATIONS_LOCAL.example.md`).

## API Key

- Gemini API Key는 프로젝트 루트의 `secrets.txt`, `secrets.json`, `.env` 중 한 곳에만 저장하며
  Git, 로그, 보고서에 포함하지 않는다.
- `secrets.txt`, `secrets.json`, `.env`는 `.gitignore` 대상이다. `secrets.example.*`,
  `.env.example`만 공개한다.
- `/config/status`와 `/config/reload`는 Key 설정 여부, 길이, 출처 이름만 반환하며 Key 문자열은
  반환하지 않는다.

## 자격증명 취급

- 운영 서버 접속은 SSH key 인증을 사용한다.
- `sudo` 비밀번호처럼 서버 운영에 필요한 자격증명을 파일로 관리할 경우, `.gitignore` 대상
  파일(`secrets.txt` 등)에만 두고 저장소·문서·로그·대화 응답 어디에도 값을 남기지 않는다.
- 그런 값을 사용하는 자동화는 값을 화면·명령 인자·로그·커밋에 그대로 출력하지 않고, 파일에서
  SSH / `sudo -S`의 표준입력으로 곧바로 전달하는 방식만 사용한다.
- 앱이 인식하는 비밀정보 키는 `app/core/secrets_loader.py`에 정의된 것뿐이다. 그 외 키
  (운영 자동화용 값 등)는 FastAPI 앱·`/config/status`·Report 어디에도 노출되지 않는다.

## 운영 서버에서 지키는 범위

- 배포 대상 디렉터리 밖의 파일은 열람·수정하지 않는다. 같은 호스트에 있는 다른 서비스의
  디렉터리와 마운트 지점은 건드리지 않는다.
- 이 프로젝트가 설치하지 않은 systemd 유닛, nginx 사이트, PostgreSQL 설정은 변경하지 않는다.
  이 프로젝트의 유닛·사이트만 추가하거나 갱신한다.
- 방화벽(`ufw`)은 이 프로젝트가 사용하는 포트를 여는 등 이 프로젝트 범위 내의 변경만 수행한다.
- 운영 서버는 사내망에서만 접근 가능한 것을 전제로 한다. 외부 노출이 필요해지면 HTTPS와
  인증 정책을 먼저 결정한 뒤 진행한다.

## 하위 서비스

`services/qa-manual-hub`는 자체 인증·세션·감사 로그를 가진다. 해당 서비스의 보안 설계와
`.env` 취급은 [services/qa-manual-hub/README.md](services/qa-manual-hub/README.md)의 보안
섹션을 따른다. 통합 배포 시 `SESSION_COOKIE_PATH`로 세션 쿠키 범위를 `/manual-hub/`로 좁힌다.
