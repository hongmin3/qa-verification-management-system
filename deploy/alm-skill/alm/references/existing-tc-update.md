# 등록된 Checklist TC를 최신 원본에 맞춰 갱신하기

검색어: 기존 등록본 갱신, Old ID 대조, Test Steps, Test Data, SOAP, setTestSteps, Full Function, TC위치, Main TC.

사용자가 이미 ALM에 등록한 Checklist TC를 최신 Checklist와 비교해 고쳐 달라고 할 때, 또는 Main TC로 병합된 TC의 분류 필드를 채워 달라고 할 때 읽는다. 신규 등록은 [Checklist 일괄 등록](checklist-registration.md)을 따른다. 제품 TC의 의미 판단은 제품 스킬이 맡는다.

## 1. 원본 판 확인

1. 사용자가 원본을 갱신했다고 하면 제품 스킬의 상태 조회에서 대상 문서 해시가 실제로 바뀌었는지 확인한다. 바뀌지 않았으면 지식 동기화가 끝났는지 확인하고 다시 조회한다. 이전 판으로 대조를 시작하지 않는다.
2. 사용자 PC 사본·다운로드 폴더의 중간 판·서버 판은 해시로 구분한다. 같은 판임을 확인한 사본만 셀 단위 비교에 쓴다.

## 2. 대응과 비교

1. 대응 키는 Checklist의 TC ID와 ALM의 Old ID다. 양쪽 개수, 한쪽에만 있는 ID, 중복 ID를 먼저 보고한다. 1:1이 아니면 쓰기 전에 사용자에게 알린다.
2. 비교 필드는 등록 때의 A~K 매핑과 같다. Test Steps의 Step·Expected는 HTML(`<br/>`, 태그, 엔티티)을 글로 바꾼 뒤 비교한다. 줄바꿈 형식(`\r\n`)·줄 끝 공백·NBSP는 정규화한다. 그 밖의 차이는 실제 차이로 센다.
3. 차이마다 원인을 나눈다.
   - 원본 보강: 체크리스트 쪽을 반영한다.
   - 원본 오류로 보이는 차이(ALM 쪽 철자가 맞는 오탈자 등): 반영하지 않고 위치와 함께 사용자에게 알린다.
   - 저장소가 표현하지 못하는 문자: 5절에 따라 따로 보고한다.
4. 변경 전 값(속성·Test Steps 원문 HTML)을 백업한다. 1건을 시험 저장하고 재조회로 일치를 확인한 뒤 나머지를 처리한다. 끝나면 문서 전체를 다시 수집해 대상 TC의 일치와 대상 외 TC의 무변경을 함께 확인한다.

## 3. REST가 제공하지 않는 필드

운영 ALM(2026-11 확인)에서 REST `/workitems/{id}/teststeps`는 404였고, `fields[workitems]=@all`에도 Test Steps가 나오지 않았다. Test Data는 값이 있을 때만 속성에 나온다. 이 필드들은 같은 개인 PAT로 SOAP를 사용한다. 토큰은 메모리에서만 쓰고 출력·저장하지 않는다.

| 작업 | 서비스 · 요소 | 확인 |
|---|---|---|
| 세션 시작 | `SessionWebService` `logInWithToken`: `mechanism=AccessToken`, `username` 공란, `token`=PAT | 응답 헤더의 `sessionID` |
| Step 조회 | `TestManagementWebService` `getTestSteps`: `workitemURI` | `TestStep`마다 `values/Text` 2개(description, expectedResult) |
| Step 저장 | `TestManagementWebService` `setTestSteps`: `workItemURI`(대문자 I), 그 아래 `testSteps` 바로 밑에 `values` → `Text`(`type`·`content`·`contentLossy`) | 저장 뒤 `getTestSteps`로 재조회 |
| Test Data 조회 | `TrackerWebService` `getCustomField`: `workitemURI`, `key=testData` | 공란이면 `content` 없음 |
| 세션 종료 | `SessionWebService` `endSession` | |

- Work Item URI는 `subterra:data-service:objects:/default/<프로젝트>${WorkItem}<ID>` 형식이다.
- `setTestSteps`의 요소 이름이 틀리면 원인 설명 없이 `NullPointerException` 500만 돌아온다. 이때 Step은 바뀌지 않았으므로 재조회로 확인하고 형식을 고친다.
- 변경사항 Checklist 등록본은 TC마다 Step 1행에 원본 I/J 블록 전체를 HTML(`<br/>` 줄바꿈)로 담고 있다. 갱신할 때도 같은 구조를 유지한다. 본문은 HTML 이스케이프한 뒤 줄바꿈을 `<br/>`로 바꾼다.
- Test Data 쓰기는 REST PATCH `{"testData": {"type": "text/plain", "value": "..."}}`로 저장·재조회가 확인됐다. Title·Precondition·변경사항 등 다른 속성도 REST PATCH로 갱신한다.

## 4. 필드를 새로 쓸 때

1. 요청한 정보를 담을 필드가 정해져 있지 않으면, 후보 필드가 사용자에게 보이는지 먼저 확인한다. 문서의 `renderingLayouts`와 작업 항목 상세 화면에 표시되는지 본다. 그다음 1건을 저장하고 실제 화면에서 보인 뒤에 일괄 반영한다.
2. 운영 ALM의 Software Test Case 상세 화면과 `titleTestSteps` 문서 표시는 Description을 보여 주지 않았다(2026-11 확인). 화면에 보여야 하는 값을 Description에 넣지 않는다.
3. Old ID처럼 원본과 대응하는 키 필드에는 다른 값을 함께 쓰지 않는다. 키가 깨지면 이후 대조·Roundtrip·등록 검증이 모두 추정 작업이 된다. 알맞은 필드가 없으면 관리자에게 전용 필드 추가를 요청하도록 안내한다.

## 5. 저장 시 바뀌는 문자

- 4바이트 문자(이모지 등)는 저장 시 제거됐다.
- C1 제어문자 U+009F는 U+0178(`Ÿ`)로 바뀌어 저장됐다.
- 쓰기 전에 원본에서 이런 문자를 찾아 두고, 재조회 불일치 중 이 원인만 남은 건은 쓰기 실패와 구분해 보고한다. 원본에서 일반 기호로 바꾸거나 제어문자를 지우도록 안내한다.

## 6. Main TC로 병합된 TC의 분류 필드

제품 스킬에서 Main TC 병합 대상과 대응 Main TC 행이 확인되면 다음 규칙으로 반영한다. 필드와 선택지 ID는 `/workitems/{id}/fields/{field}/actions/getAvailableOptions`로 현재 값을 읽어 확인한다.

| 필드 | 값 |
|---|---|
| STC Category(`stcCategory`, 다중) | 기존 값을 유지하고 `full_function`을 추가 |
| TC위치(`tcPosition`) | Checklist Main 열의 시트 이름에 해당하는 선택지 |
| Function(`function`) · Func 01(`func01`) · Func 02(`func02`) | 대응 Main TC 행의 Function · Func 01 · Func 02 열 값 그대로 |

- 대응 Main TC 행의 근거를 유형별로 보고한다: 병합 이력 기록, 내용 일치, 내용을 읽고 판단한 건, 대응 없음.
- 대응 행이 없는 TC는 Function 계열을 비워 두고 보고한다.
- 반영 전 값을 백업하고, 1건 시험 → 일괄 → 문서 전체 재조회 순서를 따른다.
