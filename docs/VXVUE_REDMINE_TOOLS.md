# VXvue·Bellalun Redmine 읽기 연결

서버 MCP와 웹 챗봇에서 Redmine 이슈 상세와 여러 프로젝트 검색을 제공한다. 원본 변경·이슈 등록·첨부 다운로드는 제공하지 않는다.

VXvue와 Bellalun은 같은 읽기 모듈 `app/modules/vxvue_chatbot/redmine.py`를 쓴다. 제품별로 다른 것은 허용·기본 프로젝트와 결과 보관 폴더뿐이므로 검색·보호 규칙과 수정은 두 제품에 동시에 적용된다.

| 제품 | 도구 | 기본 프로젝트 | 지정해야 읽는 프로젝트 | 결과 보관 |
|---|---|---|---|---|
| VXvue | `get_redmine_issue`, `search_redmine_issues`, `read_redmine_result` | 61·29·107 | 33·41 | `data/vxvue_desktop/redmine` |
| Bellalun | `bellalun_get_redmine_issue`, `bellalun_search_redmine_issues`, `bellalun_read_redmine_result` | 75 (VDMS Series QA Defect) | 86 (Software Dev), 35·37 (이전 세대 VDMS 1000B) | `data/bellalun_shared/redmine` |

한 제품의 도구는 다른 제품 프로젝트의 이슈를 반환하지 않고, 다른 제품의 결과 번호를 이어 읽지 않는다. Bellalun Redmine 도구는 문서 판(snapshot_id)을 받지 않는다.

## VXvue 등록 초안 준비와 검증

VXvue에는 등록 준비용 읽기 도구 `get_redmine_field_map`과 네트워크 없는 초안 검사 도구 `validate_redmine_draft`도 있다. 서버는 이슈를 등록하지 않는다. 필드 조회는 허용 프로젝트의 트래커·범주·우선순위·버전과 최근 공개 이슈에서 관찰한 사용자 정의 항목을 반환한다.

초안 검사는 제목·본문 6개 섹션 순서·필수 번호·경로·민감정보를 확인한다. `ok=true`인 응답만 전송용 `payload`와 그 내용의 `draft_sha256`을 제공한다. 오류가 있거나 응답 보호로 내용이 바뀌면 두 필드를 생략한다. 공유 경로·IP·이메일 등이 포함된 초안은 공용 연결에서 전송 JSON을 받을 수 없다. 원문은 사용자 PC에서 관리하며 가려진 응답을 등록 본문으로 쓰지 않는다. 이 실패 처리 보완의 운영 반영 여부는 [진행 상태](../progress.md)를 따른다.

규칙 검사는 통과했는데 보호 처리 때문에 payload를 돌려주지 못하면 `checks_passed=true`, `payload_source="pc"`가 함께 온다. 이때 전송용 payload와 해시는 PC의 [`scripts/vxvue_issue_tools/register_redmine_issue.py`](../scripts/vxvue_issue_tools/README.md)가 서버와 같은 규칙 원본(`redmine_draft_rules.py`)으로 만든다. 이 스크립트는 기본이 시험 실행이며, 승인한 `draft_sha256`과 일치할 때만 등록한다.

제목 검색(`search_redmine_issues`)은 첫 페이지에서 한영 동의어 변형을 최대 3개 더 검색해 합친다. 추가로 찾은 결과에는 `matched_query`가 붙는다. `expand_synonyms=false`로 끈다(REQ-VXVUECHATBOT-021).

## 서버 설정

기존 비밀정보 체계에 `REDMINE_HOST`와 `REDMINE_API_KEY`를 넣는다. 환경변수 > secrets.json > secrets.txt > .env 순서다. 호스트는 HTTP(S) 주소와 선택적 `/redmine` 경로만 허용한다. 값이 없으면 도구가 설정 누락을 알린다. 기존 서버 전용 자격증명이 있으면 운영자가 재사용 여부를 확인한다. 개인 PC 키를 자동 전송하지 않는다.

VXvue 공용 연결은 Integration Defect(61), QA Defect(29), 사양문의(107)와 연동 모듈 XIPL(33)·QXLink(QA Defect)(41)만 조회한다. 검색 기본값은 61·29·107이고 33·41은 `project_ids`로 지정할 때만 검색한다. 관리자 키를 쓰더라도 다른 프로젝트와 비공개 이슈는 반환하지 않는다. 요청은 GET, redirect 금지, 요청당 20초·새 요청 시작 예산 55초·응답 10 MiB 상한이다. 원본 응답의 링크를 따라가지 않는다.

