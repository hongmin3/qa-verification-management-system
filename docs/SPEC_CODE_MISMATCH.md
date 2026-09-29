# 사양–코드 불일치 목록

`SPEC.md` 를 코드·테스트·문서와 대조하면서 찾은 차이를 모은다. 사양은 문서가 약속한 동작을 적고, 코드가 그 약속과 다른 곳은 여기에 남긴다.

각 항목의 `Action` 은 무엇을 고쳐야 하는지 적는다.

| Action | 뜻 |
|---|---|
| CODE 수정 | 사양이 맞다. 코드를 사양에 맞게 고친다 |
| SPEC 수정 | 사양 문장이 틀렸다. 사양을 고친다 |
| 사용자 확인 필요 | 어느 쪽이 맞는지 사람이 정해야 한다. [결정 대기 목록](../OPEN_QUESTIONS.md)에 선택지가 있다 |

요구사항 카드의 "(MISMATCH 3)" 같은 표시는 그 카드가 속한 절(아래 같은 제목)의 3번 항목을 가리킨다.

## 1. Regression 영향 분석과 추천 평가 (10건)

### 1

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-010, REQ-IMPACT-013, REQ-CORE-002
Specification: docs/modules/impact-analyzer.md "analysis.recommended_confidence(기본 0.80) 이상은 추천, review_confidence(기본 0.60) 이상은 Manual Review 대상으로 분류한다. 낮은 확신을 조용히 추천으로 올리지 않는다." knowledge/core-ai-integration.md "근거 없는 판정을 추천으로 올리지 않기 위해서다."
Current Implementation: 추천 여부(recommended)는 AI 응답 값을 그대로 쓴다. validate_decisions 는 신뢰도·검토 상태·확인 필요 표시만 바꾸고 recommended 를 바꾸지 않는다. 신뢰도 0.4 인 판정도 AI가 추천했으면 보고서 4절 추천 표("확인 필요"로 표시), 추천 수, 정확도 평가의 추천 집합에 들어간다. 반대로 신뢰도 0.9 라도 AI가 추천하지 않았으면 추천이 아니다. 문서의 "review_confidence 이상은 Manual Review" 도 코드(0.60 이상은 REVIEW_RECOMMENDED, 그 미만이 MANUAL_REVIEW_REQUIRED)와 다르다.
Difference: 추천을 신뢰도로 정한다는 문서 약속이 구현되지 않았다. 근거 없는 판정도 추천 수와 평가에 포함된다.
Action: 사용자 확인 필요 (결정 필요 1)
```

### 2

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-014
Specification: knowledge/workflow.md "HTML/CSV 생성 순서로 실행한다", knowledge/project-overview.md "HTML/CSV Report". 이번 작업 지시도 "CSV/HTML 내보내기".
Current Implementation: create_xlsx_export 가 XLSX 만 만든다. CSV 를 만드는 코드는 없다. README, docs/modules/impact-analyzer.md, 사용법 화면, 분석 화면·이력 링크는 모두 XLSX 라고 적는다.
Difference: 결과 파일 형식이 문서마다 다르다.
Action: 사용자 확인 필요 (결정 필요 2)
```

### 3

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-005
Specification: app/core/knowledge_documents.py 설명 "읽지 못한 문서 (파일 없음·파싱 실패). 조용히 빠지면 '사양 없음' 오판으로 이어진다." 그래서 failures 에 남긴다고 적는다.
Current Implementation: RegressionAnalyzer.run_for_product 는 knowledge.failures 를 결과에 넣지 않는다. 결과의 knowledge_documents 와 보고서의 "사용한 사양서"에는 읽지 못한 문서까지 "사용한 문서"로 나온다.
Difference: 읽지 못한 문서가 분석 결과·보고서·상세 화면 어디에도 보이지 않고, 오히려 사용한 것처럼 보인다.
Action: CODE 수정 (결과에 읽지 못한 문서 목록을 넣고 보고서 1절과 분석 상세에 보인다)
```

### 4

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-017
Specification: 후보 수 상한은 retrieval.candidate_limit 설정값이다(docs/modules/impact-analyzer.md 설정 표).
Current Implementation: analysis_detail.html 의 설명 문장에 "최대 150개", "앞 150개"가 숫자로 적혀 있다.
Difference: 설정을 바꾸면 화면 설명이 틀린다.
Action: CODE 수정 (설정값을 화면에 넘겨 보인다)
```

### 5

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-018
Specification: 분석 상세 화면의 "같은 입력으로 재실행" 버튼으로 재실행한다(템플릿 버튼, test_retry_analysis_creates_linked_job).
Current Implementation: 버튼은 일반 폼 전송이고 POST /analyses/{job_id}/retry 는 JSON 을 돌려준다. 브라우저에 {"job_id": …} 글이 그대로 보이고 새 분석 화면으로 가지 않는다. 오류(409·429)도 JSON 글로 보인다.
Difference: 화면 버튼 사용 흐름이 끝나지 않는다.
Action: CODE 수정 (폼 전송이면 새 분석 상세로 303 이동하거나, 버튼을 스크립트 호출로 바꾼다)
```

### 6

```text
SPEC / CODE MISMATCH
Requirement: NFR-IMPACT-003
Specification: config.yaml analysis.max_retries: 3, retry_min_seconds: 1, retry_max_seconds: 10 (설정 키가 있으므로 바꾸면 동작이 바뀐다고 읽힌다)
Current Implementation: GeminiClient._request 의 재시도 횟수·간격은 코드에 3회·1초·10초로 적혀 있고 세 설정 키는 어디서도 읽지 않는다.
Difference: 설정을 바꿔도 재시도 동작이 바뀌지 않는다.
Action: 사용자 확인 필요 (설정을 코드에 연결하거나 config.yaml 에서 키를 지운다)
```

### 7

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-011, REQ-IMPACT-010
Specification: 사람 확인 필요 판정은 검토 상태가 MANUAL_REVIEW_REQUIRED 다(knowledge/core-ai-integration.md Confidence 분류).
Current Implementation: attach_specification_references 는 근거 조각에 취소선이 있으면 manual_review_required 를 켜지만, 검토 상태는 이미 분류된 값(예: AI_RECOMMENDATION_ACCEPTED)을 그대로 둔다.
Difference: 한 판정이 "AI 추천 채택"이면서 "확인 필요"가 될 수 있다.
Action: CODE 수정 (취소선으로 확인 필요가 될 때 검토 상태도 MANUAL_REVIEW_REQUIRED 로 바꾸거나, 근거 붙이기를 분류 전에 한다)
```

