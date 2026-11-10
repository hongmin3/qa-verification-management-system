# 클린 PC에서 VXvue·Bellalun 작업 이어가기

## 자료 위치와 범위

| 자료 | 보관 위치 | 새 PC에서 할 일 |
|---|---|---|
| 서버 코드·공용 연결 Skill·시험·이 설치 절차 | 이 Git 저장소의 master | 권한 있는 계정으로 clone하고 지정한 커밋 확인 |
| 로컬 Bellalun Skill·별칭·연결 코드·두 QA 지침 | QA 서버 `data/qa-client-handoff/`의 버전별 ZIP·manifest | SSH/SCP로 받고 SHA256 대조 |
| 로컬 Bellalun 지식 원본 15개 | 같은 폴더의 `bellalun-knowledge.zip`·내부 source-manifest | 빈 지식 폴더에 풀고 모든 파일 해시 대조 |
| 사양·매뉴얼·TC 원문 | QA 서버 `data/product_knowledge/<제품>/original/`와 manifest | 서버 도구로 조회하거나 권한 범위에서 필요한 제품 폴더를 내려받음 |
| 검증 기록·지침 변경 전 백업 | 같은 인계 자료 폴더의 `verification-and-resume.zip`·`qa-source-backups.zip` | 완료 판정과 남은 항목을 확인 |
| Redmine·Polarion·PC 등록 인증 | 새 PC에서 별도 등록 | 기존 PC의 키·쿠키·설정 파일을 복사하지 않음 |

제품 원문·QA 지침·개인 경로·자격 증명은 Git에 넣지 않는다. ZIP에는 config·키·캐시·백업을 넣지 않는다. 서버의 `current.json`에서 최종 ZIP 이름·SHA256·코드 커밋·검증 기록 위치를 확인한다.

## 공용 서버만 사용하기

1. 사내망에서 QA 서버의 `/vxvue-desktop` 또는 `/static/skills/bellalun/index.html`을 연다.
2. 새 PC용 연결 파일과 최신 Skill ZIP을 받아 안내에 따라 등록한다. 이전 PC의 등록 키를 재사용하지 않는다.
3. 앱 연결을 다시 불러오고 VXvue는 `get_status`, Bellalun은 `bellalun_get_status`를 호출한다.
4. 받은 판으로 `get_workflow(section="core")` 또는 `bellalun_get_workflow(section="core")`와 필요한 작업 유형을 읽는다. `mail`도 두 제품에서 사용할 수 있다.
5. 실제 검색·원문 한 건을 조회한다. 설치 성공과 자료 조회 성공을 구분한다.

이 경로에는 로컬 제품 지식 폴더가 필요 없다. 도구 정의가 오래 캐시됐으면 연결을 다시 불러온다. 지침은 새 상태 조회에서 읽고 확장 프로그램의 판과 구분한다.

## 로컬 Skill 유지보수까지 이어가기

Git·Node.js·Python 3.12 이상과 서버 SSH 접근 권한을 준비한다. 아래 `<...>`는 새 PC의 실제 값으로 바꾼다. SSH 개인키와 서비스 비밀번호는 묶음에 포함되지 않는다.

```powershell
git clone https://github.com/hongmin3/qa-verification-management-system.git qa-platform
Set-Location qa-platform
git checkout master
git pull --ff-only
scp "<SSH_USER>@<QA_HOST>:<서버 앱 경로>/data/qa-client-handoff/current.json" .
```

`current.json`의 ZIP·SHA256을 확인하고 그 파일을 같은 폴더로 받는다. Bellalun 로컬 원본은 `bellalun-knowledge.zip`에 원래 파일명으로 보관한다. 검색용 서버 manifest에서 제외된 중복 형식의 사양서 PDF도 이 묶음에 포함된다. 빈 지식 폴더에 풀고 내부 `source-manifest.json`의 해시를 대조한다. 그 밖의 제품 원문은 서버의 해당 제품 original 폴더에서 받는다. 같은 이름의 자료를 덮어쓰기 전에 비교한다.

```powershell
Get-FileHash -Algorithm SHA256 <받은 ZIP>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-qa-client.txt
.\.venv\Scripts\python.exe -X utf8 scripts/install_qa_client.py --archive <받은 ZIP> --knowledge-root <Bellalun 원문 폴더>
```

설치기는 ZIP 목록과 모든 파일 해시를 검사하고 새 홈·Python·지식 폴더 경로를 설정한다. 기존 파일이 다르면 저장 전에 중단한다. 사용자 설정과 API 키는 생성·변경하지 않는다. 연결 샘플은 `~/.local/share/qa-client-handoff/mcp-connection.json`에 생성된다.

Codex·Claude Desktop의 기존 연결 설정과 비교해 `bellalun-ai` 항목만 등록한다. Claude Code에도 같은 원본을 쓰려면 기존 경로를 먼저 확인한 뒤 두 Skill 이름을 `~/.codex/skills/`의 동일 폴더에 junction으로 연결한다. 기존 일반 폴더를 삭제하거나 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\python.exe -X utf8 "$env:USERPROFILE\.codex\skills\bellalun-local-knowledge\scripts\workflow.py" --check
.\.venv\Scripts\python.exe -X utf8 "$env:USERPROFILE\.codex\skills\bellalun-local-knowledge\scripts\workflow.py" --section mail
.\.venv\Scripts\python.exe -m pytest -q tests/test_qa_workflow_sections.py tests/test_qa_context_filters.py tests/test_qa_client_handoff.py
node .project-check/project-readiness.js .
```

로컬 Redmine 조회가 필요하면 본인 계정의 읽기 권한으로 `config/redmine.json`을 새로 설정한다. 기존 서버 인증을 쓰는 공용 Redmine 도구와 별개다. 공용 연결만으로 가능한 작업에는 개인 키를 만들 필요가 없다.

## 기존 예약을 복원한 경우

VXvue의 `QA_ProductKnowledge_Sync`는 버전별 Worker 폴더의 코드 사본을 실행한다. Git을 갱신해도 기존 예약의 사본은 바뀌지 않는다. 예약 XML을 복원했다면 인계 ZIP의 `resume/update_context_worker.ps1`을 프로젝트 루트에서 실행해 Preview를 읽는다. 필요한 경우 관리자 PowerShell에서 같은 명령에 `-Apply`를 붙인다. 이 도구는 이전 XML을 백업하고 코드 동작만 바꾸며 시각·계정·설정이 같은지 재조회한다. 실행 중인 Worker가 있으면 종료될 때까지 기다린다.

새 예약이 필요할 때는 저장소의 `scripts/install_product_knowledge_schedule.ps1 -CheckOnly`로 설정을 먼저 확인한다. 예약 설치는 관리자 권한이 필요하다. 로컬 Redmine watch는 자동 등록하지 않았으며 검색 조건·실행 주기를 정한 뒤 별도로 등록한다.

## 개발·배포 재개 기준

`current.json`의 코드 커밋, 운영 파일 해시, 자료 manifest·QA 원본 해시와 검증 결과를 대조한다. 이전 PC에 남아 있던 절대경로나 완료 보고만으로 상태를 판단하지 않는다. 운영 배포 전에는 현재 서버 파일과 다시 비교하고 기존 설정·원문·예약을 보존한다. 서버 재기동 권한이 없으면 인계 문서에 적힌 마지막 단계만 운영 담당자가 실행한다.
