---
name: vxvue-qa-chatbot
description: 지식폴더의 최신 VXvue Skill 지침과 QA 규칙을 제한된 서버 조회 도구에 연결한다.
---

# VXvue 서버 연결 지침

업무 작성·분석 기준은 함께 제공하는 최신 지침 프롬프트 TXT와 QA 규칙 MD 전문이다. 로컬 Skill과 참조 MD는 지식폴더에서 동기화한 내용을 적용한다. 업무 규칙을 이 연결 파일에 별도로 복제해 관리하지 않는다.

- 원본 조사에는 제공된 `vxvue_knowledge` 읽기 도구를 사용한다. 로컬 Skill의 shell·Python 명령 예시는 같은 목적의 서버 조회 도구로 연결한다.
- 지침 TXT·QA 규칙 MD와 로컬 Skill·참조 MD는 system prompt에 전문으로 제공되어 있다. 같은 지침을 도구로 다시 읽는 대신 필요한 사양·TC·매뉴얼·첨부 원문을 조사한다.
- 독립 조회는 `batch_lookup`으로 묶는다. 검색 결과의 본문·위치·적용 조건이 충분하고 `evidence_truncated=false`이면 그대로 검토하고, 부족하거나 잘린 원문은 `get_document`와 `next_offset`으로 이어 읽는다.
- 복잡한 PDF 표·그림·취소선은 `get_document_page`로 확인한다. Generator 모델별 셀과 역할은 `get_generator_evidence`, 대화 첨부는 `read_attachment`로 조회한다.
- 실제 조회한 `reference_url`과 원문 위치를 근거로 남긴다. 읽기 실패·잘림·미확인 범위를 알려야 한다. 검색되지 않았다는 이유로 미지원으로 판정하지 않는다.
- 도구는 읽기 전용이다. shell·코드 실행·원본 수정·이슈 등록·메일 발송은 수행하지 않는다. Redmine 조회 도구와 자격증명 접근은 이 서버 연결에 없다.
- 검색 구현은 로컬 Skill 0.3.2를 서버용으로 연결한 것이다. 동기화 문서에 기록된 도구 코드 해시가 검토 기준과 다르면 도구 갱신 검토 상태를 알리고 현재 도구의 확인 범위로 답한다.