### 8

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-010
Specification: 지시문 "제공된 TC ID와 Specification chunk ID만 사용한다."
Current Implementation: validate_decisions 는 tc_id 를 AI에게 보낸 TC 후보가 아니라 제품의 전체 TC 목록과 대조한다. 조각 번호는 보낸 조각과 대조한다.
Difference: AI에게 보내지 않은 TC 에 대한 판정도 통과한다.
Action: 사용자 확인 필요 (후보 목록과 대조하도록 좁히는 것을 추천)
```

### 9

```text
SPEC / CODE MISMATCH
Requirement: NFR-IMPACT-001, REQ-IMPACT-018
Specification: knowledge/core-ai-integration.md "analysis.daily_token_limit 을 넘으면 새 분석 실행을 시작 전에 429로 차단한다."
Current Implementation: POST /analyses 는 한도를 검사하지만 POST /analyses/{job_id}/retry 는 검사하지 않는다.
Difference: 한도를 넘은 뒤에도 재실행으로 새 AI 호출을 할 수 있다.
Action: CODE 수정
```

### 10

```text
SPEC / CODE MISMATCH
Requirement: REQ-IMPACT-020, REQ-IMPACT-004
Specification: 사용법 화면 "실제 진행 단계(입력 문서 분석 → 변경사항 추출 → 사양서 조회 → TC 후보 검색 → AI 영향도 분석 → TC 선정 → 결과 생성)" (7단계)
Current Implementation: ANALYSIS_STAGES 는 8단계이고 7번째 "신규 TC 초안 검증"이 있다.
Difference: 사용법 화면의 단계 목록에 한 단계가 빠졌다.
Action: 사용법 화면 수정 (코드 동작은 그대로)
```

## 2. QA Agent (이슈 검증 범위 산정) (12건)

```text
SPEC / CODE MISMATCH 1
Requirement: REQ-QAAGENT-005 (G4)
Specification: 사용법 화면(/qa-agent/guide 3절 표)과 docs/modules/qa-agent.md 6절이 "Test Data 준비를 비워 두면 G4가 '확인 필요'로 대기(NEED_INPUT)"라고 안내한다. 분석 폼도 "비워 두면 G4가 확인을 요청합니다"라고 쓴다.
Current Implementation: gates.py::evaluate_g4 는 test_data_ready 가 False(폼 `없음`)일 때만 input 을 세운다. None(모름)이면 아무 항목도 남기지 않는다.
Difference: Test Data 를 모름으로 두면 G4 가 통과할 수 있다.
Action: 사용자 확인 필요 (추천: CODE 수정)
```

```text
SPEC / CODE MISMATCH 2
Requirement: REQ-QAAGENT-010 (G5 Runtime TC 검사), NFR-QAAGENT-001
Specification: knowledge/qa-agent-architecture.md 가 "Spec/Not Bug·Document Fix·Inquiry 유형은 요구받지 않으면 Runtime TC를 자동 생성하지 않는다. G5가 검사한다"고 쓴다. 결과 화면도 "Runtime TC 자동생성: 차단"을 보인다.
Current Implementation: gates.py::evaluate_g5 는 종류가 "runtime_tc" 인 판정이 있을 때만 block 을 세운다. analyzer.py::_build_evidence 가 만드는 판정 종류는 issue_analysis·spec_trace·tc_coverage·regression 뿐이다. B 유형 이슈에 AI 가 NEW_TC 를 내도 검사에 걸리지 않는다.
Difference: 이 검사는 운영 경로에서 한 번도 걸리지 않는다. 테스트는 합성 판정으로만 확인한다.
Action: 사용자 확인 필요 (추천: CODE 수정)
```

```text
SPEC / CODE MISMATCH 3
Requirement: REQ-ISSUE-004, REQ-QAAGENT-005 (G1 issue_type_unconfirmed)
Specification: docs/modules/qa-agent.md 3.1절 "후보만 남기고 QA가 고른다", 사용법 화면 "Issue 탭에서 후보를 보고 QA가 판단".
Current Implementation: 분석 폼과 결과 화면에 유형을 고르는 입력이 없다. 유형 확인 필요 이슈는 G1 이 NEED_INPUT 이 되고, 초안 허용 말고는 넘길 방법이 없다. 연구소 검토 결과가 비어 있는 이슈도 모두 여기에 걸린다.
Difference: QA 가 고른 유형을 시스템에 알려 줄 수 없다.
Action: 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH 4
Requirement: REQ-QAAGENT-001 (scope_note), 규칙 4절
Specification: 사용법 화면 4절 "특정 시트나 버전만 보려면 범위를 적으세요. 결과에 '제한된 범위 기준'으로 표시되고, 그 범위 밖은 '확정 불가'로 남습니다."
Current Implementation: analyzer.py 는 scope_note 를 AI 입력과 결과 화면 표시에만 쓴다. 검색 대상(사양 조각·TC)은 줄이지 않고, 범위 밖 항목을 '확정 불가'로 표시하는 코드도 없다.
Difference: 범위를 적어도 전체를 검색한다. 범위 밖 표시는 AI 가 따를 때만 생긴다.
Action: 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH 5
Requirement: REQ-QAAGENT-012
Specification: router.py::approve 설명 "AI 판정을 덮어쓰지 않고 별도 행으로 쌓는다 (규칙 §20)", docs/QA_AGENT_ARCHITECTURE.md 7절 "AI 결과 / QA 수정 결과 / QA 승인·거절을 각각 저장".
Current Implementation: storage.py::save_qa_agent_approval 은 (분석, 종류, 대상)이 같으면 ON CONFLICT DO UPDATE 로 결정·사유를 바꿔 쓴다. 이전 QA 결정은 남지 않는다. 수정률 통계는 처음 결정한 시각(created_at)으로 센다.
Difference: AI 판정은 보존되지만 QA 결정의 변경 이력은 사라진다.
Action: 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH 6
Requirement: REQ-QAAGENT-003, NFR-QAAGENT-001 ("문서 검색 실패를 숨긴 확정 판정" 금지)
Specification: docs/modules/qa-agent.md 7절과 knowledge 파일이 "Gate 차단 이유·읽지 못한 문서를 결과에 남긴다"고 쓴다. analyzer.py 주석도 "읽지 못한 문서는 조용히 빠지면 '사양 없음' 오판이 된다"고 쓴다.
Current Implementation: 읽지 못한 문서는 결과 JSON 의 knowledge_failures 에만 저장된다. 결과 화면(analysis.html)은 이 값을 보여주지 않고, G1·G2 도 이 값을 보지 않는다(등록 문서 수는 읽지 못한 문서까지 센다).
Difference: 사용자는 결과 화면에서 어떤 사양서가 빠졌는지 알 수 없다.
Action: CODE 수정 (결과 화면에 표시, 가능하면 G2 주의 항목으로)
```

```text
SPEC / CODE MISMATCH 7
Requirement: REQ-QAAGENT-012
Specification: router.py::approve 는 기록 뒤 QA Action 탭으로 돌려보내려 한다(`#tab-action`).
Current Implementation: 결과 화면의 탭은 주소의 `?tab=action` 으로 고른다. `#tab-action` 은 아무 탭도 고르지 않아 기본 Issue 탭이 열린다.
Difference: 결정을 기록하면 보던 탭에서 Issue 탭으로 튄다.
Action: CODE 수정
```

```text
SPEC / CODE MISMATCH 8
Requirement: REQ-QAAGENT-005 (G2 split_spec_partial), 규칙 10절
Specification: 사용법 화면 "사양이 여러 권으로 나뉘어 있어도 전체를 하나의 집합으로 본다". 검색은 모든 사양서의 조각을 대상으로 한다.
Current Implementation: gates.py::evaluate_g2 는 "찾은 조각이 나온 사양서 수 < 등록 사양서 수"이면 "사양서 N건 중 M건만 조사했습니다"를 남긴다. 사양 조각 상한이 8개라 여러 권일 때 거의 항상 걸린다.
Difference: 전체를 조사했는데 문장은 일부만 조사했다고 말한다.
Action: 사용자 확인 필요 (추천: 문장을 "근거가 N건 중 M건 문서에서만 나왔습니다"로 고치는 CODE 수정)
```

```text
SPEC / CODE MISMATCH 9
Requirement: REQ-QAAGENT-001
Specification: 사용법 화면 "Issue를 찾을 수 없음 — 서버에 Export 폴더가 없다 → backup.json을 첨부".
Current Implementation: index.html 은 Export 폴더가 없을 때 "Issue ID를 직접 입력하거나 backup.json을 첨부하세요"라며 번호 입력칸을 보인다. 폴더가 없으면 번호만으로는 이슈를 읽을 수 없어 분석이 반드시 FAILED 가 된다.
Difference: 화면이 될 수 없는 방법을 안내한다.
Action: CODE 수정 (폴더가 없을 때 번호 입력칸을 빼거나 안내를 고친다)
```

```text
SPEC / CODE MISMATCH 10
Requirement: REQ-ISSUE-001
Specification: 이름이 "."으로 시작하거나 ".staging-"·".previous-"가 들어간 폴더는 읽지 않는다.
Current Implementation: polarion_issue.py 는 이 규칙을 실행 폴더에만 적용한다. 예전 구조에서 폴더 바로 아래 `backup.json` 이 있는 폴더는 이름을 보지 않고 목록에 넣는다.
Difference: 예전 구조 자리에 `.staging-…` 폴더가 생기면 목록에 든다. 지금 실데이터에서는 확인되지 않았다.
Action: 사용자 확인 필요 (낮은 우선순위)
```

```text
SPEC / CODE MISMATCH 11
Requirement: REQ-QAAGENT-001 (GET /qa-agent/issues)
Specification: 화면 조각은 안전하게 그려야 한다(명시된 약속 없음. 명백한 결함 후보로 보고).
Current Implementation: router.py::issue_list 는 폴더 이름을 HTML 이스케이프 없이 `<option value="…">` 에 넣는다. index.html 은 Jinja2 자동 이스케이프를 쓴다.
Difference: 폴더 이름에 `"`·`<` 가 있으면 조각 HTML 이 깨진다.
Action: CODE 수정 (낮은 우선순위)
```

```text
SPEC / CODE MISMATCH 12
Requirement: REQ-QAAGENT-007, REQ-RULE-004 (문서 사이 불일치)
Specification: docs/modules/qa-agent.md 9절은 models.standard 기본값을 gemini-2.5-flash 로, knowledge/qa-agent-architecture.md 는 규칙 "56개 최상위 절"로 쓴다. docs/QA_AGENT_ARCHITECTURE.md 머리는 "기준: QA 규칙 Rev1.12"다. app/core/qa_rules.py 머리 주석은 규칙이 없으면 "Gate가 NEED_RULES로 보고"한다고 쓴다.
Current Implementation: config.yaml models.standard/complex 는 gemini-3.5-flash 다. 구현 현황표는 Rev1.17 기준 79절이다. 규칙이 없을 때 G1 코드는 `no_rules`, 판정은 BLOCK 이다.
Difference: 문서가 현재 설정·코드보다 뒤처졌다.
Action: SPEC 수정 (이 초안은 현재 설정·코드 값을 적었다. 해당 문서를 고친다)
```

## 3. 매뉴얼 개정 검증과 비용 대시보드 (13건)

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-015
Specification: 사용법 화면 "재검증이라면 이전 리비전을 선택합니다. Round 계보와 이전 QA 지적사항이 자동 연결됩니다." README "Round 계보 추적. QA가 확정하기 전에는 이전 지적사항 상태를 자동 변경하지 않음"
Current Implementation: 지적사항을 저장하는 함수(Storage.add_manual_comment)를 앱 코드 어디에서도 부르지 않는다. 테스트만 직접 부른다. Word Comment 파일을 만들어도 지적사항 표에 남지 않는다.
Difference: 운영에서 "이전 Round 미해결 지적사항" 칸이 항상 비고, 상태 확정 기능을 쓸 수 없다.
Action: 사용자 확인 필요 (미확정의 "지적사항은 어디서 생기는가" 결정 뒤 CODE 수정)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-002 (REQ-USAGE 일일 토큰 한도)
Specification: 비용 대시보드 사용법 "넘으면 새 분석 실행이 429로 차단", docs/COST_OPTIMIZATION.md "새 분석 실행 자체를 차단", knowledge/core-ai-integration.md "새 분석 실행을 시작 전에 429로 차단"
Current Implementation: Regression 분석(impact_analyzer/router.py)과 QA Agent(qa_agent/router.py)는 daily_token_status()로 검사한다. 매뉴얼 개정 검증 start_revision()에는 검사가 없다.
Difference: 한도를 넘어도 매뉴얼 개정 검증은 계속 시작되고 AI를 부른다. 이 기능은 변경마다 여러 번 부르므로 한도 초과 폭이 가장 크다.
Action: CODE 수정
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-012
Specification: README "이미지 변경은 AI가 PASS 처리하지 못하게 막고", docs/modules/manual-review.md "AI가 임의로 PASS 처리하지 않고 항상 사람이 원본 이미지를 직접 확인한다"
Current Implementation: reviewer.py run()은 확신도를 0.6 이하로 낮추고 needs_human_review와 IMAGE_CHANGE_REVIEW_REQUIRED만 붙인다. AI 판정 PASS는 그대로 저장되고 판정 요약에 "문제없음"으로 세며, Word Comment에서도 빠진다(comment_text_for).
Difference: 이미지 변경이 "문제없음"으로 끝날 수 있다.
Action: 사용자 확인 필요 (미확정 결정 뒤 SPEC 또는 CODE 수정)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-017
Specification: 사용법 화면은 구현과 맞아야 한다(AGENTS.md: 기능별 사용법은 앱 화면 안에 둔다)
Current Implementation: guide.html이 "Revision 표기를 입력"하라고 안내하지만 입력 칸은 제품 버전이고 리비전 표기는 자동으로 만든다. "준비 중: Cross-Manual 영향분석, 이미지 변경 Human Review Gate"라고 적었지만 둘 다 구현돼 있다(cross_manual.py, reviewer.py). 매뉴얼 서버 연동 안내도 없다.
Difference: 사용법 화면이 옛 상태를 설명한다.
Action: CODE 수정 (guide.html 문구)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-014
Specification: 사용법 화면 "QA가 판정을 변경하고 사유를 남기면 AI 원본과 QA 결정을 함께 보존합니다." 판정 값은 8개(schemas.ManualJudgment)
Current Implementation: router.py set_qa_decision()이 qa_decision 값을 검사하지 않는다. 아무 글자나 저장되고, 목록 첫 줄("QA 판정 변경...", 빈 값)을 고르고 저장하면 QA 판정이 지워진다. 다른 두 확정 기능(set_cross_manual_status, set_comment_status)은 허용 값을 검사한다.
Difference: 잘못된 값이 Word Comment 기준 판정으로 쓰일 수 있다(빈 값이 아닌 모르는 코드면 Comment가 "[코드] 검토가 필요합니다."로 들어간다).
Action: CODE 수정 (허용 값 검사. 빈 값의 뜻은 사용자 확인 필요)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-005
Specification: docs/modules/manual-review.md 처리 흐름 "구조화 추출: 삽입·삭제·서식 변경을 항목 단위로 분해"
Current Implementation: docx_track_changes.py는 w:ins, w:del, w:moveFrom, w:moveTo만 본다. 서식 변경 표시(w:rPrChange, w:pPrChange)는 읽지 않는다.
Difference: 서식 변경은 목록에 나오지 않는다.
Action: 사용자 확인 필요 (추천: 문서에서 "서식 변경" 삭제)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-016
Specification: comment_writer.output_filename() 설명 "스펙 §25 예시(예: 'VXvue Service Manual.V1.1.0W2_KO_AI검토.docx')와 같은 형태로 만든다" (§25는 원본 요구 문서의 25절)
Current Implementation: 실제 리비전 표기는 "V1.1.0 · W2"라서 공백만 지우면 "VXvue Service Manual.V1.1.0·W2_KO_AI검토.docx"가 된다. 테스트(test_output_filename_matches_spec_convention)는 가운뎃점이 없는 "V1.1.0 W2"로 확인해 차이를 잡지 못한다.
Difference: 파일 이름에 가운뎃점(·)이 남는다.
Action: CODE 수정 (가운뎃점도 지우고, 테스트는 _revision_label()이 만든 실제 표기로 확인)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-002
Specification: 요청 검사가 모두 끝난 뒤에 저장을 시작한다(검사에 실패한 요청은 아무것도 남기지 않는다)
Current Implementation: start_revision()이 storage.ensure_version(product, target_version)을 이전 검증 검사보다 먼저 부른다.
Difference: 400으로 거절된 요청의 제품 버전이 Knowledge 버전 목록에 남는다.
Action: CODE 수정
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-MANUAL-011
Specification: schemas.VisualVerificationStatus 설명 "QA가 UI에서 수동으로 갱신할 수 있다"
Current Implementation: 근거의 시각 확인 상태를 바꾸는 화면이나 요청 주소가 없다. 항상 NOT_VERIFIED로 보인다.
Difference: 약속한 수동 갱신 기능이 없다.
Action: 사용자 확인 필요 (기능을 만들지, 설명을 지울지)
```

