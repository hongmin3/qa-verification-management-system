# QA 검증 관리 시스템 — VXvue AI 챗봇 기능 사양

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `vxvue-chatbot` |
| Document Version | 0.2.0 |
| Last Updated | 2026-10-15 |
| Status | implemented |

## 1. 목적

QA 팀이 GPTs에서 하던 VXvue 문서 조사와 초안 작성을 사내 웹서버에서 이어 한다. 자유롭게 질문하고 자료를 첨부하면 서버 Claude CLI가 Skill과 최신 지침 프롬프트·QA 규칙 MD를 적용해 답한다.

### 이 기능은

브라우저에서 대화 목록을 고르고 질문한다. 사양·매뉴얼·TC·Generator 자료를 근거로 답변과 초안을 받는다. 제품 판정과 초안 반영은 QA가 결정한다.

## 2. 범위

사양·매뉴얼 질의응답, TC 검토, Generator 분석, Issue·메일 초안, 대화 이력과 첨부를 포함한다. 로그인, 사용자 간 공유, 실시간 Redmine 조회, 이슈 등록·메일 발송·원본 TC 수정은 첫 버전 범위 밖이다.

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| VXVUECHATBOT | VXvue AI 챗봇 |

### 요구사항 지도

- 대화 시작·이력: REQ-VXVUECHATBOT-001·002
- 첨부·실행: REQ-VXVUECHATBOT-003·004
- 근거·규칙·지식: REQ-VXVUECHATBOT-005·006
- 개인 PC의 Claude에서 쓰기: Desktop·Cowork 연결 REQ-VXVUECHATBOT-011, Polarion 수집 REQ-VXVUECHATBOT-012, 입력 없는 자동 연결 REQ-VXVUECHATBOT-013, Claude Code CLI 설치 REQ-VXVUECHATBOT-014
- 자동 최신화: 상위 REQ-SYNC-002

### REQ-VXVUECHATBOT-001 챗봇 화면

**하는 일** 허브의 VXvue AI 챗봇 카드로 `/vxvue-chatbot`에 들어간다. 왼쪽 대화 목록과 오른쪽 메시지 영역, 입력·첨부·전송·중지·답변 복사를 제공한다. 답변을 Markdown 파일로 내려받는 기능은 제공하지 않는다.

**순서**

1. 새 대화를 만들거나 기존 대화를 고른다.
2. 질문과 자료를 넣는다. 첨부 이름과 개수를 확인하고 개별 제거할 수 있다.
3. 답변 자리에 진행 카드가 보인다. 카드는 큰 표시와 함께 대기 중 → 자료 검색 준비 → 자료 검색 → 원문 확인 → 답변 작성 단계 중 지금 단계와 경과 시간, 중지 버튼을 보인다. 답변 글이 들어오기 시작하면 카드는 답변 위 한 줄로 줄어든다.
4. 답변의 문서 근거를 열고 후속 질문한다.

**지킬 것** 사용법은 `/vxvue-chatbot/guide`에 둔다. 서버 동작과 무관한 구현 용어를 사용자 입력에 요구하지 않는다.

### REQ-VXVUECHATBOT-002 브라우저별 대화 이력

**하는 일** 로그인 없이 웹서버 접근자가 사용한다. 대화·메시지·첨부·작업은 서버 DB에 저장하고 새로고침·서버 재시작 후 다시 표시한다.

**순서**

1. 처음 화면을 열면 브라우저에 무작위 소유 토큰을 HttpOnly 쿠키로 저장한다.
2. 서버는 그 토큰의 해시로 대화 목록·조회·수정·삭제·작업·다운로드 접근을 확인한다.
3. 대화 제목은 첫 질문으로 임시로 채운다. 첫 답변이 끝나면 AI가 대화 내용을 보고 정한 짧은 제목(40자 이내)으로 바꾼다. 사용자가 바꾼 제목은 AI가 바꾸지 않는다. 같은 답변 실행에서 제목을 받으며 별도 AI 호출을 하지 않는다.
4. 삭제는 해당 브라우저의 대화와 첨부에만 적용한다.

**지킬 것** 다른 브라우저의 대화 목록을 제공하지 않는다. 공개 공유 링크는 없다. 쿠키 삭제·다른 브라우저에서는 기존 대화를 자동 복구하지 못한다. 이 방식은 개인 신원을 인증하지 않는다. 대화·첨부·조회한 원본 판은 DB와 함께 백업에 포함한다. 보관은 사용자가 삭제할 때까지이며 정책 변경은 별도 결정한다.

