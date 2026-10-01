# Gate 판정표 (가이드 §76 기준)

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
| G3 | TC & Root Cause Coverage | §62 G3, §33, §61 | 기존 TC 가 Trigger·Root Cause·관찰점을 실제로 검출하는가 |
| G4 | Test State Validity | §58, §62 G4 | Precondition·Step 이 만드는 상태가 사양상 허용되는가 |
| G5 | Execution Feasibility | §62 G5 | 관찰 수단(UI/API/로그/장비)이 있는가. 무인 모드에서는 대개 `CHECK` |
| G6 | Artifact Formatting | §74, §76 G6 | 초안 열 구성이 영향성평가 Checklist 와 같은가 (코드가 Excel 을 만든다) |
| G7 | Cross-check | §76 G7 | Issue ↔ 사양 ↔ Test State ↔ TC ↔ Expected 사이에 모순이 없는가 |

§44 의 "Gate 4 = Execution Feasibility, Gate 5 = Cross-check" 는 옛 번호다. 쓰지 않는다.
