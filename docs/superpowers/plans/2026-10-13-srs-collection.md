# SRS 수집 통합 실행 계획

> 실행: 이 세션에서 직접 구현한다. `executing-plans`와 TDD로 작업별 결과를 확인한다.

**Goal:** 외부 ALM 저장소 없이 SRS를 수집하고 읽을 수 있는 PDF를 배포한다.

**Architecture:** Knowledge 모듈에 기존 ALM 수집·렌더링 로직을 이식하고 현재 설정·인증·동기화를 연결한다.

**Tech Stack:** Python, httpx, BeautifulSoup, Playwright Chromium, PyMuPDF.

**Spec:** [통합 설계](../specs/2026-10-13-srs-collection-design.md), SPEC REQ-SYNC-003.

## 제약과 조사 근거

- 기존 `vxvue_spec_sync.py`, 수집 원본 `apps/srs-spec/src/`, 기존 수집 설정·테스트·PC 작업과 실제 PDF를 대조했다.
- 다른 저장소에 있는 수집·분할·렌더링 복구를 재사용한다. 기존 QA Agent의 분석·메일을 중복 구현하지 않는다.
- 기존 사용자 변경과 인증, 운영 DB, 원본 TC·매뉴얼은 보존한다. 원본 자료는 커밋하지 않는다.
- 관련 시험 → 전체 pytest → 실제 수집·배포·서버 다운로드 → readiness → 변경 파일 검토 → 커밋·push 순서로 완료한다.

## 검토 초점

미완성 날짜 폴더, 부분 수집, 빈 PDF, 최종 경로 읽기 실패, 작업 중복을 시험한다. 실패 상태가 성공으로 보고되지 않아야 한다.

## 작업

- [x] 1. `tests/test_srs_crawler.py`에 설정·배포 복구·최종 검증·공개 완료본 선택·중복 실행 시험을 작성하고 실패를 확인한다.
- [x] 2. `app/modules/knowledge/srs_crawler/`에 원본의 순수 수집·렌더링 모듈을 옮긴다. `load_config(root=None)`은 이 프로젝트의 비밀 설정과 상대 경로를 사용한다. `run(config, snapshot_date=None, force=False)`는 실제 실행 결과 manifest를 반환한다.
- [x] 3. `scripts/collect_srs_spec.py`에 설정 확인·읽기 전용 미리보기·스냅샷 재출력·정상 수집 실행을 연결한다. 실패와 중복은 종료 코드 1이다. `vxvue_spec_sync.py`는 완료 manifest가 있는 새 출력만 선택하며 외부 기존 출력은 호환한다.
- [ ] 4. 관련·전체 시험과 실제 Polarion 수집, PDF 생성, 최종 배포 읽기·해시·페이지를 확인한다. 기존 PC 작업을 백업하고 새 작업을 등록한다. 실행·서버 결과가 정상인 뒤 기존 ALM 예약을 이슈 수집 전용으로 바꾼다. 기존 이슈 입력은 보존한다.
- [ ] 5. SPEC·CHANGELOG·progress·README·문서 지도를 갱신하고 HTML·readiness·정리를 확인한다. 기존 사용자 변경을 제외해 커밋·push한다.

## 실행 확인과 남은 운영 전환

관련 시험 79 passed, 전체 1265 passed·7 skipped. 실제 SRS 465개와 PDF 6개·690페이지를 생성하고 지식 폴더 및 서버 다운로드 해시를 확인했다. 새 예약의 수동 시작도 성공했다. 기존 S4U 예약 변경은 Windows 권한 거부로 미완료다. 관리자 실행용 scripts/install_srs_schedule.ps1을 준비하고 -CheckOnly로 계획을 확인했다. 운영 상태는 progress.md를 따른다.
