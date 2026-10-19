# Gate 결과 형식 연결

아래 절 번호와 명칭은 탐색 힌트다. rules/qa-guide.md의 현재 Gate 정의를 먼저 읽는다. 개정 번호 차이로 중단하지 않으며 의미가 결과 형식과 충돌하면 확인 필요로 남긴다.

`gate_status` 에는 아래 7개 중 이 작업에 해당하는 것만 넣는다. 값은 네 가지다.

| 값 | 뜻 |
|---|---|
| `PASS` | 확인했고 문제 없다 |
| `FAIL` | 이 Gate 때문에 판정을 낼 수 없다 (해당 Finding 은 `사양 확인 필요` 로) |
| `CHECK` | 사람이 확인해야 한다 (질문을 남겼다) |
| `DRAFT` | 무인 모드라 초안으로만 통과시켰다 |

| Gate | 이름 | 가이드에서 읽을 절 | 무인 모드 판정법 |
|---|---|---|---|
| G1 | Source Completeness | §62 G1, §5.1 | 입력에 판정에 필요한 SRS 본문·TC·이슈 필드가 있는가 |
| G2 | Specification Evidence | §62 G2, §9~§11 | 근거 SRS 가 현재 유효한가, 분할·Linked 사양까지 찾았는가 |
| G3 | TC & Root Cause Coverage | §62 G3, §33, §61, §17 | 기존 TC 가 Trigger·Root Cause·관찰점을 실제로 검출하는가. Generator 이슈는 관찰점을 따로 보고, 기존 TC 와 이슈의 Expected 가 부딪치는지 본다 |
| G4 | Test State Validity | §58, §62 G4, §3.9 | Precondition·Step 이 만드는 상태가 사양상 허용되는가. Generator·Console·Viewer 상태를 따로 보고, 화면 초기화를 장비 상태 해제로 보지 않는다 |
| G5 | Execution Feasibility | §62 G5 | 관찰 수단(UI/API/로그/장비)이 있는가. 무인 모드에서는 대개 `CHECK` |
| G6 | Artifact Formatting | §74, §76 G6 | 초안 열 구성이 영향성평가 Checklist 와 같은가 (코드가 Excel 을 만든다) |
| G7 | Cross-check | §76 G7 | Issue ↔ 사양 ↔ Test State ↔ TC ↔ Expected 사이에 모순이 없는가. Generator 이슈는 장비·Console·Viewer 의 Actual 과 Event·Log 의 앞뒤 시점이 맞는지, 기존 TC–이슈 Expected 충돌을 분류했는지도 본다 |

§44 의 "Gate 4 = Execution Feasibility, Gate 5 = Cross-check" 는 옛 번호다. 쓰지 않는다.

현재 원문 은 §76 의 Gate 구조(G1~G7)를 그대로 두고 G3·G4·G7 에 Generator 확인 항목만 더했다.