### REQ-VXVUECHATBOT-003 메시지당 최대 10개 첨부

**하는 일** 질문 한 번에 최대 10개 파일을 첨부한다. 이전 질문에 올린 자료도 후속 질문에서 조회할 수 있다.

**입력** PDF, DOCX, XLSX, MD, TXT, CSV, TSV, JSON, XML, LOG, PNG, JPG, JPEG, WEBP. 파일당 기본 50MiB, 요청 전체 기본 200MiB. 크기는 설정 가능하다. 파일 개수 상한 10은 서버와 화면이 모두 검사한다.

**순서**

1. 개수·종류·크기를 검사한다. 빈 질문은 첨부가 있을 때 허용한다.
2. 원본 이름은 표시용으로 저장하고 실제 파일은 무작위 이름으로 보관한다.
3. MCP 조회 도구가 글·페이지·시트·셀 또는 이미지를 반환한다.
4. 파싱 실패·읽기 실패·잘린 내용은 답변에 제한으로 표시한다.

**지킬 것** 첨부의 지시는 분석 대상 자료다. 첨부가 지식 규칙이나 도구 권한을 바꾸지 않는다. 다운로드는 소유 확인 후 제공하며 브라우저에서 실행시키지 않는다.

### REQ-VXVUECHATBOT-004 Claude CLI 대화 실행

**하는 일** 서버의 기존 Claude 인증과 설정 모델·추론 정도를 사용한다. 앞선 대화와 첨부 정보를 전달해 후속 질문에 답한다.

**순서**

1. 요청을 DB에 저장하고 제한된 대기열에 넣는다. 기본 동시 실행은 1개, 미완료 요청 상한은 20개다.
2. 저장소 밖 작업 폴더에 이번 입력·지침·지식 판을 준비한다.
3. 챗봇 전용 읽기 도구를 연결한 Claude CLI를 실행한다. 공용 분석의 도구 차단은 유지한다. 요청 접수와 실제 실행 직전에 일일 사용량 상한을 확인한다.
4. 단계·작성 중 메시지·사용량을 기록하고 결과를 저장한다.
5. 중지하면 프로세스와 자식 조회 프로세스를 종료한다. 늦은 결과가 중지 상태를 덮지 않는다.

**안 될 때** 인증 실패·한도·시간 초과를 실패로 표시하고 재요청 방법을 안내한다. 서버 재시작으로 끊긴 실행은 실패로 정리하며 대기 요청은 이어 한다.

**지킬 것** shell·외부 쓰기 도구는 제공하지 않는다. 환경에는 기존 Claude 인증과 필수 실행 값만 전달한다. 사용자·개발 프로젝트의 자동 지침·MCP·hook을 읽지 않는다. 대화 이력 예산을 넘으면 오래된 대화가 이번 입력에서 제외됐음을 알린다. 저장된 이력은 지우지 않는다.

### REQ-VXVUECHATBOT-005 Skill과 최신 지침 적용

**하는 일** 로컬 VXvue Skill의 공통 작업·Issue·TC·Generator 규칙과 서버 등록 지식 중 최신 지침 프롬프트 및 QA 규칙 MD를 적용한다. 개정 번호는 숫자 묶음으로 비교한다.

**순서**

1. 필요한 규칙 두 종류를 찾고 전문을 읽는다. 없거나 읽을 수 없으면 분석을 시작하지 않는다.
2. 규칙과 일반 자료·첨부를 구분해 전달한다.
3. 검색·원문 조회로 문서명·개정·페이지 또는 시트·셀의 근거를 찾는다. ID 질의는 그 ID를 정의한 사양 항목을 먼저 보인다.
4. 사실·자료 기반 판단·추가 확인를 구분해 답한다.

**지킬 것** 검색 결과 없음은 미지원 판정이 아니다. 로그·Expected·Pass/Fail을 만들지 않는다. 원본 TC와 완료 결과를 보존한다. 이미지·표·취소선 판단에는 원본 페이지를 조회하거나 제한을 알린다. 인용 링크는 이번 작업에서 조회한 자료만 연결한다.

사양서 근거는 ALM ID(`VP-`번호)와 제목으로 표시한다. 옛 SRS 번호(`Legacy No.`)는 사용자가 물을 때만 쓴다.

