# VXvue Issue 보조 스크립트

Generator 연동 Issue를 분석·등록할 때 작업 PC에서 반복하던 수작업을 대신하는 스크립트다. 판단 기준은 통합 QA 지침(운영 4, 9.6, 29.8, 29.10)을 따르며, 이 폴더는 그 절차를 실행만 한다.

## 준비

- Windows의 Python 3.10 이상. 시스템 Python 환경은 바꾸지 않는다.
- 영상 처리 패키지(`opencv-python-headless`)는 처음 실행할 때 `%LOCALAPPDATA%\vxvue-issue-tools\pylib`에 `pip --target`으로 설치된다.
- 결과는 `--out` 폴더에 UTF-8 파일(`*.md`, `*.json`)로 저장된다. 콘솔에는 요약 한 줄과 결과 경로만 출력한다. cp949 콘솔과 `python -I` 실행에서도 한글이 깨지지 않게 하려는 것이다.
- `Database` 폴더는 C: 또는 D: 아래에서 자동으로 찾는다.

## 스크립트

| 파일 | 하는 일 | 예 |
|---|---|---|
| `video_frames.py` | 3초 간격 몽타주를 만든 뒤, 지정 구간을 1초 이하 간격으로 다시 뽑는다. Bucky·상태바·Dose 영역을 원본 해상도에서 잘라 확대한다. PicPick 파일명 시각을 녹화 시작 시각으로 쓴다. | `python video_frames.py "<PicPick>\2026-11-12 13 55 44.mp4" --out out\v --range 1:30-1:44 --crop bucky status dose` |
| `collect_evidence.py` | 영상 시간 ± 여유 분과 로그 안의 시각이 겹치는 Viewer·VSS 로그를 고른다(재실행 전·후 세션). 쓰기 중인 파일도 공유 읽기로 복사하고 `01_현상요약.mp4` 형식으로 이름을 바꾼 뒤 크기 비교표를 만든다. 에뮬레이터 로그처럼 연동별로 필요한 로그는 `--extra`로 지정할 때만 넣는다. | `python collect_evidence.py --video "<영상>" --margin 3 --out out\e --restart` |
| `env_info.py` | 설치된 VXvue·VSS 버전과, 최신 VSS 로그의 `connector created` 줄로 현재 선택된 Generator 모델·포트를 출력한다. `VSS.Vieworks.xml`의 `<DeviceList>`는 설정 가능한 모델 목록으로 따로 보여 준다. 선택값을 확정하지 못하면 `미확정`으로 표시한다. | `python env_info.py --out out\env` |
| `register_redmine_issue.py` | 초안 JSON을 검증하고 전송 payload와 `draft_sha256`을 만든다. 기본은 시험 실행이라 Redmine에 쓰지 않는다. 실제 등록은 `--execute --approved-sha <승인한 해시>`일 때만 한다. 등록 → 공유폴더 `Defect\#번호` 생성·복사·크기 확인 → 본문 경로 번호 기입(댓글 포함, 한 요청) → 재조회 순서이며, 쓰기는 2회다. Grade를 지정하지 않으면 `C`로 넣는다. | `python register_redmine_issue.py draft.json --out out\r` |

## 지킬 것

- Redmine 등록은 사용자가 해당 초안을 승인한 뒤에만 한다. 승인한 초안의 해시와 현재 초안의 해시가 다르면 실행하지 않는다.
- API 키는 환경변수 `REDMINE_API_KEY`에서 읽는다. 화면·파일·로그에 출력하지 않는다.
- 초안 검증 규칙은 서버 `validate_redmine_draft`와 같은 원본(`app/modules/vxvue_chatbot/redmine_draft_rules.py`)을 사용한다. 그래서 이 폴더는 저장소 안의 위치 그대로 실행해야 한다.
- 공용 연결 응답에서는 공유 경로가 가려지므로, 실제 전송 payload는 이 스크립트가 PC에서 만든다(REQ-VXVUECHATBOT-020).

시험: `tests/test_vxvue_issue_tools.py`
