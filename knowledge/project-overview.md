## 목적
<!-- akela: id=purpose scope=all tier=must -->

QA 담당자가 별도 AI 채팅에 묻지 않고도 검증할 곳을 찾게 돕는다. 세 가지를 한다.

- 평일 아침 Polarion 의 SRS·이슈 변화를 감지해 바뀐 것만 Claude 로 분석하고 대시보드·메일로 알린다(QA Intelligence Agent).
- 이슈 하나의 검증 범위(관련 사양·TC·Regression 축)를 낸다(QA Agent).
- 개정 매뉴얼의 변경마다 사양과 맞는지 판정한다(매뉴얼 개정 검증).

변경 문서를 올려 Regression TC 를 추천하던 영향 분석 기능은 2026-10-01 에 제거했다.

## 주요 구성
<!-- akela: id=components scope=core-development tier=should -->

FastAPI UI, PDF/Excel Parser, BM25 Retriever, Rule Candidate Selector, Gemini Structured Decision, Validation Engine, HTML/CSV Report, SQLite Storage로 구성한다.

## 운영 격리
<!-- akela: id=production-isolation scope=deployment tier=must -->

서버에서는 `/home/ubuntu/ai-regression-impact-analyzer`만 사용한다. 기존 jjhhub, systemd, nginx, PostgreSQL, VHD 경로를 변경하지 않으며 서비스 등록은 사용자 사전 승인 후에만 수행한다.