사양서의 Status(draft 등)는 현재 운영에서 대부분 draft이므로 답변의 제한이나 추가 확인 항목으로 쓰지 않는다. 사용자가 Status를 물으면 값만 알려준다.

### REQ-VXVUECHATBOT-006 서버 지식 판과 갱신 상태

**하는 일** PC가 꺼져 있어도 마지막 서버 지식을 사용한다. 서버 Knowledge에 새 자료가 등록되면 다음 요청에 반영한다. 기존 03:30 사양서 동기화 결과도 포함한다.

**순서**

1. 등록된 VXvue 문서 목록과 원본 해시로 읽기 전용 지식 판을 만든다.
2. 임시 폴더에 복사·검사 후 준비된 판을 사용한다.
3. 답변 하나는 같은 판을 사용하고 판 번호와 조회 근거를 저장한다.
4. 상단에는 지식 문서 수와 마지막 정상 동기화만 한 줄로 보인다. 펼치면 매뉴얼별 상태·마지막 업데이트 일시, 사양서 자동 업데이트(평일 09:40)의 마지막 성공·시도 일시, 적용 중인 Skill 최신 버전과 동기화 일시를 보인다. 실패·읽기 오류가 있으면 접힌 줄에도 경고로 보인다.

   > **참고** 실행 시각 문구(`매일 08:00 / 실패 시 15분 재시도`)와 제외 파일 목록은 접힌 줄에 두지 않는다. 제외 파일은 펼친 매뉴얼 목록에 `제외` 상태로만 보인다. 접힌 상태와 펼친 상태는 브라우저에 기억한다.

**지킬 것** 파일 업로드 성공과 검색 준비를 구분한다. 실패 문서는 숨기지 않는다. 부분 실패는 정상 최신화로 표시하지 않는다. 동기화한 지식 원본·지침·검색 색인·대화·첨부는 Git에 넣지 않는다. Skill 어댑터와 공개 프롬프트 틀만 코드로 관리한다.

### REQ-VXVUECHATBOT-007 Opus medium 사용량 절감

**하는 일:** 기존 Opus 모델과 medium 추론 설정을 유지하면서 반복 입력과 도구 조회 왕복을 줄인다.

1. CLI 실행 폴더와 공통 지침 순서를 고정한다. 질문마다 바뀌는 지식 오류는 질문 입력에 둔다. 최신 지침 TXT·QA 규칙 MD 전문을 유지한다.
2. 검색 결과마다 반복되는 전체 문서 목록·서버 경로·조회 시각을 제외한다. 문서 목록은 상태 조회에서 제공한다. 근거 본문·개정·위치·취소선·역할·경고·잘림 여부는 유지한다.
3. 여러 검색 또는 문서·첨부 조회를 한 번의 도구 호출로 처리한다. 개별 오류는 다른 조회 결과와 함께 표시한다.
   검색은 기본 6개 후보, 원문은 기본 4개 구간을 반환한다. limit을 늘리거나 next_offset으로 이어 조회할 수 있다.
   묶음 응답은 설정한 추정 토큰 상한(기본 30,000) 안에서 돌려주고, 넘친 요청은 줄인 근거와 이어 읽을 위치를 남긴다.
4. 대화 이력은 기존 한도까지 원문을 유지하고 JSON의 불필요한 공백만 줄인다. 자동 요약을 위한 추가 AI 호출은 만들지 않는다.
5. 도구 왕복을 포함한 최대 진행 횟수를 설정한다. 한도에 도달하면 중단 이유를 표시하고 자동으로 전체 요청을 재실행하지 않는다.
6. 새 입력·캐시 쓰기·캐시 읽기·출력 토큰, 진행 횟수와 CLI의 비용 추정치를 구분해 저장한다. 비용 추정치를 청구액이나 구독 잔여 사용량으로 표현하지 않는다.

**설정:** `vxvue_chatbot.max_turns` 기본 24회, `max_budget_usd` 기본 0(금액 제한 없음), `prompt_cache_ttl` 기본 빈 값(CLI 기본 정책). `batch_token_budget` 기본 30,000(묶음 응답의 추정 토큰 상한), `mcp_output_tokens` 기본 40,000(Claude CLI가 도구 응답 하나에 허용하는 토큰 수).

> **참고** 답변 자체를 다른 대화에서 재사용하거나 근거를 임의로 축약하지 않는다. 캐시 수명은 인증·과금 방식에 따른 CLI 기본 정책을 따른다.

### REQ-VXVUECHATBOT-008 로컬 Skill 지침 자동 반영

