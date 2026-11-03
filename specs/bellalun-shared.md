# Bellalun 공용 서버 지식과 Skill

## 1. 목적과 범위

파트원이 개인 PC의 지식 폴더 없이 Bellalun 사양·매뉴얼·TC와 최신 공통 지침을 조회한다.
기존 QA 서버의 인증된 VXvue AI 연결을 공유하며 제품별 자료 판을 분리한다.
사내 사용자에게 공개하고 기존 네트워크·PC 등록·회수 정책을 유지한다.

## 2. 사용 순서

Bellalun 안내 화면에서 연결과 `bellalun-server-ai` Skill을 설치한다.
기존 연결 사용자는 연결을 다시 불러온다. 상태 → 최신 지침 전문 → 검색 → 원문 순으로 조회한다.
서버 지침은 사용 때마다 읽으며 설치 Skill에 QA 규칙을 중복 저장하지 않는다.

## 5. 요구사항

| 카테고리 | 이름 |
|---|---|
| BELLALUNSHARED | Bellalun 공용 서버 |

### REQ-BELLALUNSHARED-001 자료와 최신 공통 지침 동기화

기존 Bellalun 수집·변경분 업로드를 사용한다. 사양·매뉴얼·TC 원본은 읽기 전용이다.
현재 공통 Skill의 SKILL.md·참조 MD를 실행 코드·자격증명 없이 내보낸다.
통합 QA 지침을 유일한 qa_rules로 선택하고 과거 분리 지침은 수집 대상에서 제외한다.

접근 불가·빈 지침·동기화 도중 변경은 실패로 알린다. 사내 자료는 Git에 넣지 않는다.
동기화 스크립트는 명시적인 원본 폴더·공통 Skill 폴더·서버 URL을 받는다.
반복 실행은 같은 내용을 유지하며 기존 수집 예약을 변경하지 않는다.

별도 Bellalun 예약은 매일 10:10과 사용자 로그인 때 실행하고 실패하면 15분 간격 3회 재시도한다.
로그인된 담당자 PC에서 실행하며 기존 VXvue 예약을 대체하지 않는다.

### REQ-BELLALUNSHARED-002 인증된 판 고정 조회

기존 연결에 bellalun_get_status, bellalun_get_workflow, bellalun_search_documents,
bellalun_get_document, bellalun_get_document_page, bellalun_find_testcases,
bellalun_trace_requirements를 추가한다. 모두 읽기 전용이다.

상태는 문서 목록·SHA256·추출 오류·한계·Bellalun 전용 snapshot_id를 반환한다.
다른 조회는 그 판을 필수로 받는다. 새 판이 생겨도 최근 8개 판은 변경되지 않는다.
임시 자료 판 확정 시 Windows 공유·접근 오류(5·32·33)는 최대 8회, 합계 0.7초 범위에서만 재시도한다.
기존 판을 덮어쓰거나 다른 권한 오류를 성공으로 바꾸지 않는다. 동일한 확정 절차를 쓰는 XIPL에도 적용한다.

필수 사양·매뉴얼·TC·통합 QA·Skill 누락, 해시 불일치, 추출 오류는 새 판 공개를 차단한다.
값이 없는 서식 셀은 근거로 만들지 않는다. 단독 연속 TC ID 행은 원래 행 범위·접두사·번호 수식으로 무손실 표현하고 Step·Expected가 없는 번호 구간임을 명시한다.
세로 병합에서 실제 절차가 이어지는 행은 번호 구간 압축에서 제외한다. 원본 파일은 수정하지 않는다.

문서 ID는 해당 판의 목록으로 제한하고 경로 탈출·다른 제품 판·알 수 없는 입력을 거절한다.
서버 경로와 비밀정보는 응답에 노출하지 않는다. 지침은 마스킹 후 문자 단위로 이어 읽는다.

PDF는 원본 페이지 이미지, DOCX는 문단·표 위치를 사용하며 렌더링하지 않은 쪽수를 만들지 않는다.
Bellalun에 VXvue의 Polarion·Generator 판단 규칙을 적용하지 않는다.
실시간 Bellalun Redmine 조회는 이번 범위에 없으며 로컬 전용 도구와 지원 차이를 명시한다.

### REQ-BELLALUNSHARED-003 안내와 배포

서버에 설치·최초 질문·작업 예시·업데이트·연결 문제·제약을 담은 한국어 가이드를 제공한다.
Skill ZIP은 개인 경로·키·제품 원문 없이 서버 도구 순서만 담는다.
기존 로컬 bellalun-ai 및 공통 원본은 덮어쓰지 않고 별도 bellalun-server-ai 이름을 사용한다.
기존 확장 다운로드는 최신 Bellalun 도구를 포함한다. 인증을 완화하거나 키를 공유하지 않는다.

## 11. 테스트 사양

### TEST-BELLALUNSHARED-001 판·원문·지침

합성 사양·매뉴얼·TC·지침으로 실제 인증 RPC에서 목록·검색·원문·지침 이어 읽기를 확인한다.
갱신 전후 판, 필수 자료 누락, 해시 오류, 저장된 판 변조, 다른 제품 판, 경로 탈출,
잘못된 인증·입력과 PDF/DOCX 차이를 확인한다. 기존 VXvue·XIPL 회귀도 실행한다.

### TEST-BELLALUNSHARED-002 동기화와 배포물

합성 Skill 내보내기의 해시·반복 실행·불완전 원본 실패·기존 파일 보존을 확인한다.
서버 안내·Skill ZIP과 연결 도구 목록을 읽고 실제 서버에서 사양 검색·원문·지침 해시를 확인한다.

## 12. 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-BELLALUNSHARED-001 | `scripts/sync_bellalun_shared.py`, `config/products/bellalun-viewer.yaml` | `tests/test_bellalun_shared.py` | verified |
| REQ-BELLALUNSHARED-002 | `app/modules/bellalun_shared/service.py`, `app/modules/vxvue_chatbot/desktop.py`, `app/core/snapshot_files.py` | `tests/test_bellalun_shared.py`, `tests/test_vxvue_desktop.py`, `tests/test_xipl_shared.py`, `tests/test_snapshot_files.py` | verified |
| REQ-BELLALUNSHARED-003 | `app/web/static/skills/bellalun/index.html`, `deploy/bellalun-skill/bellalun-server-ai/SKILL.md` | `tests/test_bellalun_shared.py`, `tests/test_skill_management.py` | verified |