```text
SPEC / CODE MISMATCH
Requirement: NFR-MANUAL-001
Specification: docs/modules/manual-review.md 설정 표 "services.manual_hub.api_url 기본값 (빈 값). 비면 연동 비활성"
Current Implementation: config.yaml에 통합 배포 주소가 기본값으로 들어 있다. 비밀 값 두 개가 없으면 꺼지는 동작은 같다.
Difference: 문서의 기본값 칸이 실제 설정과 다르다.
Action: SPEC 수정 (모듈 문서의 기본값 칸)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-COST-001
Specification: QA Agent 기록도 대시보드에 기능 이름으로 보여야 한다(qa_agent/templates/guide.html "비용 대시보드에서 호출 수·토큰·캐시 적중을 집계해서 볼 수 있습니다")
Current Implementation: cost_dashboard/router.py MODULE_LABELS에 impact_analyzer와 manual_review만 있다. QA Agent 분석(module="qa_agent")은 "qa_agent"라는 코드로 보인다. 오늘 사용량 칸 안내도 "Regression 영향 분석과 매뉴얼 개정 검증이 같은 analyses 테이블에 기록되므로 두 기능의 사용량을 합산"이라고 QA Agent를 빠뜨린다.
Difference: 표시 이름과 안내 문구가 기능 목록과 다르다.
Action: CODE 수정
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-COST-004
Specification: 비용 대시보드 사용법 "지표 읽는 법" 표의 "API 호출 수: 실제로 Gemini에 요청한 횟수", docs/COST_OPTIMIZATION.md와 README "/cost-dashboard에서 호출 수·토큰·캐시 적중을 집계"
Current Implementation: 대시보드에 실제 호출 수 합계가 없다. 최근 분석 표의 "캐시 Hit/호출"은 캐시 적중을 포함한 시도 수이고, 기간 합계는 캐시 Hit율의 분모("Gemini 호출 M건 기준")로만 보인다. 같은 표에 "이 시스템은 분석 1건당 1회가 원칙"이라고 적었지만 매뉴얼 개정 검증은 변경마다 1~2회 부른다.
Difference: 사용법이 설명하는 지표가 화면에 없고, 호출 원칙 설명이 매뉴얼 개정 검증과 맞지 않는다.
Action: 사용자 확인 필요 (대시보드에 기능별 실제 호출 수를 더할지, 사용법 문구를 고칠지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-COST-001
Specification: 최근 분석 표의 첫 열 제목 "완료 시각(UTC)"
Current Implementation: 표에 넣는 값은 작업을 만든 시각(analyses.created_at)이다. 기간 필터와 날짜 묶음도 만든 시각 기준이다.
Difference: 오래 걸린 분석은 완료 시각과 다른 값이 보인다. 자정 무렵 시작한 분석은 전날로 묶인다.
Action: 사용자 확인 필요 (열 이름을 "시작 시각(UTC)"으로 고칠지, 완료 시각을 쓸지)
```