**하는 일:** 담당자가 로컬 Skill을 갱신하면 지침과 참조 MD를 기존 지식폴더에서 자동 관리하고 다음 동기화 후 챗봇에 적용한다.

1. PC 지식 업로드 전에 설정한 활성 로컬 플러그인의 최신 설치 캐시를 확인한다. 버전은 숫자로 비교하고 최신 설치본이 불완전하면 이전 판으로 숨기지 않고 오류를 기록한다.
2. SKILL.md와 references의 MD만 자동 생성 문서 `[Skill 지침] VXvue.md`로 내보낸다. 기존 사양·TC·QA 규칙·프롬프트 원본을 수정하지 않는다. 내보낸 파일은 사용자가 따로 편집하지 않는다.
3. 검색 코드·MCP 설정은 해시만 기록한다. 코드 내용·설정·자격증명을 전송하거나 실행하지 않는다. 서버에서 검토한 코드 해시와 다르면 도구 갱신 검토 필요를 표시한다.
4. 챗봇은 동기화 자산 목록의 Skill 지침과 QA 규칙 MD·지침 TXT를 읽는다. QA 규칙과 프롬프트는 각각 최신 개정본의 전문을 적용한다. 서버용 연결 지침은 허용된 도구와 원문 조회 방법을 안내한다.
5. Skill 지침이 바뀌면 고정 사본의 참조 MD 대신 동기화한 내용을 적용한다. 질문별 원본 판에는 Skill 버전과 문서 해시도 남긴다.
6. 누락·읽기 실패·해시 불일치는 조회 상태에 표시한다. 상태 화면에는 서버가 마지막으로 받은 Skill 버전과 그 동기화 일시도 보인다. 최신 필수 규칙을 읽지 못하면 AI 호출 전에 중단한다. PC가 꺼져 있으면 마지막 정상 동기화본을 사용한다.

> **예외** 처음 배포할 때 Skill 동기화본이 없으면 화면에 동기화 대기를 표시하고 서버 연결의 기존 참조 MD를 적용한다. 이후 Skill 자산이 등록되면 그 내용을 사용하며 읽기 실패를 옛 사본으로 숨기지 않는다.

**설정:** 제품의 `knowledge_source.skill_sync`에서 플러그인·Skill을 지정한다. 챗봇의 `reviewed_skill_code_hash`는 도구 변경을 검증하고 배포한 뒤 갱신한다.


### REQ-VXVUECHATBOT-010 누락 범위 확인과 필요한 근거 조회

**하는 일** 목록을 묻는 질문에서는 검색 후보를 끝까지 확인하고 필요한 원문을 읽는다. 상위 결과만 읽고 전체 목록이라고 답하지 않는다.

1. 검색은 기본 단어 중 하나가 맞는 방식(any)을 유지한다. 모든 단어가 필요한 경우 all을 선택한다. 한글·영문 명칭과 표기 차이는 별도 검색으로 대조한다.
2. offset과 next_offset으로 검색 결과를 이어 읽는다. 미리보기(preview)는 후보의 위치와 최대 300자만 제공하며, 근거 전문 확인과 구분한다. 전체 일치 수·문서별 일치 수·남은 수·이어 읽을 위치를 표시한다. 문서 ID로 범위를 좁힐 수 있다.
3. 목록 질문은 제조사·기능처럼 구체적인 단어로 범위를 좁히고 후보를 확인한다. 관련 후보의 잘린 본문과 적용 조건만 추가로 읽는다. 미리보기만 보고 지원 여부를 확정하지 않는다.
4. PDF의 이미지뿐인 페이지·표·취소선은 원본 페이지 도구로 확인한다. 도구는 텍스트 추출 상태와 이미지 조회 사실을 구분한다. 이미지 제공만으로 모델이 내용을 검증했다고 표시하지 않는다. 독립 페이지는 최대 6개를 한 번에 조회하고 각 페이지의 실패를 구분한다. 문서의 정확한 구간을 읽을 때 context 0~3으로 앞뒤 구간도 함께 확인할 수 있다.
5. 전체 지식의 읽기 경고를 해당 질문의 미확인 항목으로 그대로 나열하지 않는다. 관련 위치를 확인하고, 복구 가능한 잘림·이미지 조회를 먼저 시도한다. 여전히 실패한 경우 위치·시도·남은 이유를 답변에 적는다.

