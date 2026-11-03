# XIPL 공용 문서 조회 구현 계획

승인된 범위: VXvue와 Bellalun에서 동일 XIPL 문서를 서버 조회한다.
Spec: `specs/xipl-shared.md`. 기존 `product_knowledge`, 업로드 프로토콜, Corpus와 인증된 RPC를 재사용한다.

## 공통 조건과 인터페이스

- 원문·기존 사용자 변경·제품별 지식·인증을 보존하고 새 의존성을 추가하지 않는다.
- 수집 단위는 `XIPL`, 서버 경로는 `data/product_knowledge/xipl`이다.
- 서버 도구는 `xipl_` 접두어의 4개 읽기 도구다. status 외에는 `snapshot_id`가 필수다.
- 상태 결과의 문서 ID·SHA-256·판이 다른 도구의 입력과 근거다. 입력에 product/path를 받지 않는다.
- 기존 JS 중계가 tools/list를 전달하므로 별도 중계 설치·키 발급 없이 기존 인증 연결을 재사용한다.
  이는 검토안의 전용 연결을 전용 도구군으로 구체화한 결정이다. 제품 간 조회 한계는 서버에서 지킨다.

## 작업 1: XIPL 수집 설정과 판 고정 조회

- 파일: `config/products/xipl.yaml`, `app/modules/shared_knowledge/service.py`, `tests/test_xipl_shared.py`.
- RED: XIPL config 없음과 RPC의 도구 미지원으로 시험 실패를 확인한다.
- GREEN: 제품 설정과 원문 검증·불변 사본·최근 8개 판 캐시를 구현한다.
- 문서 경로와 판 경계를 검증하고 파일 누락·추출 실패 시 새 판 공개를 차단한다.
- 검증: 신규 시험과 기존 수집·업로드 무결성 시험.

## 작업 2: 기존 인증 연결 통합

- 파일: `app/modules/vxvue_chatbot/desktop.py`, 신규 서비스의 definitions/call.
- 입력·출력: 기존 도구 스키마를 복사해 접두어와 필수 snapshot_id를 더한다. 기존 RPC가 응답을 마스킹한다.
- RED→GREEN: 실제 TestClient RPC에서 상태·검색·원문·PDF 이미지, 잘못된 키·판·ID를 확인한다.
- 검증: `test_vxvue_desktop.py`, `test_vxvue_chat_knowledge.py`, `test_vxvue_desktop_bridge.py`와 신규 시험.

## 작업 3: 운영 수집과 양쪽 도구 안내

- 원본 3개의 읽기·해시를 확인해 비공개 XIPL 수집 폴더로 복사한다.
- dry-run 후 기존 업로드 확정 경로로 서버에 전송한다. 서버에서 독립적으로 원본 해시를 읽어 대조한다.
- 스킬의 제품별 QA 규칙은 유지하고 XIPL 도구 선택 안내만 추가한다.
- 기존 중계 프로그램의 새 프로세스에서 도구 목록·상태·검색·원문을 호출한다.

## 검토 초점

1. 원본 누락을 삭제로 오인하지 않는지. 기존 수집 실패 시험을 유지한다.
2. 원문이 같은 이름으로 바뀌었을 때 새 판과 이전 판이 분리되는지. 신규 시험으로 확인한다.
3. 같은 파일명이 다른 제품에 있을 때 XIPL 밖을 읽지 않는지. ID·경로 거절 시험으로 확인한다.
4. DOCX 문단을 페이지로 오인하지 않는지. DOCX 페이지 조회의 기존 한계를 확인한다.
5. 기존 연결이 새 자료함 없이도 정상인지. XIPL 도구 호출 전까지 XIPL 자료를 읽지 않는다.

## 완료 검증과 반영

로컬 전체 pytest와 readiness, 문서 렌더링, 변경 diff를 확인한다. SPEC·CHANGELOG·progress·docs 지도 갱신.
완료 정리 후 관련 파일만 커밋·push한다. 배포 직전 원격 diff와 작업 충돌을 재확인하고 백업한다.
변경 코드·XIPL 설정만 반영한 뒤 health·파일 해시·문서 해시·기존 조회와 양쪽 공용 조회를 재검증한다.
