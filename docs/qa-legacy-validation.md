# QA 문서 변경 검사와 배포

REQ-PLMSKILL-005의 공통 변경 검사 도구는 PLM, ALM, VXvue와 Bellalun 서버 Skill ZIP 및 QA 서버 장애 대응 Skill에 제공한다. XIPL은 기존 공용 문서 연결을 사용한다. 도구는 PC 파일을 읽기만 하며 운영 PLM·ALM 기록을 자동으로 변경하지 않는다.

## 사용법

같은 제품·문서 유형의 승인본, 이번 회차 실제 증거, 수정본을 구분한다. 다른 제품의 PLM 선례는 등록 방식만 참고한다. [공통 실행 절차](../deploy/qa-validation/references/legacy-validation.md)에서 허용 변경 목록, 위치 표기, 실패 조건, 업로드 전후 검증 및 브라우저 연결 중단 조건을 확인한다.

프로젝트 루트에서:

```powershell
& .\.venv\Scripts\python.exe -X utf8 deploy/qa-validation/scripts/qa_document_guard.py inspect "<기준.docx>"
& .\.venv\Scripts\python.exe -X utf8 deploy/qa-validation/scripts/qa_document_guard.py compare "<기준.docx>" "<수정.docx>" --allow "<허용변경.json>"
```

종료값과 JSON 결과를 모두 읽는다. 자동 비교는 텍스트·구조의 범위에 한하며 최종 레이아웃과 실제 시험 증거가 필요한 판단을 대신하지 않는다. 특수 개체는 해시와 실제 렌더링을 확인한다. 이미지 전용 PDF는 자동 텍스트 검사 성공으로 처리하지 않는다.

## 배포 후보 생성

```powershell
& .\.venv\Scripts\python.exe -m scripts.build_qa_skill_candidates --server "<현재 QA 서버 기본 URL>" --output-dir "<새 후보 폴더>"
```

네 ZIP과 파일별 SHA256이 기록된 manifest.json을 생성한다. 후보는 운영 게시된 파일이 아니다. 해당 폴더에 이미 파일이 있으면 덮어쓰지 않는다.

## 운영 반영 절차

1. 기존 변경사항과 동시 작업을 확인하고 프로젝트 전체 시험 및 준비 검사를 수행한다. 실패하면 배포하지 않는다.
2. VXvue·Bellalun은 `scripts/deploy.ps1`가 호출하는 `scripts/qa_skill_compare.py`로 현행과 후보를 비교한다. 이 스킬 릴리즈만 게시할 때는 같은 비교에 SSH로 재조회한 두 운영 스킬 해시와 배포할 `deploy` 사본을 전달하고, 통과하면 확인된 변경 파일만 전송한다. 운영 작업이 없는지 파일 전송과 서비스 재기동 직전에 확인한다.
3. ALM·PLM은 현재 교육자료 manifest 체인을 검증한 뒤 각각 1.6.0·1.2.0 Skill ZIP과 해시를 새 자료 판에 게시한다. 기존 교육 원본·미확정 항목·이전 판을 보존한다. 모든 파일의 해시를 검증한 뒤 current.json을 원자적으로 전환한다. 이전 판은 되돌리기에 쓸 수 있게 남긴다.
4. VXvue·Bellalun의 동적 ZIP과 웹 안내의 버전별 ZIP은 후보 해시와 실제 서버 파일을 대조한다. 운영 ZIP의 모든 공통 파일이 같은지 확인한다.
5. `scripts/sync_server_skills.py --server <URL>`로 두 CLI 설치본을 갱신하고 `--check`로 변경 없음인지 확인한다. VXvue의 관리 사본 및 플러그인 캐시는 `scripts/sync_vxvue_local_skill.py --server <URL>`의 지원 경로로 확인한다. 기존 로컬 변경 사항을 임의 덮어쓰지 않는다.
6. `client/qa-server-fallback/scripts/qa_fallback.py install`과 `check`를 실행해 로컬 장애 대응 Skill과 보충 블록을 갱신한다. 기존 설정·Junction·인증을 보존한다.
7. 운영 health·다운로드 링크·ZIP SHA256·양 CLI의 설치 파일 및 새 세션의 조회·합성 문서 검사를 확인한다. 운영 PLM/ALM에 시험용 객체를 만들거나 승인하지 않는다.

운영 서버나 PC 설치 경로가 권한상 차단되면 후보 생성과 코드 검증 결과만 남기고 관리자 세션에서 이어서 수행한다.