**지킬 것** 검색 결과 없음은 미지원이 아니다. 문서 간 조건 차이, 이미지 해상도 부족, 암호화·손상·누락, 페이지 범위 오류와 사용량 차단을 구분한다. 반복 실패나 사용량 차단에 같은 호출을 되풀이하지 않는다. 전체 검색 확인은 실제 제품의 모든 기능을 증명하지 않는다.

### REQ-VXVUECHATBOT-011 Claude Desktop 공용 지식 연결

**하는 일** 파트원이 Claude Desktop 일반 채팅과 Cowork에서 같은 서버의 VXvue 자료와 최신 작업 지침을 조회한다. AI 답변은 각 사용자의 Claude에서 작성한다.

**순서**

1. 사용자는 설치 안내 화면에서 `.mcpb` 확장을 받아 Claude Desktop에 설치한다. 서버 주소·접근 토큰·Polarion 계정은 입력하지 않는다. 연결은 REQ-VXVUECHATBOT-013이 자동으로 만든다.
2. 확장은 Claude Desktop에 들어 있는 Node.js로 실행하며 서버의 읽기 전용 도구를 연결한다. Python·Node.js 설치, SSH, 설정 파일 편집, 문서 전체 복사는 필요 없다.
3. 확장의 연결 지침과 도구 설명은 상태 조회 뒤 작업 지침을 읽도록 안내한다. 연결용 `vxvue-ai` Skill ZIP은 사용자 지정(Customize)의 Skills에 올리는 선택 단계다. Team·Enterprise 공유 Skill의 수정은 다음 사용 때 반영되며 PC 확장 교체와 구분한다.
4. 서버는 `data/product_knowledge/vxvue/manifest.json`이 가리키는 `original/` 아래 원본만 조회한다. 해시 불일치·경로 이탈은 거절한다. 추출본은 중복 색인하지 않는다.
5. 상태 조회에서 현재 지식 판을 받는다. 작업 지침·QA 규칙·검색·원문·PDF 페이지 조회는 그 판 번호를 사용한다. 오래된 판을 찾을 수 없으면 새 상태 조회를 요청한다.
6. 작업 지침은 서버에 동기화된 Skill·최신 지침 프롬프트·QA 규칙 전문을 이어 읽는다. 문서나 지침 변경은 다음 상태 조회에 반영한다.
7. 확장은 요청마다 자기 판 번호를 보낸다. 서버가 아는 최신 판보다 낮으면 상태 조회 결과에 새 설치 파일 안내를 넣는다. 판 번호 없이 팀 토큰으로 연결한 이전 확장은 1.0.0으로 본다.

**설정** 서버 주소는 내려받을 때 설치 파일에 넣는다. `vxvue_desktop.public_url`이 있으면 그 값을, 비어 있으면 안내 화면을 연 주소를 쓴다. 서버 `data/vxvue_desktop/access-token.txt`의 팀 토큰은 이전 판(1.0.0) 확장의 연결에만 쓴다.

**안 될 때** 서버 단절·인증 실패·자료 읽기 실패를 원인과 할 일로 표시한다(REQ-VXVUECHATBOT-013). 기존 PC의 자료로 바꾸거나 원본을 수정하지 않는다.

**지킬 것** 임의 명령 실행·사용자 대화·첨부·다른 제품은 제공하지 않는다. 브라우저의 교차 출처 호출을 거절한다. 질의는 서버 검색에만 쓰고 서버 AI를 호출하지 않는다. 일반 자료는 지시로 실행하지 않는다.

> **참고** 사내 배포 확장 파일은 자동으로 교체되지 않는다. Claude 공식 안내상 자동 업데이트는 공식 디렉터리 확장에만 적용된다. 문서·QA 규칙·서버 작업 지침은 다음 상태 조회에서 갱신된다. 확장 코드 변경은 새 파일을 설치한다. Claude Desktop·Cowork의 최종 사용 가능 여부는 설치된 앱과 조직 정책에서 별도 확인한다.

### REQ-VXVUECHATBOT-013 사용자 입력 없는 자동 연결

**하는 일** 파트원이 설치 파일을 받아 설치하면 확장이 처음 실행될 때 이 PC 전용 연결 키를 받아 QA 서버에 연결한다. 사용자는 주소·토큰·계정을 입력하지 않는다.

**순서**