## 4. VXvue 일일 QA 점검 (7건)

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-001 (기존 SPEC 4절 "어디서 끊기면" 표와 REQ-DAILY-001 예시)
Specification: Polarion 조회가 실패하면 사양 변경 영향 검토·이슈 수정확인 초안은 "실패" 로 남는다.
Current Implementation: pipeline._run 은 SRS·이슈 수집이 실패하면 작업 묶음을 만들지 않고, 두 점검 단계를 stages.setdefault(key, _stage("skipped", "입력이 없습니다 (변경·신규 항목 없음).")) 로 남긴다.
Difference: 수집 실패인데 두 점검이 "건너뜀 / 변경·신규 항목 없음" 으로 보인다. 사람은 오늘 바뀐 것이 없다고 오해할 수 있다. 실패는 SRS 수집·이슈 수집 단계에만 보인다. 자동 테스트(test_polarion_failure_does_not_stop_trace_gap)는 이 부분을 보지 않는다.
Action: CODE 수정 (수집 실패 때 두 점검 단계를 "실패" 또는 "수집 실패로 건너뜀" 으로 남긴다). 초안의 REQ-DAILY-001 예시는 SRS 수집 단계 기준으로 고쳐 적었다. 사용자 확인 필요.
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-001 / 기존 SPEC 4절 "어디서 끊기면" 표
Specification: Claude 토큰이 없으면 AI 단계가 "건너뜀" 으로 남고 이유가 메일과 audit.json 에 남는다.
Current Implementation: scheduled_jobs.launch_detached 는 Polarion 설정과 Claude 토큰 가운데 하나라도 없으면 점검을 띄우지 않는다(if not (cfg.polarion.configured and cfg.claude_token)). 모듈 설명문은 "둘 다 없는 호스트에서 건너뛴다" 고 적어 코드와도 다르다.
Difference: 운영 서버에서 토큰이 만료·누락되면 예약 점검이 아예 돌지 않는다. 실행 기록도 메일도 없고, 사양–TC 연결 점검처럼 AI 가 필요 없는 점검도 멈춘다. 이유는 앱 로그(daily_qa_skipped)에만 남는다. "토큰 없음 → AI 단계 건너뜀" 은 CLI 로 직접 돌릴 때만 성립한다.
Action: 사용자 확인 필요 (추천: Polarion 설정만 있으면 띄우고 AI 단계만 건너뛰게 CODE 수정. 아니면 SPEC 4절 표를 "예약 실행이 돌지 않는다" 로 수정)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-003 / NFR-DAILY-001 (상한 초과 미룸), REQ-DAILY-016 (작업 실패)
Specification: 상한을 넘는 묶음은 "다음 실행으로 미룸" (단계 비고 문구).
Current Implementation: 사양 변경 영향 검토의 작업은 오늘 스냅샷과 전날 스냅샷의 차이로만 만든다. 오늘 스냅샷은 작업 성공 여부와 상관없이 저장된다(pipeline._run 2단계).
Difference: 상한 때문에 미룬 묶음, 두 번 다 실패한 묶음의 SRS 변경은 다음 실행의 비교에 다시 나오지 않는다. "다음 실행으로 미룸" 이라는 비고와 달리 영원히 검토되지 않는다.
Action: CODE 수정 (미룬·실패한 SRS 변경을 상태 값에 남겨 다음 실행에 다시 넣는다. 또는 비고를 "이번에 검토하지 못함" 으로 바꾸고 사람에게 알린다). 사용자 확인 필요.
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-002 (이슈 기준 시각), REQ-DAILY-004
Specification: 마지막 성공 실행 이후 바뀐 이슈만 새 이슈로 본다.
Current Implementation: 이슈 수정확인 초안 단계가 "일부 실패" 여도 기준 시각을 옮긴다(stage_result["status"] in ("ok", "partial")). 상한으로 묶음을 미룬 날은 기준 시각을 옮기지 않고, 이 점검은 중복 검사를 하지 않는다(_save 의 dedupe 는 사양 변경 영향 검토만).
Difference: (1) 실패한 묶음의 이슈는 다음 날 다시 읽히지 않아 검토에서 빠진다. (2) 묶음을 미룬 다음 날에는 이미 처리한 이슈까지 다시 보내 같은 Finding 과 초안이 두 번 생기고 비용도 두 번 든다.
Action: CODE 수정 (기준 시각을 처리에 성공한 이슈 기준으로 옮기거나, 이슈 번호로 중복을 거른다). 사용자 확인 필요.
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-018 (기존 REQ-DAILY-005 마지막 문단의 중복 방지)
Specification: 같은 SRS·같은 판정의 Finding 이 열려 있으면 새로 만들지 않는다. 한편 사양 변경 영향 검토 Skill 은 "TC 별로 Finding 하나" 를 요구하고, 삭제 SRS 계산(removed_srs_findings)과 사양–TC 연결 점검의 "삭제된 SRS 참조" 는 TC 행마다 Finding 을 만든다.
Current Implementation: daily_qa_has_open_finding(skill, subject, verdict) 는 대상 TC(tc_ref)를 보지 않는다. 같은 실행 안에서도 첫 Finding 이 저장된 뒤에는 같은 SRS·같은 판정의 다른 TC Finding 이 모두 버려진다.
Difference: 예를 들어 VP-10 을 가리키는 TC 세 개가 모두 "수정 필수" 면 첫 TC 하나만 저장되고 두 TC 는 검토 대기열에 나타나지 않는다. 메일 건수에도 빠진다.
Action: 사용자 확인 필요 (추천: 같은 기록인지 가리는 기준에 대상 TC 위치를 넣도록 SPEC 과 CODE 를 함께 수정)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-DAILY-007
Specification: 이슈를 닫거나 기존 TC 를 덮어쓰라는 조치를 담은 Finding 을 버린다.
Current Implementation: FORBIDDEN_ACTION_RE 가 조치 글에서 "close", "닫" 같은 글자 조각만 찾는다.
Difference: "닫기 버튼 동작을 확인하는 TC 보강 검토", "Closed 상태 이슈 목록 TC 보강" 처럼 화면 이름에 그 글자가 들어간 정상 조치도 버려진다. 버린 이유는 audit.json 에만 남아 사람이 알아채기 어렵다.
Action: CODE 수정 (종료·덮어쓰기를 "하라는" 표현만 잡도록 좁히고, 걸린 예를 테스트로 남긴다). 사용자 확인 필요.
```

```text
SPEC / CODE MISMATCH
Requirement: 기존 SPEC 4절 "어디서 끊기면" 표 (사용량 한도)
Specification: Claude 사용량 한도로 실패하면 이유가 메일과 audit.json 에 남는다.
Current Implementation: 이유 문장(error_text)은 audit.json 과 claude_logs/ 에만 남는다. 메일의 단계 비고는 "N개 작업 모두 실패 (audit.json 참고)" 다.
Difference: 메일만 보는 사람은 실패 이유(사용량 한도)를 알 수 없다.
Action: SPEC 수정 (메일에는 "audit.json 참고" 로 위치만 알린다) 또는 CODE 수정 (첫 실패 이유를 한 줄 요약해 비고에 붙인다). 사용자 확인 필요.
```

## 5. 공통 기반: 설정·저장·AI 호출·Knowledge·배포·동기화 (12건)

```text
SPEC / CODE MISMATCH
No: 1
Requirement: REQ-KNOW-001, REQ-KNOW-005, REQ-KNOW-013
Specification: /knowledge 의 삭제는 확인 창을 거친다("되돌릴 수 없습니다"). `지금 수집` 은 결과를 알림 창으로 보인다. `죽은 등록 정리` 버튼이 동작한다. 제품 필터가 목록을 거른다(화면 문구·사용법 화면·knowledge 파일의 약속).
Current Implementation: app/modules/knowledge/templates/knowledge.html 154~156행과 172~176행의 JS 작은따옴표 문자열 안에 줄바꿈 문자가 그대로 들어 있다(`.join('` 다음 줄 `')`, `notes.push('` 다음 줄 `[중복 확인 필요]`). <script> 블록 전체를 떼어 Node 로 문법 검사하면 "Invalid or unexpected token" 이 난다. 블록이 하나라서 모든 스크립트가 실행되지 않는다.
Difference: 삭제 버튼을 누르면 확인 없이 바로 원본 파일까지 지워진다. `지금 수집` 은 브라우저가 JSON 응답 페이지로 이동한다. `죽은 등록 정리`·제품 필터·버전 자동 완성은 아무 일도 하지 않는다. tests/test_web.py 는 제목·링크만 봐서 잡지 못한다.
Action: CODE 수정 (문자열의 줄바꿈을 `\n` 으로 되돌리고, 화면 스크립트 문법을 검사하는 테스트를 더한다)
```

```text
SPEC / CODE MISMATCH
No: 2
Requirement: REQ-KNOW-007, REQ-KNOW-012, REQ-SYNC-002
Specification: 지식 폴더에서 바뀐 파일은 sha256 비교로 다시 수집·업로드되고 분석에 반영된다(knowledge/product-knowledge.md "sha256 비교로 변경된 파일만 재수집한다", REQ-SYNC-002 1~3단계). 파싱 저장본은 "같은 문서 번호의 원본은 바뀌지 않는다"를 전제로 한다(app/core/document_cache.py 머리 주석).
Current Implementation: 리비전 표시가 없는 이름(예: `(TC) RA16-148-002_VXvue_TestCase.xlsx`)의 내용이 바뀌면, store_asset(서버) 또는 sync_product(서버 폴더 수집)가 같은 경로 `original/<종류>/<이름>` 을 새 내용으로 덮어쓴다. register_collected 는 그 경로가 이미 등록돼 있으므로 "미변경"으로 두고, 문서 번호·부가 정보의 sha256·파싱 저장본을 그대로 둔다. document_cache.delete 를 부르는 곳은 삭제·정리 경로뿐이다.
Difference: 서버의 TC 파일은 새 내용인데 Regression 분석·QA Agent 는 옛 TC 목록(파싱 저장본)으로 계속 판정한다. 화면의 "마지막 수집" 시각은 새로워서 사람이 알아채기 어렵다. 코드를 읽고 찾은 것이며 실행으로 재현하지 않았다.
Action: CODE 수정 (등록 때 부가 정보의 sha256 과 파일 sha256 이 다르면 파싱 저장본을 지우고 sha256 을 갱신한다. 재현 테스트를 먼저 더한다)
```

```text
SPEC / CODE MISMATCH
No: 3
Requirement: REQ-SYNC-002
Specification: 읽지 못하는 새 판이 읽을 수 있는 옛 판을 밀어내지 않는다. 결과는 PARTIAL, 종료 코드 1 (이유: 사라진 문서는 어디에도 드러나지 않는다).
Current Implementation: PC 의 sync_product 가 새 판 텍스트 추출에 실패하면 수집 기록에 error 를 남긴다. 같은 실행에서 다른 파일이 하나라도 수집되면 상태는 PARTIAL 이라 업로드가 이어진다. push_product(app/core/knowledge_push.py 70행)는 error 자산을 올릴 목록에서 뺀다. 서버 commit 은 그 문서의 새 판도 옛 판도 목록에 없으므로 _prune_orphans 로 옛 판 파일을 지운다. _keep_previous_readable 은 목록 안의 error 기록만 보므로 동작하지 않는다. commit 결과는 SUCCESS, push_product 는 업로드 실패가 없으면 commit 상태를 그대로 쓰므로 SUCCESS, 종료 코드 0 이다. 이튿날 PC 의 "미변경" 분기는 지난 기록의 error 를 잇지 않는다.
Difference: 2026-09-29 재현과 같은 결과(쓸 수 있는 문서 0건)가 PC 쪽 실패로는 막히지 않고, 작업 스케줄러에는 성공으로 보인다. 사양서1~5 가 같은 날 함께 바뀌는 흐름에서 생길 수 있다. tests/test_knowledge_upload.py 는 서버 쪽 실패만 흉내 낸다. 코드를 읽고 찾은 것이며 실행으로 재현하지 않았다.
Action: CODE 수정 (PC 가 error 자산도 목록에 넣어 보내거나, 서버가 "PC 가 읽지 못함" 표시를 받아 이전 판을 유지한다. PC 수집 PARTIAL 을 최종 상태에 반영한다) / 사용자 확인 필요 (REQ-SYNC-002 문장을 "서버 또는 PC 가 읽지 못하면"으로 넓힐지)
```

```text
SPEC / CODE MISMATCH
No: 4
Requirement: REQ-USAGE-002
Specification: `analysis.daily_token_limit` 을 넘으면 새 분석 실행이 차단된다(config.yaml 주석, knowledge/core-ai-integration.md "새 분석 실행을 시작 전에 429로 차단", docs/COST_OPTIMIZATION.md). 기능을 가리지 않는다.
Current Implementation: Regression 분석(app/modules/impact_analyzer/router.py start_analysis)과 QA Agent(app/modules/qa_agent/router.py start_analysis)만 daily_token_status 를 확인한다. 매뉴얼 개정 검증(app/modules/manual_review/router.py)은 동시 작업 수만 확인한다.
Difference: 한도를 넘어도 매뉴얼 개정 검증은 Gemini 를 계속 부른다(변경 건마다 여러 번 부르는 기능이다).
Action: 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH
No: 5
Requirement: REQ-USAGE-002
Specification: "오늘" 누적 토큰(화면 배너·429 문구·사용법 화면 "하루 누적 토큰").
Current Implementation: app/modules/impact_analyzer/router.py::_today_start_iso 가 UTC 0시를 쓴다.
Difference: 한국 시간 09:00 에 사용량이 0 으로 돌아간다. 00:00~09:00 KST 사용분은 "전날"에 들어간다.
Action: 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH
No: 6
Requirement: REQ-CONF-001, REQ-AICALL-003, REQ-STORE-001
Specification: 설정은 config.yaml 하나이고 코드에 값을 하드코딩하지 않는다(knowledge/core-architecture.md). config.yaml 에 `analysis.max_retries: 3`, `retry_min_seconds: 1`, `retry_max_seconds: 10`, `storage.database_url: "sqlite:///data/app.db"` 가 있다.
Current Implementation: app/core/gemini_client.py 의 @retry 가 stop_after_attempt(3), wait_exponential(min=1, max=10) 을 코드에 박아 둔다. Storage 와 scripts/backup_data.py 는 `data/app.db` 를 고정 경로로 쓴다. 네 키와 `app.locale` 을 읽는 코드가 없다.
Difference: 이 키를 바꿔도 동작이 바뀌지 않는다. 운영자는 바뀐 줄로 믿는다.
Action: 사용자 확인 필요 (미확정의 결정 필요 항목)
```

```text
SPEC / CODE MISMATCH
No: 7
Requirement: REQ-KNOW-001, REQ-KNOW-017, REQ-SYNC-001, REQ-SYNC-002
Specification: 사양서 동기화는 평일 09:40, 지식 폴더 업로드는 평일 10:00 에 `--upload-to http://<서버 주소>:24357` 로 돈다(SPEC 4절, REQ-SYNC-001, 002).
Current Implementation: 화면·문서의 안내가 다르다.
  - app/modules/knowledge/templates/knowledge.html "ALM 사양서 동기화 상태": "매주 월요일 07:30(KST)에"
  - app/modules/knowledge/templates/guide.html 2절: `--report-to http://서버:12000`, "매주 자동 수집", `--upload-to` 안내 없음
  - knowledge/core-deployment.md: nginx `/` → "핵심 앱(:12000)"
  - docs/PRODUCT_ONBOARDING.md 6절: schtasks 예시 `/SC WEEKLY /D MON /ST 07:45`
  - docs/DEPLOYMENT.md 6절: 사양서 동기화를 "매일 등록"