## 조회와 범위

- `get_redmine_issue(issue_id=25496)`: 본문·공개 journals·attachments·relations. 비공개 댓글은 저장·반환 전에 제외한다. 제외 여부와 건수는 알리지 않는다. 첨부 본문은 포함하지 않는다.
- `read_redmine_result(result_id=..., offset=...)`: 보호 후 저장한 상세를 끝까지 읽는다. 결과는 24시간 뒤 만료한다.
- `search_redmine_issues(keywords="VXvue", project_ids=[61,29,107], status="*", page=1, page_size=50)`: 제목 검색. 결과 항목에는 번호·프로젝트·상태·제목만 있다.
- `full_text=true`: 프로젝트별 `/projects/{id}/search.json`으로 검색하고 실제 이슈 번호로 프로젝트와 비공개 여부를 다시 확인한다. 종료 이슈(`issue-closed` 결과)도 포함한다. Redmine은 `open_issues`·`titles_only`에 값이 있기만 하면 켠 것으로 보므로 두 매개변수는 보내지 않는다(`=0`을 보내면 열린 이슈의 제목만 검색된다). 공개 제목·본문·댓글·사용자 정의 항목에서도 검색어를 확인하고, 일치한 항목 이름을 `matched_in`으로 돌려준다. 확인할 수 없는 후보와 비공개 이슈는 제외하며 번호·제목·제외 건수를 알리지 않는다. root search의 project_id 필터는 사용하지 않는다. 프로젝트 URL의 기본 범위를 유지하도록 scope 매개변수는 보내지 않는다. 상세의 현재 프로젝트가 요청과 다른 후보는 제외한다. 같은 페이지의 중복은 오류로 남기고 프로젝트 이동으로 다시 나타난 공개 후보는 한 번만 제공한다. 전문 검색의 상태 제한은 숫자 상태 번호만 허용한다.

`next_pages`에 남은 프로젝트·페이지가 있으면 같은 조건으로 계속 읽는다. 제목·전문 검색 모두 공용 후보 목록 조회다. 원본 전체 건수는 항상 null이고 `complete=false`를 유지한다. 다음 페이지가 없어도 전체 자료 조사 완료로 해석하지 않는다. `returned`는 이번에 확인해 제공한 공개 후보 수다. 전송·응답 오류는 `errors`에 남긴다.

공개 본문 일치만 제공하고 비공개·확인 불가 후보는 집계하지 않는다는 안내를 항상 포함한다. 다음 페이지는 원본 검색 후보의 페이지이므로 페이지 유무 자체까지 모든 존재 신호를 차단하는 방식은 아니다. 공유 조회 계정의 권한을 제한하는 것이 권장 운영 방식이다.

## 설치와 지침 반영

기존 Desktop 전달기는 연결할 때 서버의 `tools/list`를 다시 읽으므로 새 서버 도구를 받기 위해 확장 코드를 교체할 필요가 없다. 캐시된 연결은 다시 불러온다. 최신 설치 파일의 도구 목록과 Skill ZIP·Claude Code 설치 명령도 같은 서버 원본에서 만든다. 연결 프로그램 버전은 1.2.1을 유지한다.

관리 Skill의 get_status timeout은 1초 간격 총 3회까지 재시도한다. 계속 실패하면 원인·확인사항을 알리고 로컬 색인·rawfile 등으로 전환하기 전에 사용자 승인을 받는다. 업무 판단은 통합 QA MD를 읽는다.

로그 경로 보존은 승인된 통합 QA 원문의 29.8절에 있는 정확한 템플릿의 해시를 대조한다. 해당 지침·원문 전달에서만 보존한다. 템플릿 주소·경로는 이 저장소에 복제하지 않는다. 다른 문서·대화·이슈의 UNC와 고객 경로에는 기본 보호를 적용한다.

이 저장소의 공용 연결은 Desktop RPC·HTTP MCP와 웹 챗봇이다. 별도 GPT Actions OpenAPI는 이 저장소에서 제공하지 않는다. 외부 GPT 연결의 도구 전달은 해당 연결 서비스에서 따로 확인한다.
