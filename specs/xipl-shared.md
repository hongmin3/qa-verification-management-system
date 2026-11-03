# XIPL 공용 문서 조회

## 1. 목적과 범위

VXvue와 Bellalun 담당자가 같은 XIPL 매뉴얼과 개정 이력을 조회한다.
서버에 원문을 한 번 수집하고 기존 인증된 AI 연결에서 XIPL 전용 도구로 읽는다.
뷰어별 적용 사양과 QA 업무 지침은 각 제품의 기존 연결에서 확인한다.

## 2. 사용 순서

담당자가 XIPL 자료함을 수집한다. AI는 `xipl_get_status`로 문서 목록과 판을 받은 뒤
그 판에서 검색하고 원문을 읽는다. 프로그램 실행이나 지원 여부 판정은 하지 않는다.

Knowledge의 XIPL 제품 화면에서 원문을 등록·교체할 수 있다.
공용 AI는 확정된 수집 목록을 읽으므로 화면에서 교체한 뒤 XIPL 동기화를 실행해야 새 판을 조회한다.
초기 범위에는 XIPL 예약 동기화 설치를 포함하지 않는다.

## 5. 요구사항

| 카테고리 | 이름 |
|---|---|
| XIPLSHARED | XIPL 공용 자료 |

### REQ-XIPLSHARED-001 XIPL 원문 수집

XIPL 제품 설정으로 기존 수집·변경분 업로드 경로를 사용한다.
User Manual DOCX, STUDIO User Manual PDF는 매뉴얼로, Revision History DOCX는 릴리즈노트로 분류한다.
원본 이름과 해시를 보존한다. 필수 자료는 매뉴얼과 릴리즈노트이며 TC·QA 지침을 요구하지 않는다.

원본 폴더는 환경변수로 지정하며 미설정·접근 실패를 빈 폴더로 처리하지 않는다.
제품별 자료함에 XIPL 사본을 만들지 않고 XIPL 수집본만 관리한다.
수집 실패 시 이전 정상본 보존은 REQ-SYNC-002와 REQ-KNOW-012를 따른다.
XIPL은 자동 QA 분석을 실행하지 않는다.

### REQ-XIPLSHARED-002 같은 판에서 검색과 원문 조회

기존 인증된 서버 연결에 `xipl_get_status`, `xipl_search_documents`, `xipl_get_document`,
`xipl_get_document_page`를 제공한다. 모든 도구는 읽기 전용이며 XIPL 자료함만 읽는다.
상태 도구는 문서 목록·원본 해시·추출 실패·한계와 `snapshot_id`를 반환한다.
다른 도구는 같은 판을 필수로 받으며 조회에 필요한 기존 도구의 매개변수를 사용한다.


새 판이 만들어져도 이미 읽고 있는 판의 내용은 바꾸지 않는다. 프로세스 안에서 최근 8개 판을 유지한다.
모르는 판은 오류로 알린다. 원본 이름·해시·매뉴얼 위치와 응답 잘림 여부를 유지한다.
파일 누락·해시 불일치·추출 실패가 있으면 새 판을 공개하지 않고 오류를 알린다.
기존 판은 계속 조회할 수 있다. 서버 재시작 후에는 상태 도구부터 다시 조회한다.

원문 조회의 문서 ID는 해당 판 목록 안에서만 허용한다. 경로 탈출과 다른 제품 자료 접근을 거절한다.
응답은 기존 비밀정보 마스킹을 적용하며 서버의 로컬 경로를 공개하지 않는다.
PDF 페이지는 이미지로 제공한다. DOCX는 문단·표 위치를 제공하고 렌더링하지 않은 쪽수를 만들지 않는다.
Word 변경 추적·메모와 이미지의 추출 한계를 상태·원문 응답에 표시한다.

### REQ-XIPLSHARED-003 두 제품의 조회 경로 유지

기존 VXvue AI 연결의 동적 도구 목록에 XIPL 전용 도구를 더한다.
VXvue와 Bellalun은 이 도구로 같은 XIPL 원문을 조회하며 각 제품의 사양·QA 지침은 기존 도구로 읽는다.
XIPL 공용 자료가 특정 뷰어의 지원 사양이나 실행 결과를 증명한다고 안내하지 않는다.
기존 인증·회수·접근 네트워크 제한을 그대로 적용한다. 별도 키 복사나 인증 완화를 하지 않는다.
기존 VXvue 스냅샷과 Bellalun 로컬 지식 경로를 변경하지 않는다.

## 11. 테스트 사양

### TEST-XIPLSHARED-001 원문 수집 분류

세 종류의 실제 파일명으로 분류와 필수 자료 설정을 확인한다.
기존 업로드 실패·이전 판 보존 시험도 실행한다.

### TEST-XIPLSHARED-002 인증된 연결의 판 고정 조회

합성 PDF·DOCX·개정 이력으로 상태→검색→원문→페이지 조회를 수행한다.
원문 갱신 전후 판, 잘림, 누락·해시 실패, 문서 ID·경로 탈출, 잘못된 인증을 확인한다.
기존 VXvue 연결 시험을 함께 실행한다. 운영에서는 양쪽 프로젝트가 같은 판·해시를 읽는지 확인한다.

## 12. 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-XIPLSHARED-001 | `config/products/xipl.yaml` | `tests/test_xipl_shared.py`, `tests/test_knowledge_sync_integrity.py` | verified |
| REQ-XIPLSHARED-002 | `app/modules/shared_knowledge/service.py` | `tests/test_xipl_shared.py` | verified |
| REQ-XIPLSHARED-003 | `app/modules/vxvue_chatbot/desktop.py` | `tests/test_xipl_shared.py`, `tests/test_vxvue_desktop.py` | verified |