Difference: 사용자가 화면을 보고 옛 포트·옛 명령으로 수동 실행하면 연결에 실패하거나 서버 대신 로컬 DB 에 등록한다.
Action: CODE 수정 (두 템플릿) / 문서 수정 (knowledge·docs 세 파일)
```

```text
SPEC / CODE MISMATCH
No: 8
Requirement: REQ-AICALL-002, REQ-AICALL-003
Specification: 상위 등급 모델을 쓰지 못해 기본 모델로 물러난 경우 조용히 바꾸지 않고 audit 에 남긴다(app/core/gemini_client.py 주석 "물러났다는 사실은 audit 에 남는다").
Current Implementation: 대신 쓴 모델로 받은 응답(또는 추론을 켜고 받은 응답)을 처음 요청한 모델 이름이 든 기준으로 ai_cache 에 저장한다. 다음 같은 요청은 저장본 적중으로 끝나고, model_fallback·thinking_override 가 비어 있으며 last_model 은 요청한 모델 이름이다.
Difference: 두 번째부터는 감사 화면에 "요청 모델로 답했다"고 보인다. 실제로 답한 모델이 기록에서 사라진다.
Action: CODE 수정 (저장본에 쓴 모델과 대신 쓴 기록을 함께 저장하고 꺼낼 때 되살린다)
```

```text
SPEC / CODE MISMATCH
No: 9
Requirement: REQ-KNOW-012, REQ-SYNC-002
Specification: 원본 파일이 사라진 등록은 자동 정리한다(사용법 화면 구버전 정리 표, knowledge/product-knowledge.md).
Current Implementation: 문서 하나가 지식 폴더에서 통째로 빠지면 commit 의 _prune_orphans 가 서버 파일은 지운다. register_collected 는 같은 논리 문서 이름의 수집본이 있을 때만 기존 등록을 비교하므로 그 등록은 문서 표에 남는다.
Difference: 죽은 등록이 생겨 분석마다 "읽지 못한 문서"에 뜬다. `죽은 등록 정리` 버튼으로만 지울 수 있는데 그 버튼은 MISMATCH 1 때문에 동작하지 않는다.
Action: CODE 수정 (commit 이 지운 파일을 가리키는 등록도 함께 지운다)
```

```text
SPEC / CODE MISMATCH
No: 10
Requirement: REQ-KNOW-016
Specification: 앱은 시작할 때 재시작에 끊긴 RUNNING 작업을 정리하므로 강제 종료돼도 상태가 깨지지 않는다(deploy/systemd 주석, docs/DEPLOYMENT.md 5절). 동기화 기록은 "언제 최신화됐나"를 보여 준다.
Current Implementation: lifespan 은 analyses 의 RUNNING 만 정리한다. sync_log 의 RUNNING 줄은 남고, is_sync_running 은 같은 제품·종류의 마지막 줄만 보므로 그 뒤의 `지금 수집`·업로드 확정·예약 실행이 모두 409 또는 건너뜀이 된다. 시간 제한이 없다.
Difference: 업로드 확정 중에 서버가 재시작되면 그 제품의 지식 업로드가 사람이 DB 를 고칠 때까지 매일 409 로 실패한다(작업 스케줄러는 1 로 끝나 드러나기는 한다).
Action: CODE 수정 (시작 때 sync_log RUNNING 도 FAILED 로 닫는다) / 사용자 확인 필요
```

```text
SPEC / CODE MISMATCH
No: 11
Requirement: REQ-STORE-001
Specification: "SQLite WAL과 단일 프로세스는 현재 부하에 적합하다"(docs/SHARED_PLATFORM_ARCHITECTURE.md DB 확장 원칙 4).
Current Implementation: 어디에서도 `PRAGMA journal_mode=WAL` 을 설정하지 않는다. 일일 QA 점검은 웹 프로세스와 다른 프로세스(scripts/run_daily_qa.py)로 같은 data/app.db 에 쓴다.
Difference: 문서는 WAL 을 전제로 하지만 기본 모드로 돈다. 두 프로세스가 동시에 쓰면 "database is locked" 가 날 수 있다(재현하지 않음).
Action: 사용자 확인 필요 (WAL 을 켤지, 문서를 고칠지)
```

```text
SPEC / CODE MISMATCH
No: 12
Requirement: REQ-SYNC-002
Specification: PARTIAL 이면 읽지 못한 파일과 대신 유지한 파일을 결과에 적는다. CLI 는 "읽지 못해 이전 판 유지" 줄을 찍는다(scripts/sync_product_knowledge.py 281행).
Current Implementation: push_product 의 반환값에 commit 결과의 kept_previous·failures 가 들어가지 않는다. 업로드 모드에서는 그 줄이 한 번도 찍히지 않는다. 상세 문구에는 서버 확정 상세가 붙어 "이전 판 유지 N건: …"(3건까지)가 보이고, 읽지 못한 파일은 건수만 보인다.
Difference: 작은 차이다. 운영자는 상세 문구를 끝까지 읽어야 무엇을 유지했는지 안다.
Action: CODE 수정
```

## 6. 하위 서비스 QA Manual Hub (11건)

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBAUTH-003 세션 유지와 자동 연장
Specification: 사용 안내서 1.1 "브라우저를 계속 쓰고 있으면 자동으로 연장됩니다." 지식 파일 "사용 중이면 자동 연장."
Current Implementation: deps.get_current_user 는 15분마다 DB 의 expires_at 만 늘린다. 쿠키는 create_session 에서 max_age=8시간으로 한 번만 보내고 다시 보내지 않는다.
Difference: 브라우저가 로그인 8시간 뒤 쿠키를 버리므로, 계속 써도 8시간이 되면 로그인 화면으로 돌아간다. DB 연장은 효과가 없다.
Action: CODE 수정 (연장할 때 같은 토큰으로 쿠키를 다시 내려보내거나, max_age 없는 쿠키로 바꾼다)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUB-016 API 문서 화면
Specification: README 문서 표 "`/api/docs` | 로그인 후 접근 가능한 OpenAPI 문서"
Current Implementation: main.py 가 FastAPI(docs_url="/api/docs", openapi_url="/api/openapi.json") 로 인증 없이 연다.
Difference: 로그인하지 않은 사람도 전체 API 목록과 입력 형식을 볼 수 있다.
Action: 사용자 확인 필요 (문서 공개를 허용할지, 로그인 뒤로 옮길지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBAUTH-013 감사 기록 남기기 (IP)
Specification: 감사 기록에 요청한 사람의 IP 를 남긴다 (README 보안 표, 사용 안내서 1.11).
Current Implementation: audit.client_ip 가 X-Forwarded-For 의 첫 값을 쓴다. nginx 는 proxy_add_x_forwarded_for 로 사용자가 보낸 X-Forwarded-For 뒤에 실제 주소를 덧붙인다.
Difference: 사용자가 X-Forwarded-For 머리글을 직접 넣으면 감사 기록과 로그인 이력의 IP 를 원하는 값으로 바꿀 수 있다.
Action: CODE 수정 (nginx 가 넣는 X-Real-IP 나 마지막 값을 쓴다)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBAUTH-013 감사 기록 남기기 (제품·분류 생성)
Specification: 사용 안내서 1.11 "제품·분류 생성·수정" 이 기록된다. 지식 파일 "25종 이벤트를 기록".
Current Implementation: cli.cmd_seed_catalog 는 분류 10종과 제품을 만들면서 감사 기록을 남기지 않는다. bootstrap-admin 과 reset-password 는 남긴다.
Difference: CLI 로 만든 제품·분류는 Audit Logs 에 생성 기록이 없다.
Action: 사용자 확인 필요 (CLI 시드도 PRODUCT_CREATE / CATEGORY_CREATE 를 남길지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-010 복구
Specification: README·지식 파일 "현재 상태를 backup/pre-restore-<timestamp>/ 에 먼저 백업 - 되돌릴 수 있음", 사용 안내서 3.7 "스크립트는 안전 절차를 강제합니다."
Current Implementation: restore.sh 는 안전 백업의 pg_dump 나 tar 가 실패해도 warn 만 출력하고 DB 삭제와 복원을 계속한다.
Difference: 안전 백업이 없는 채로 지금 데이터를 지울 수 있다. 그러면 잘못 복구했을 때 되돌릴 방법이 없다.
Action: CODE 수정 (안전 백업 실패 시 멈춘다)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-010 복구 (manifest 확인)
Specification: README "manifest 가 있어 DB 덤프와 파일 세트가 어긋난 조합으로 복구되는 일을 방지합니다."
Current Implementation: restore.sh 는 manifest 의 backup_at, storage_file_count 두 줄만 화면에 보인다. SHA-256 값은 확인하지 않고, manifest 가 없어도 진행한다.
Difference: 다른 백업의 storage.tar.gz 가 섞였거나 파일이 손상돼도 알아채지 못한다. 방지는 사람이 눈으로 보는 것에 달려 있다.
Action: 사용자 확인 필요 (restore.sh 에서 sha256sum -c 로 확인할지, 문구를 "사람이 확인한다"로 고칠지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-009 백업 (자동 실행)
Specification: README "자동 실행 (/etc/cron.d/qa-manual-hub-backup): 30 2 * * * ...", 지식 파일 "자동 실행: /etc/cron.d/qa-manual-hub-backup (기본 매일 02:30)".
Current Implementation: install.sh, deploy.sh 어디에서도 cron 파일을 만들지 않는다. 저장소에 cron 파일도 없다.
Difference: 설치 절차를 그대로 따르면 자동 백업이 켜지지 않는다. 문서는 이미 있는 것처럼 쓴다.
Action: 사용자 확인 필요 (install.sh 가 cron 파일을 만들지, 문서에 수동 설치 단계를 넣을지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-012 Docker Compose 로 실행
Specification: README "Docker Compose 로 실행 ... docker compose up -d --build", 지식 파일 "깨끗한 호스트나 로컬 개발 환경에서 가장 빠른 경로".
Current Implementation: docker-compose.yml 이 deploy/Dockerfile 과 deploy/nginx/docker.conf 를 가리키지만 둘 다 저장소에 없다(git ls-files 확인). 또 PASSWORD_MIN_LENGTH 기본값을 8 로 넣어 다른 설치 형태의 기본값 1 과 다르다.
Difference: 문서의 명령이 빌드 단계에서 실패한다.
Action: 사용자 확인 필요 (빠진 두 파일을 추가할지, Compose 경로를 문서에서 내리고 deprecated 로 둘지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-001 서버 설치 (DB 계정)
Specification: README·지식 파일 "전용 DB 와 전용 role 생성. 이미 있으면 그대로 사용하고 절대 초기화하지 않습니다", "모두 추가 작업만 수행합니다".
Current Implementation: install.sh 는 role 이 이미 있으면 ALTER ROLE ... PASSWORD 로 비밀번호를 .env 값으로 바꾼다. .env 가 없으면 새 무작위 값으로 바꾼다. (사용 안내서 3.8 은 이 동작을 복구 방법으로 쓴다.)
Difference: 같은 이름의 role 을 다른 곳에서 쓰고 있었거나 .env 를 잃은 뒤 다시 실행하면 기존 비밀번호가 바뀐다. "추가 작업만" 약속과 다르다.
Action: 사용자 확인 필요 (지금 동작을 사양으로 인정하고 README 를 고칠지, .env 가 없을 때는 기존 role 비밀번호를 건드리지 않게 할지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-006 저장소 점검
Specification: README·지식 파일 "SHA-256 은 경고와 무결성 검증에만 씁니다."
Current Implementation: SHA-256 은 업로드 때 계산해 같은 내용 경고에만 쓴다. check-storage 는 파일 존재와 크기만 비교한다. 저장된 파일의 SHA-256 을 다시 계산하는 곳이 없다.
Difference: 크기가 같은 채 내용이 손상된 파일은 찾지 못한다.
Action: 사용자 확인 필요 (check-storage 에 SHA-256 비교 옵션을 넣을지, 문구에서 "검증"을 뺄지)
```

```text
SPEC / CODE MISMATCH
Requirement: REQ-HUBOPS-009 백업 (저장소 위치)
Specification: README 설정 표 "STORAGE_ROOT | 문서 파일 저장 루트" (바꿀 수 있는 값으로 안내).
Current Implementation: backup.sh 와 restore.sh 는 저장소를 $DATA_ROOT/storage 로 고정해서 묶고 푼다. restore.sh 의 점검만 .env 의 STORAGE_ROOT 를 읽는다.
Difference: STORAGE_ROOT 를 다른 곳으로 바꾸면 백업에 문서 파일이 빠지고, 복구가 엉뚱한 폴더에 푼다.
Action: 사용자 확인 필요 (스크립트가 STORAGE_ROOT 를 읽게 할지, "STORAGE_ROOT 는 <DATA_ROOT>/storage 여야 한다"를 제약으로 둘지)
```