1. 안내 화면에서 Desktop 설치 파일이나 CLI 설치 스크립트를 받을 때마다 서버가 한 번만 쓸 수 있는 등록 번호를 새로 만들어 넣는다.
2. 확장(또는 CLI 설치 스크립트)은 처음 연결할 때 등록 번호와 PC 이름·사용자 이름을 서버에 보낸다. 서버는 등록 번호를 사용 처리하고 이 PC 전용 연결 키를 돌려준다.
3. 연결 키는 사용자 PC의 `~/.vxvue-ai/credentials.json`에 서버 주소별로 저장한다. 앱 재시작·확장 재설치·같은 PC의 CLI 설치는 저장된 키를 다시 쓴다.
4. 서버는 요청마다 연결 키를 확인한다. 취소됐거나 오래 쓰지 않은 키는 거절한다. 거절되면 확장은 저장된 키를 지우고 설치 파일의 등록 번호로 한 번 다시 등록한다. 다시 등록도 안 되면 처음 거절된 원인(취소·만료)을 그대로 알린다.
5. 서버에 닿지 않아 등록하지 못한 경우에는 등록 번호를 버리지 않는다. 연결이 돌아오면 다음 요청에서 다시 등록한다.

**접근 허용 범위**

- 등록 번호 발급·등록·연결은 `vxvue_desktop.allowed_networks`에 든 접속 주소에서만 허용한다. 기본값은 사설망 대역(10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)과 서버 자신이다. 그 밖의 주소는 거절한다.
- nginx를 거친 요청은 nginx가 넘긴 실제 접속 주소로 판단한다. 다른 주소에서 온 전달 머리글은 믿지 않는다.
- 관리자는 `scripts/vxvue_desktop_devices.py`로 등록된 PC(PC 이름·사용자·접속 주소·등록 시각·마지막 사용 시각)를 보고 하나씩 취소한다.
- 이전 판 팀 토큰은 `vxvue_desktop.accept_team_token`이 참일 때만 받는다.

**설정**

| 키 | 기본값 | 뜻 |
|---|---|---|
| `vxvue_desktop.public_url` | 빈 값 | 설치 파일에 넣을 서버 주소 |
| `vxvue_desktop.allowed_networks` | 사설망 대역과 서버 자신 | 등록·연결을 허용할 접속 주소 범위 |
| `vxvue_desktop.ticket_hours` | 168 | 등록 번호 유효 시간 |
| `vxvue_desktop.device_idle_days` | 90 | 이 기간 쓰지 않은 연결 키를 거절 |
| `vxvue_desktop.accept_team_token` | true | 이전 판 팀 토큰 허용 |

**안 될 때** 확장은 서버에 닿지 않아도 시작한다. 시작 응답(`initialize`)은 서버를 기다리지 않고 설치 파일에 든 연결 지침으로 바로 답한다. 도구 목록은 서버에 10초까지 묻고, 답이 없으면 설치 파일에 든 목록을 쓴다. 이유: Claude Desktop은 시작 응답이 늦은 확장을 실패로 처리한다. 확장은 단계별 기록을 `~/.vxvue-ai/bridge.log`에 남기며 키·등록 번호·조회 내용은 적지 않는다. 도구 결과에 원인과 할 일을 한국어로 돌려준다. 원인은 사내망 연결 없음, 주소 해석 실패, 서버 프로그램 중지, 응답 시간 초과, 허용되지 않은 네트워크, 등록 번호 사용됨·만료, 연결 키 취소·만료, 서버 오류로 나눈다.

**지킬 것** 연결 키와 등록 번호를 로그·화면·도구 응답에 출력하지 않는다. 서버는 둘 다 해시만 저장한다. 등록 번호는 한 번만 쓴다. Polarion 계정은 서버에만 두고 PC로 보내지 않는다.

> **참고** 등록 번호는 한 번만 쓰이므로 다른 사람에게 전달한 설치 파일은 등록되지 않는다. 각자 안내 화면에서 받는다.

### REQ-VXVUECHATBOT-014 Claude Code CLI 연결 설치

**하는 일** Claude Code CLI 사용자는 안내 화면의 PowerShell 명령 한 줄로 VXvue AI 연결과 Skill을 등록한다.

**순서**

1. 명령이 서버에서 설치 스크립트를 받아 실행한다. 스크립트에는 서버 주소와 새 등록 번호가 들어 있다.
2. 저장된 연결 키가 있으면 다시 쓰고, 없으면 REQ-VXVUECHATBOT-013 방식으로 등록한다.
3. `claude mcp add --transport http --scope user vxvue-ai <서버>/vxvue-desktop/mcp`로 사용자 범위 연결을 등록한다. 같은 이름의 이전 연결은 바꾼다.
4. `~/.claude/skills/vxvue-ai/SKILL.md`를 설치한다. 같은 이름의 다른 Skill이 있으면 덮어쓰지 않고 알린다. 이 스크립트가 설치한 표시가 있는 이전 판만 교체한다.

