# 본인 PC의 개인 PAT로 ALM 이슈 처리하기

공용 QA 서버의 제품 연결은 자료 조회용이다. 이슈 생성·수정·댓글 등록은 사용자가 쓰는 PC의 로컬 실행 환경에서 본인 명의 Polarion PAT로 운영 ALM에 직접 요청한다. 공용 서버의 토큰이나 다른 사람의 키로 쓰지 않는다. 이 문서는 이슈 업무의 REST API 경로를 다룬다.

## 개인 PAT 준비

1. Polarion 개인 설정에서 본인 명의 Personal Access Token을 발급하고 대상 프로젝트에 필요한 권한을 확인한다.
2. Windows PowerShell 7에서 본인 PC의 사용자 환경 변수에 넣는다. 입력은 가려지며 실행 중이던 Codex·Claude Code는 재시작한 뒤 사용한다.

   ```powershell
   [Environment]::SetEnvironmentVariable('POLARION_TOKEN', (Read-Host -MaskInput 'Polarion PAT'), 'User')
   ```

3. 에이전트는 로컬 환경의 `POLARION_TOKEN` 유무만 확인한다. 값은 메모리의 `Authorization: Bearer …` 헤더에서만 사용한다. PAT를 채팅·공용 QA 서버·저장소·스킬 설정·명령 인자·로그로 전달하지 않는다. 대상은 사용자가 확인한 운영 Polarion HTTPS 주소의 `/polarion/rest/v1`이다. 인증된 요청을 다른 호스트로 리디렉션하지 않는다.

## 쓰기와 재조회

1. 실제 쓰기 요청을 받은 경우에만 실행한다. 프로젝트·이슈 ID 또는 신규 이슈 유형·변경값을 확인한다. 기존 이슈는 현재 값과 상태부터 조회한다. 신규 등록은 필수 속성·중복 후보·프로젝트를 확인한 뒤 `POST /projects/{projectId}/workitems`를 사용한다.
2. 이슈 수정은 `PATCH /projects/{projectId}/workitems/{workItemId}`에 요청받은 속성·관계만 전달한다. 상태는 현재 프로젝트에서 지원하는 값과 전환을 확인한다. Close Version을 Target Version과 맞출 때는 현재 요청 범위와 실제 Plan ID를 대조한다.
3. 댓글의 최종 본문을 조사 메모와 따로 준비한다. 실제 전송할 본문의 사양 출처를 `VP-480`처럼 SRS ID만 남겨 대조한다. 내부 사양 PDF 파일명·분권 번호·판·날짜·쪽수·절 이름이 인용에 남아 있으면 본문을 정리한 뒤 다시 점검한다. 대상 이슈 ID와 최종 본문을 확인한 다음 `POST /projects/{projectId}/workitems/{workItemId}/comments`로 등록한다.
4. 저장 뒤 같은 ID를 조회해 변경 필드, 댓글 본문과 작성 계정, 신규 발급 ID 등 요청한 결과를 확인한다. 응답이 끊기거나 결과가 불명확하면 먼저 재조회한다. 기존 댓글이나 이슈 생성 여부를 확인하지 못한 상태에서 POST를 반복하지 않는다.
5. 로컬 토큰이 없거나 `401`이면 각자 PC의 PAT 설정을 안내한다. `403`이면 해당 계정의 REST 및 프로젝트 권한을 확인해야 한다고 알린다. 등록·수정·댓글에 성공한 범위만 보고한다.

## 게시된 댓글

운영 ALM에서 Work Item 댓글의 본문 변경은 `Cannot modify read-only field(s): text`로 거부됐고 댓글 자체의 DELETE는 제공되지 않았다. 댓글 리소스에 PATCH가 있어도 본문 편집과 스레드 해결 상태 변경은 별개의 동작이다. 기존 댓글을 수정·삭제해 달라는 요청에서 해당 작업이 지원되지 않으면 같은 ID의 본문을 재조회해 결과를 알린다. 해결 처리나 정정 댓글 등록은 사용자가 그 대안을 선택했을 때만 수행한다.

Polarion 공식 문서의 [인증](https://developer.siemens.com/polarion/latest/authentication.html), [권한](https://developer.siemens.com/polarion/latest/authorization.html), [REST API](https://developer.siemens.com/polarion/latest/rest-api-spec.html)는 사용 중인 서버 버전과 맞춰 확인한다.
