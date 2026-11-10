# Bellalun 공용 서버 운영

파트원 사용법은 서버의 `/static/skills/bellalun/index.html`에 있다.
기존 VXvue AI 연결을 재사용하며 Bellalun 전용 도구·자료 판·QA 지침을 제공한다.
사용자에게 개인 지식 폴더나 서버 키 복사를 요구하지 않는다.

## 원본과 공개 범위

제품 원문과 QA 업무 규칙의 원본은 담당자가 관리하는 Bellalun 지식 폴더와 공통 Skill이다.
`references/bellalun-qa-guide.md`가 통합 QA 원본이다. 서버는 수집본을 읽고 제품 원본을 바꾸지 않는다.
서버 연결용 Skill은 `deploy/bellalun-skill/bellalun-server-ai/SKILL.md`에서 관리한다.
이 파일은 도구 호출 순서만 가지며 업무 규칙은 매번 `bellalun_get_workflow`로 읽는다.

실제 제품 자료·공통 QA 원문·manifest·키는 저장소에 넣지 않는다.
원본이 바뀌면 서버에 동기화하고 SHA256을 대조한다. 안내의 접근 범위는 기존 사내 접속·PC 등록 정책이다.
Bellalun Redmine은 VXvue와 같은 읽기 모듈(`app/modules/vxvue_chatbot/redmine.py`)을 Bellalun 프로젝트 범위(기본 75, 지정 86·35·37)로 사용한다. 도구와 보호 규칙은 [VXvue Redmine 읽기 연결](VXVUE_REDMINE_TOOLS.md)과 같다.
로컬 Skill 수정 MCP, Viewer·장비 실행, 이슈 등록·수정은 서버 지원 범위가 아니다.

## 수동 동기화

프로젝트의 Python 환경에서 다음 명령을 사용한다. 실제 경로와 주소는 운영 환경 값을 쓴다.

```powershell
python -X utf8 scripts/sync_bellalun_shared.py --skill-root "<공통 Bellalun Skill 폴더>" --knowledge-root "<Bellalun 지식 폴더>" --upload-to "<QA 서버 base URL>"
```

공통 Skill의 SKILL.md와 참조 MD  전체를 읽고 참조·빈 파일·변경 도중 충돌을 확인한다.
실행 코드·설정·키는 내보내지 않는다. 통합 QA 사본과 자동 생성 Skill 지침만 갱신한다.
사본 교체 전 백업은 `data/bellalun_sync/backups`에 둔다. 다른 수동 작성 파일은 덮어쓰지 않는다.
그 뒤 기존 수집·해시 변경분 업로드·서버 확정 경로를 사용한다.
과거 분리 QA 지침과 프롬프트는 현재 서버 수집 범위에서 제외하며 담당자 원본은 보존한다.

업로드 결과가 SUCCESS인지, failed·unreadable·kept_previous가 없는지 읽는다.
서버 상태→지침 전문을 다시 읽고 통합 QA와 Skill 문서의 해시를 원본과 대조한다.
검색 후보·원문·PDF 페이지를 실제로 조회한다. 수집 성공은 제품 검증 Pass가 아니다.

## PC 예약

```powershell
./scripts/install_bellalun_shared_schedule.ps1 -Python "<Python 실행 파일>" -SkillRoot "<공통 Skill 폴더>" -KnowledgeRoot "<지식 폴더>" -TargetUrl "<QA 서버 base URL>" -CheckOnly
```

확인 후 `-CheckOnly`를 빼면 `Bellalun_SharedKnowledge_Sync`를 등록한다.
매일 10:10과 사용자 로그인 때 실행하고 실패 시 15분 간격 3회 재시도한다.
로그인된 담당자 PC에서만 실행한다. 기존 `QA_ProductKnowledge_Sync`는 변경하지 않는다.
Task Scheduler의 LastTaskResult와 서버의 마지막 수집 시각을 함께 확인한다.
예약 설치 성공만으로 미래 실행 성공을 주장하지 않는다.

## 배포와 확인

핵심 앱 배포에 `app/modules/bellalun_shared`, 기존 desktop.py 변경, 안내 HTML,
Skill 원본과 동기화 스크립트를 포함한다. 의존성은 추가하지 않는다.
운영 파일을 먼저 읽고 차이를 대조하며 기존 운영 설정을 덮어쓰지 않는다.
Bellalun 수집본과 기존 연결 파일을 백업하고 실행 중인 분석이 없는 때 반영한다.
systemd 재시작 뒤 `/health`, 공용 가이드, 기존 VXvue 상태와 Bellalun RPC를 확인한다.
다운로드한 신규 `.mcpb`의 tools.json에도 Bellalun 7개 도구가 있어야 한다.
기존 설치는 동적 목록을 다시 읽으며 도구 목록을 캐시한 앱은 연결을 다시 불러온다.

서버 자료 판은 원문과 검색 인덱스를 모두 검증한다. 불일치 시 결과를 반환하지 않는다.
자료 판은 재시작 후 상태 도구부터 다시 받는다. 최신화 실패를 숨기고 로컬 자료로 바꾸지 않는다.