**안 될 때** `claude` 명령을 찾지 못하거나 등록·연결 등록에 실패하면 원인을 보이고 멈춘다. 이미 바꾼 단계는 알린다.

**지킬 것** Node.js·Python 설치를 요구하지 않는다. 서버의 `/vxvue-desktop/mcp`는 MCP HTTP 방식의 POST 요청을 받는다. 알림에는 202, GET에는 405를 돌려준다. 연결 키는 사용자 PC의 Claude 설정에만 저장한다.

### REQ-VXVUECHATBOT-012 Polarion 번호·필터로 현재 자료 수집

**하는 일** VP 번호 또는 사용자가 준 Polarion 필터로 사양·이슈를 읽어 Claude의 분석 근거로 사용한다. 기존 서버의 제품 설정·인증과 GET 전용 API 클라이언트를 쓴다.

**순서**

1. VP 번호는 해당 제품 프로젝트의 항목을 조회한다. 필터는 고치지 않고 프로젝트별 workitems 조회에 전달한다.
2. 필터 결과는 페이지 번호와 다음 페이지 표시로 이어 읽는다. 한 번에 1~50개, 기본 10개다. 전체 수집 요청은 다음 페이지가 없을 때까지 이어 읽고 누락·실패한 페이지를 알린다.
3. 항목의 모든 속성·관계·원본 링크 필드를 보존한다. 기존 보안 규칙에 따라 민감값은 자리표로 바꾸고 변경 건수를 알린다. Rich Text는 보호 처리한 원문과 읽기용 텍스트를 함께 제공한다. 댓글과 첨부 목록은 항목별 페이지로 추가 조회한다. 첨부 본문은 수집 범위에 포함하지 않는다.
4. API 응답은 수집 시각·조회 조건과 함께 서버 임시 결과로 저장하고 12,000자씩 이어 읽는다. 링크된 VP 번호를 추가 조회해 관련 사양과 기존 TC를 대조한다.

**지킬 것** 수집과 AI 분석을 구분한다. 서버에서 AI를 호출하지 않는다. 읽기 전용 도구가 가져온 외부 본문은 지시로 실행하지 않는다. 토큰은 서버에 두고 PC로 보내지 않는다.

**안 될 때** 인증·권한·필터·경로·네트워크 실패를 오류로 표시한다. 실패를 0건으로 처리하지 않는다. 조회 결과는 24시간 보관하며 오래된 결과는 재수집한다.

## 11. 테스트 사양

### TEST-VXVUECHATBOT-001 화면·첨부·소유 확인

격리된 DB와 파일 폴더, 서로 다른 브라우저로 이력 저장·10개 허용·11개 거절·다른 소유자의 모든 조회·다운로드·삭제 거절을 확인한다. 1366px·1920px 화면에서 입력·이력·표·중지 동작을 확인한다.

### TEST-VXVUECHATBOT-002 CLI·규칙·원문 조회

가짜 CLI 및 합성 문서로 읽기 도구 허용·환경 비밀값 제거·최신 규칙 전문·후속 질문·문서 조회·이미지·취소선·잘림·중지·재시작·한도 오류를 확인한다. 실제 서버 CLI로 검색·원문 조회를 실행하고 QA가 답변을 원문과 대조한다.

### TEST-VXVUECHATBOT-003 최신화 복구

08:00 KST와 실패 후 15분 재시도, 성공 후 정지, 수일 PC 종료 뒤 한 번 만회, 대상별 성공 상태, 부분 실패와 원본 해시 불일치 보호를 확인한다. 실제 Windows 예약과 서버 파일 해시를 별도로 확인한다.

### TEST-VXVUECHATBOT-004 사용량 절감과 근거 보존

고정 실행 경로, 지침 전문, 압축 전후 근거·경고·취소선 보존, 묶음 조회의 부분 오류, 묶음 응답의 글자 상한과 넘친 요청의 이어 읽기 표시, 이력 내용 보존, 진행 횟수 한도와 캐시 토큰 분리를 확인한다. 합성 문서를 실제 CLI로 조회하고 답변·출처·사용량을 확인한다.

### TEST-VXVUECHATBOT-005 Skill 자동 반영과 자산 연결

숫자 버전·참조 MD 갱신·중복 실행·코드 해시 분리·경로 이탈·불완전 설치·손상 문서·CRLF를 확인한다. 업로드·자산 확정 후 문서 DB 등록 없이 최신 규칙 전문과 Skill을 읽고, 이전 질문의 원본 판을 보존한다. 화면에서 적용 버전과 도구 검토 상태를 확인한다.

### TEST-VXVUECHATBOT-006 자동 연결과 실패 안내

합성 등록 번호로 한 번 사용·만료·허용되지 않은 네트워크 거절, 해시 저장, 연결 키 취소·유휴 만료, 이전 팀 토큰 호환을 확인한다. 실제 Node 전달기로 첫 등록, 재시작 후 키 재사용, 서버 중지·연결 거부·인증 거절의 안내 문구를 확인한다. 설치 파일과 스크립트에 장기 키가 없는지 확인한다. 실제 Claude CLI·Desktop 일반 채팅·Cowork 확인은 `progress.md`에 각각 기록한다.

## 12. 요구사항 추적성

<!-- Skill 동기화 시험은 문서 DB 등록 없이 실제 자산 확정 경로를 사용한다. -->

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-VXVUECHATBOT-011 | `app/modules/vxvue_chatbot/desktop.py`, `deploy/claude-desktop/server.cjs`, `scripts/build_vxvue_desktop.py`, `scripts/configure_vxvue_desktop.py` | `tests/test_vxvue_desktop.py` | implemented |
| REQ-VXVUECHATBOT-012 | `app/modules/vxvue_chatbot/desktop_polarion.py` | `tests/test_vxvue_desktop_polarion.py` | implemented |
| REQ-VXVUECHATBOT-013 | `app/modules/vxvue_chatbot/desktop_enroll.py`, `app/core/vxdesk_storage.py`, `deploy/claude-desktop/server.cjs`, `scripts/vxvue_desktop_devices.py` | TEST-VXVUECHATBOT-006: `tests/test_vxvue_desktop_enroll.py`, `tests/test_vxvue_desktop_bridge.py` | implemented |
| REQ-VXVUECHATBOT-014 | `app/modules/vxvue_chatbot/desktop.py`, `deploy/claude-desktop/install-claude-code.ps1` | TEST-VXVUECHATBOT-006: `tests/test_vxvue_desktop_enroll.py` | implemented |
| REQ-VXVUECHATBOT-001 | `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/router.py`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-002 | `app/core/vxchat_storage.py`, `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/service.py`, `app/prompts/vxvue_chatbot.yaml`, `app/web/static/vxvue-chatbot.js` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_storage.py`, `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-003 | `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/mcp_server.py` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-004 | `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/service.py` | TEST-VXVUECHATBOT-002: `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-005 | `app/modules/vxvue_chatbot/knowledge.py`, `app/modules/vxvue_chatbot/skill/scripts/corpus.py`, `app/modules/vxvue_chatbot/skill/SKILL.md`, `app/prompts/vxvue_chatbot.yaml` | TEST-VXVUECHATBOT-002: `tests/test_vxvue_chat_knowledge.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-006 | `app/modules/vxvue_chatbot/knowledge.py`, `app/core/knowledge_retry.py`, `app/modules/vxvue_chatbot/router.py`, `app/core/vxchat_storage.py`, `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css` | TEST-VXVUECHATBOT-003: `tests/test_product_knowledge_retry.py`, `tests/test_product_knowledge_retry_script.py`, `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-007 | `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/mcp_server.py`, `app/core/vxchat_storage.py`, `config.yaml` | TEST-VXVUECHATBOT-004: `tests/test_vxvue_chat_cost.py` | verified |
| REQ-VXVUECHATBOT-008 | `app/core/skill_knowledge.py`, `app/modules/vxvue_chatbot/knowledge.py`, `app/core/knowledge_push.py`, `app/modules/vxvue_chatbot/router.py`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css` | TEST-VXVUECHATBOT-005: `tests/test_skill_knowledge.py`, `tests/test_vxvue_chat_knowledge.py`, `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-010 | `app/modules/vxvue_chatbot/skill/scripts/corpus.py`, `app/modules/vxvue_chatbot/mcp_server.py`, `app/prompts/vxvue_chatbot.yaml` | `tests/test_vxvue_chat_retrieval.py` | verified |
