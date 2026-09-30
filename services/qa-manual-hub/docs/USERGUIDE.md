# QA Manual Hub 사용 안내서

이 문서는 세 부분으로 나뉩니다. 필요한 곳만 읽으셔도 됩니다.

| 대상 | 내용 |
|---|---|
| [1부. 일반 사용자](#1부-일반-사용자) | 로그인, 문서 찾기, 다운로드, 새 버전 올리기 |
| [2부. 관리자(Admin)](#2부-관리자admin) | 계정 관리, 제품·분류 관리 |
| [3부. 서버 운영자](#3부-서버-운영자) | 설치·배포·백업·복구·장애 대응 |

용어 정리:

| 용어 | 뜻 |
|---|---|
| **Product** | 제품. 예: Bellalun Viewer, VXvue |
| **Document** | 문서 종류. 예: Operation Manual, Service Manual |
| **Version** | 문서의 한 개정본. 실제 파일 1개가 붙습니다 |
| **Current** | 그 문서의 **현재 최신본**. 문서마다 딱 하나 |
| **Revision / Version** | 문서에 적힌 개정 번호. 형식은 자유 |
| **Archive** | 보관. 목록에서 숨기지만 **파일은 지우지 않습니다** |

---

# 1부. 일반 사용자

## 1.1 로그인

브라우저에서 시스템 주소로 접속하면 **로그인 화면이 먼저 나옵니다.**
로그인하지 않으면 어떤 화면도 볼 수 없습니다.

- **User ID** — 관리자가 알려준 로그인 ID (대소문자 구분하지 않습니다)
- **Password** — 관리자가 알려준 비밀번호

계정이 없으면 QA 관리자에게 요청하세요.

### 처음 로그인한 경우

관리자가 만들어 준 임시 비밀번호로 로그인하면 화면 위에 노란 경고가 보입니다.

> 임시 비밀번호로 로그인했습니다. 문서 등록·수정 기능을 사용하려면 먼저
> 비밀번호를 변경하세요.

**비밀번호를 바꾸기 전에는 문서를 등록하거나 수정할 수 없습니다.**
(조회·검색·다운로드는 됩니다.)
Admin 도 마찬가지입니다. 비밀번호를 바꾸기 전에는 사용자·제품·분류 관리도 할 수 없습니다.

경고 문구의 "비밀번호를 변경" 링크를 누르거나, 우측 상단 내 이름 → **비밀번호
변경** 으로 이동해서 바꾸세요.

> 현재 이 시스템은 **비밀번호 길이 제한이 없습니다.** 한 글자도 등록됩니다.
> 빈 값과 앞뒤 공백만 거부합니다. 제한을 다시 걸려면 서버 운영자가
> `PASSWORD_MIN_LENGTH` 를 올리면 됩니다.

### 로그인이 안 될 때

화면에는 항상 이 메시지만 나옵니다.

> 아이디 또는 비밀번호가 올바르지 않습니다.

아이디가 틀렸는지 비밀번호가 틀렸는지 알려주지 않는 것은 보안상 의도된
동작입니다. 계정이 비활성화된 경우에도 같은 메시지가 나옵니다.
반복해서 실패하면 관리자에게 확인을 요청하세요.

### 세션

로그인 상태는 기본 **8시간** 유지됩니다. 브라우저를 계속 쓰고 있으면 자동으로
연장됩니다. 만료되면 다시 로그인 화면으로 돌아갑니다.

관리자가 계정을 비활성화하거나 비밀번호를 초기화하면 **열려 있던 탭도 즉시**
로그아웃됩니다.

---

## 1.2 화면 구조

로그인하면 왼쪽에 메뉴, 오른쪽 위에 내 이름이 보입니다.

```
Dashboard         현황 요약과 최근 활동
Products          제품 목록 → 제품별 문서 현황
Documents         전 제품의 문서를 한 표로
Search            상세 조건 검색
Recent Updates    최근 업로드 100건

관리 (Admin 만 보임)
Users             사용자 계정 관리
Categories        문서 분류 관리

시스템
Audit Logs        누가 언제 무엇을 했는지
Settings          서버 설정값 조회
```

우측 상단 내 이름을 누르면 **My Account / 비밀번호 변경 / 로그아웃** 이 나옵니다.

---

## 1.3 최신 문서 찾기

### 방법 1 — 제품에서 찾기 (권장)

**Products** → 제품 이름 클릭

제품의 모든 문서가 한 표로 나옵니다.

표 머리글을 클릭하면 정렬됩니다. 기본은 분류 순서(Operation Manual →
Service Manual → QC Manual → …)입니다.

| 열 | 뜻 |
|---|---|
| Document | 문서 이름 (클릭하면 상세) |
| Category | 문서 분류 |
| **Current Revision** | **현재 최신본**. `CURRENT` 배지가 붙어 있습니다 |
| Doc. No. | 문서 번호 |
| Lang | 언어 |
| Revision Date | 문서에 적힌 개정일 |
| Uploaded By | 그 버전을 올린 사람 |
| Upload Date | 시스템에 올린 일시 |
| Ver. | 총 버전 수 |

맨 오른쪽 **다운로드** 버튼이 곧 **최신본 다운로드**입니다.
상세 화면에 들어가지 않고 바로 받을 수 있습니다.

> `버전 없음` 이라고 표시된 문서는 문서 틀만 만들어져 있고 아직 파일이 올라오지
> 않은 상태입니다.

### 방법 2 — Documents 에서 전체 보기

**Documents** 는 전 제품의 문서를 한 화면에서 보여줍니다.
위쪽에서 제품 / 분류 / 상태 / 문서 이름으로 걸러낼 수 있습니다.

**표 머리글을 클릭하면 그 열 기준으로 정렬됩니다.** 한 번 더 누르면 역순입니다.

- 기본은 **제품 기준 오름차순** 이라 같은 제품 문서가 모여서 보입니다
- `Uploaded By` 로 정렬하면 누가 무엇을 올렸는지 한눈에 묶입니다
- `Upload Date` 내림차순은 "최근에 갱신된 문서" 목록이 됩니다
- `Ver.` 로 정렬하면 개정이 잦은 문서가 드러납니다

정렬 규칙 두 가지를 알아두면 헷갈리지 않습니다.

- **개정 번호는 숫자로 비교합니다.** `V1.0.9` 가 `V1.0.10` 보다 앞에 옵니다
  (글자 그대로 비교하면 반대가 됩니다)
- **빈 칸은 오름차순·내림차순 모두 항상 맨 아래**입니다. 값이 없는 행이
  위로 튀어 올라오지 않습니다

### 방법 3 — 검색

**Search** 에서 **통합 검색** 칸에 아무거나 넣으면 부분 일치로 찾습니다.
제품명, 문서명, 분류, Revision, Version, 문서번호, 언어, 개정 내용, 업로더 이름,
파일명을 한 번에 훑습니다.

`상세 조건 펼치기` 를 누르면 항목별로 지정할 수 있습니다.

| 조건 | 예 |
|---|---|
| Document Name | `operation` |
| Document Number | `OM-001` |
| Revision / Version | `V1.0.12`, `Rev.B` |
| Language | `KO`, `EN` |
| Original File Name | `매뉴얼` |
| Uploaded By | `홍길동` 또는 `hong` |
| Revision Date (from/to) | 개정일 범위 |
| Upload Date (from/to) | 업로드일 범위 |
| Document / Version Status | Active / Archived / 전체 |
| **Current 버전만 검색** | 최신본만 보고 싶을 때 |

---

## 1.4 문서 상세 화면

문서 이름을 클릭하면 상세 화면이 나옵니다.

### 위쪽 요약 카드 4개

- **Current Revision** — 현재 최신본과 개정일
- **Uploaded By** — 누가 언제 올렸는지
- **Document Number** / Language
- **Total Versions** — 총 몇 개의 버전이 보존되어 있는지

### 매뉴얼 뷰어

요약 카드 바로 아래에 뷰어가 자동으로 뜹니다. 클릭 없이 Current 버전(미리보기
가능한 형식이면)이 바로 보이고, Revision History 에서 다른 버전의 "뷰어로 보기"
를 누르면 그 버전으로 바뀝니다. 미리보기를 지원하지 않는 형식은 안내 문구와
함께 다운로드만 제공합니다.

### Revision History

아래로 내려가면 **최신순 타임라인**입니다. 각 버전마다:

- 개정 번호 (`CURRENT` 또는 `HISTORY` 배지)
- Uploaded By (이름과 로그인 ID)
- Revision Date, Document No., Language
- 원본 파일명과 크기
- SHA-256 앞부분 (마우스를 올리면 전체)
- Revision Description — 이번 개정에서 무엇이 바뀌었는지
- Comment

각 버전의 버튼:

| 버튼 | 동작 |
|---|---|
| **다운로드** | 그 버전 파일을 원본 파일명으로 받습니다 |
| **뷰어로 보기** | 위쪽 매뉴얼 뷰어에 그 버전을 띄웁니다 (PDF·이미지·텍스트는 그대로, doc/xls/ppt 는 서버가 PDF 로 변환해서 보여줍니다) |
| **Set as Current** | 그 버전을 현재 최신본으로 지정 |
| **메타데이터 수정** | Revision·Version·문서번호·언어·개정내용 수정 (파일은 그대로) |
| **보관** | 그 버전을 목록에서 숨김 (파일은 삭제되지 않음) |

> **Current 버전은 보관할 수 없습니다.** 먼저 다른 버전을 Current 로 지정하세요.
> 문서에 최신본이 없는 상태가 되지 않게 하려는 장치입니다.

---

## 1.5 문서 등록

문서 "종류"를 새로 만드는 작업입니다. 예: 어떤 제품에 QC Manual 을 추가.

**Products** → 제품 선택 → **+ 문서 등록**
(또는 **Documents** → **+ 문서 등록**)

| 입력 | 설명 |
|---|---|
| Document Name * | 예: `Operation Manual`, `Service Manual`, `QC Manual` |
| Document Category * | 목록에서 선택 |
| Description | 설명 (선택) |

같은 제품 안에 **같은 이름의 문서는 만들 수 없습니다** (대소문자 무시).
다른 제품에는 같은 이름을 쓸 수 있습니다.

문서를 만든 뒤 상세 화면에서 첫 버전 파일을 올립니다.

---

## 1.6 새 버전 업로드

문서 상세 → **+ 새 버전 업로드**

### 파일 선택

- 최대 크기와 허용 확장자는 화면에 표시됩니다 (기본 500MB /
  `pdf doc docx xls xlsx ppt pptx txt md png jpg jpeg`)
- 파일을 고르면 **SHA-256 을 브라우저에서 계산**해 이미 등록된 동일 파일이
  있는지 미리 알려줍니다

### 이미 같은 파일이 있다는 경고가 나오면

> 동일한 내용의 파일이 이미 등록되어 있습니다.
> Bellalun Viewer / Operation Manual / V1.0.12W1 — ... (홍길동, 2026-07-11)

**경고일 뿐 막지 않습니다.** 업무상 같은 파일을 별도 버전으로 등록해야 하는
경우가 있기 때문입니다. 실수로 같은 파일을 올리는 것인지 확인하고 판단하세요.

### 입력 항목

| 항목 | 설명 |
|---|---|
| **Version** | 예: `V1.0.12W1`, `1.1`, `2026.07` |
| **Revision** | 예: `Rev.1.3`, `R2`, `A` |
| Document Number | 문서 관리번호 |
| Language | `KO`, `EN` 등 |
| Revision Date | 문서에 적힌 개정일. 파일명에 날짜가 있으면 채워지고, 없으면 빈칸입니다 (업로드 날짜로 채우지 않습니다) |
| Revision Description | 이번 개정에서 바뀐 내용 (여러 줄 가능) |
| Comment | 기타 메모 |

> **Version 과 Revision 중 최소 하나는 반드시 입력해야 합니다.**
> 형식은 시스템이 강제하지 않습니다. **문서에 적힌 값을 그대로** 넣으세요.

### "이 버전을 Current 로 지정" 체크박스

- **체크 (기본)** — 업로드 후 이 버전이 최신본이 됩니다. 이전 최신본은 이력으로
  남습니다. 평소에는 이대로 두면 됩니다.
- **해제** — 최신본을 바꾸지 않습니다. **과거 Legacy 문서를 뒤늦게 등록할 때**
  사용하세요.

### 업로더는 자동으로 기록됩니다

이름을 입력하는 칸이 없습니다. **지금 로그인한 계정**이 업로더로 기록됩니다.
Login ID 와 표시 이름이 함께 저장되며, 나중에 이름이 바뀌어도 과거 업로드
기록의 업로더 이름은 **당시 이름 그대로** 남습니다.

---

## 1.7 과거 버전을 다시 최신본으로 (Set as Current)

이런 상황을 위한 기능입니다.

```
현재 최신본:  V1.0.13
     ↓
나중에 창고에서 옛 자료 V1.0.8 을 발견해 등록
     ↓
기본 정책상 방금 올린 V1.0.8 이 Current 가 되어 버림  ← 원하지 않는 상태
     ↓
V1.0.13 의  [Set as Current]  클릭
     ↓
Current 가 V1.0.13 으로 복구.  V1.0.8 은 이력으로 남음
```

문서 상세 → Revision History 에서 원하는 버전의 **Set as Current** 를 누르면
확인 창이 뜹니다.

> `V1.0.13` 을 Current 버전으로 지정합니다. 기존 Current(`V1.0.8`)는 이력으로
> 남고 삭제되지 않습니다.

**Current 를 바꿔도 어떤 파일도 삭제되지 않습니다.** 변경 이력은 Audit Log 에
`이전 → 이후` 형태로 남습니다.

업로드할 때 애초에 "Current 로 지정" 체크를 해제하면 이 과정이 필요 없습니다.

---

## 1.8 보관(Archive)과 복원(Restore)

이 시스템에는 **완전 삭제 기능이 없습니다.** 잘못 만든 문서나 더 이상 쓰지 않는
문서는 **보관** 상태로 바꿉니다.

### 문서 보관

문서 상세 → **문서 보관**

확인 창에 무엇이 어떻게 되는지 나옵니다.

> `Operation Manual` 문서를 보관 상태로 전환합니다. 버전 3건과 저장된 파일은
> 삭제되지 않으며 언제든 복원할 수 있습니다.

보관하면:

- 기본 목록에서 사라집니다 (Status 필터를 `Archived` 또는 `전체` 로 하면 보임)
- **새 버전을 올릴 수 없습니다** — 먼저 복원해야 합니다
- 기존 버전 파일은 그대로 다운로드할 수 있습니다

### 복원

보관된 문서를 열고 **문서 복원**. 버전 수와 파일이 그대로 돌아옵니다.

### 버전 보관

특정 버전만 숨기려면 Revision History 에서 그 버전의 **보관** 을 누릅니다.
Current 버전은 보관할 수 없습니다.

---

## 1.9 내 계정

우측 상단 이름 → **My Account**

- 표시 이름, Login ID, 권한, 마지막 로그인 시각 확인
- 비밀번호 변경 (현재 비밀번호 필요. 길이 제한은 서버 설정에 따르며 현재는 제한 없음)

**Login ID 와 표시 이름은 스스로 바꿀 수 없습니다.** 관리자에게 요청하세요.
Login ID 를 고정하는 이유는 과거 업로드 기록의 추적성을 지키기 위해서입니다.

비밀번호를 잊었으면 관리자에게 초기화를 요청하세요.

---

## 1.10 Dashboard

로그인 후 첫 화면입니다.

**집계**
- Products / Documents / Versions
- **Current 지정 문서** — `5 / 6` 처럼 표시됩니다. 파일이 아직 없는 문서가
  몇 건인지 바로 보입니다
- Storage — 저장소 사용량
- 활성 사용자 수

**목록**
- 최근 업로드 — 제품, 문서, 개정번호, 업로더, 일시, Current 여부
- 최근 등록 문서
- 최근 Current 변경
- 최근 사용자 활동

---

## 1.11 Audit Logs

누가 언제 무엇을 했는지 전부 남습니다. 일반 사용자도 조회할 수 있습니다.

기록되는 일: 로그인 / 로그인 실패 / 로그아웃, 사용자 생성·수정·비밀번호
초기화·활성·비활성·권한 변경, 제품·분류 생성·수정, 문서 생성·수정·보관·복원·
다운로드, 버전 업로드·정보 수정·보관·복원, **Current 버전 변경**, 설정 변경.

Action / 사용자 / 대상 / 날짜 범위로 걸러낼 수 있습니다.
**변경내역** 버튼을 누르면 변경 전(BEFORE)과 후(AFTER)를 나란히 보여줍니다.

이 기록은 **수정하거나 삭제할 수 없습니다.** 그런 기능이 시스템에 존재하지
않습니다.

---

## 1.12 자주 겪는 상황

| 상황 | 원인과 해결 |
|---|---|
| 문서 목록이 비어 보인다 | Status 필터가 `Active` 일 때 보관된 문서는 숨습니다. `전체` 로 바꿔 보세요 |
| 업로드가 "파일 크기 제한 초과" | Settings 에서 최대 크기 확인. 초과 시 관리자에게 상향 요청 |
| "확장자는 허용되지 않습니다" | Settings 의 허용 확장자 목록 확인. 필요하면 관리자에게 추가 요청 |
| "파일 내용이 형식과 일치하지 않습니다" | 확장자만 바꾼 파일입니다. 실제 형식으로 다시 저장해 올리세요 |
| "Revision 또는 Version 중 하나는 필수" | 둘 다 비워두면 올릴 수 없습니다 |
| "Current 버전은 보관할 수 없습니다" | 다른 버전을 먼저 Current 로 지정하세요 |
| "보관된 문서에는 새 버전을 업로드할 수 없습니다" | 문서를 먼저 복원하세요 |
| 갑자기 로그인 화면으로 튕겼다 | 세션 만료(8시간) 또는 관리자가 계정을 잠금/비밀번호 초기화 |
| 문서 등록 버튼을 눌렀는데 안 된다 | 임시 비밀번호 상태입니다. 비밀번호를 먼저 변경하세요 |
| 한글 파일명이 깨져 보인다 | 스크립트로 올린 파일입니다. **브라우저로** 다시 올리세요 ([3부 참조](#39-한글-파일명-업로드-주의)) |

---

# 2부. 관리자(Admin)

Admin 은 1부의 모든 기능에 더해 **사용자 계정 / 제품 / 문서 분류** 관리를
할 수 있습니다. 왼쪽 메뉴에 **Users** 와 **Categories** 가 추가로 보입니다.

## 2.1 사용자 계정 관리

**Users** 메뉴

| 열 | 설명 |
|---|---|
| Login ID | 로그인 ID (변경 불가) |
| Name | 표시 이름. 업로더 이름으로 나타납니다 |
| Role | `ADMIN` 또는 `USER` |
| Status | `ACTIVE` 또는 `DISABLED` |
| Last Login | 마지막 로그인 시각 |
| Created | 계정 생성 시각 |

### 신규 사용자 생성

**+ 사용자 추가**

| 입력 | 설명 |
|---|---|
| Login ID * | 영문·숫자·`.` `_` `-` 만. 2자 이상. **나중에 변경할 수 없습니다** |
| 사용자 이름 * | 예: `홍길동`. 업로더 이름으로 표시됩니다 |
| 초기 Password * | 길이 제한은 서버 설정을 따릅니다 (현재 제한 없음) |
| Role | `User` (문서 관리 전체) 또는 `Admin` (사용자·제품·분류 관리 추가) |
| 최초 로그인 시 비밀번호 변경 요구 | **켜 두는 것을 권장** |

초기 비밀번호는 화면에 다시 표시되지 않습니다. **사내 메신저 등 별도 경로로
직접 전달**하세요.

Login ID 는 대소문자를 구분하지 않고 중복을 막습니다 (`hong` 과 `HONG` 은 같은
것으로 취급).

### 사용자 정보 수정

**수정** 버튼 — 표시 이름, Role, 비밀번호 변경 요구 여부를 바꿉니다.

> 표시 이름을 바꿔도 **과거 업로드 기록의 업로더 이름은 당시 값으로 유지**됩니다.
> 예: `김철수` → `김철수(전출)` 로 바꿔도, 예전에 올린 버전의 Uploaded By 는
> `김철수` 로 남습니다.

### 비밀번호 초기화

**비밀번호 초기화** 버튼

- 임시 비밀번호를 지정합니다 (길이 제한은 서버 설정에 따름)
- "최초 로그인 시 비밀번호 변경 요구" 를 켜 두면 사용자가 직접 다시 정합니다
- **그 사용자의 열려 있는 모든 세션이 즉시 종료됩니다**
- 임시 비밀번호는 별도 경로로 직접 전달하세요

### 사용자 비활성화 / 활성화

**비활성화** — 즉시 로그인이 차단되고 **열려 있던 탭도 다음 요청에서 종료**됩니다.
계정과 데이터는 지워지지 않으며, 그 사람이 올린 문서와 이력은 그대로 남습니다.

**활성화** — 같은 비밀번호로 다시 로그인할 수 있습니다.

> 퇴사·전출 시에는 계정을 삭제하지 말고 **비활성화**하세요. 삭제 기능이 없는
> 이유이기도 합니다 — 문서 이력의 추적성을 지켜야 합니다.

### 권한 변경

**수정** → Role 을 `Admin` / `User` 로 변경.

### 안전장치 (일부러 막아 둔 것)

| 시도 | 결과 | 이유 |
|---|---|---|
| 자기 계정 비활성화 | 거부 | 스스로 잠기는 것 방지 |
| 자기 Admin 권한 해제 | 거부 | 같음. 다른 Admin 에게 요청하세요 |
| 마지막 활성 Admin 강등·비활성화 | 거부 | 시스템에 Admin 이 하나도 없는 상태 방지 |

**모든 Admin 이 잠긴 경우**에는 서버에서 CLI 로 복구합니다
([3.5 관리 CLI](#35-관리-cli) 참조).

---

## 2.2 제품 관리

### 제품 찾기

제품이 많아지면 **Products** 화면 위쪽의 **제품 검색** 칸을 쓰세요.
제품 이름과 제품 코드를 부분 일치로 찾습니다 (대소문자 무시).
`비활성 포함` 을 켜면 비활성 제품까지 함께 검색합니다.

표 머리글(Product / Code / Documents / Versions / 최근 업로드 / Status)을
클릭하면 그 기준으로 정렬됩니다. 예를 들어 `최근 업로드` 내림차순은 최근에
문서가 갱신된 제품부터 보여줍니다.

### 제품 추가

**Products** → **+ 제품 추가** (Admin 만 보입니다)

| 입력 | 설명 |
|---|---|
| Product Name * | 예: `Bellalun Viewer`, `VXvue`, `VXvue M`, `VIVIX` |
| Product Code | 예: `BLV`, `VXV` (선택) |
| 정렬 순서 | 작은 값이 목록 위. 기본 100 |
| Description | 설명 |

**제품을 추가하는 것만으로 새 제품 문서 관리가 시작됩니다.** 코드 수정이나
서버 작업은 필요하지 않습니다.

### 제품 비활성화

**수정** → "활성(Active)" 체크 해제.

- 새 문서를 만들 때 선택 목록에서 사라집니다
- **기존 문서와 파일은 삭제되지 않습니다**
- `비활성 포함` 을 체크하면 목록에서 다시 볼 수 있습니다

제품은 삭제할 수 없습니다. 문서가 참조하고 있기 때문입니다.

---

## 2.3 문서 분류 관리

**Categories** (Admin 만)

기본 제공 10종:

```
Operation Manual              제품 사용/운영 매뉴얼
Service Manual                서비스/유지보수 매뉴얼
QC Manual                     품질관리 매뉴얼
DICOM Conformance Statement   DICOM 적합성 선언서
Installation Manual           설치 매뉴얼
User Manual                   사용자 매뉴얼
Release Note                  릴리즈 노트
Specification                 사양서
Technical Manual              기술 매뉴얼
Other                         기타 문서
```

**+ 분류 추가** 로 자유롭게 추가할 수 있습니다.

### 분류 비활성화

**비활성화** 버튼. 단, **그 분류를 사용하는 활성 문서가 있으면 거부됩니다.**

> 이 분류를 사용하는 활성 문서가 3건 있어 비활성화할 수 없습니다.

먼저 해당 문서들의 분류를 다른 것으로 바꾸거나 보관 처리하세요.
분류는 삭제할 수 없습니다.

---

## 2.4 관리자 운영 권장 사항

**계정**
- 담당자별로 개별 계정을 만들고 공용 계정을 쓰지 마세요. 업로더 추적의 근거가
  사라집니다
- 초기 비밀번호는 "최초 로그인 시 변경 요구" 를 항상 켜세요
- `admin` 계정은 비상용으로만 남기고, 평소 업무는 개인 계정으로 하세요
- 퇴사·전출자는 삭제가 아니라 **비활성화**

**문서**
- 문서 이름은 제품 안에서 일관되게 유지하세요 (`Operation Manual` /
  `Service Manual` / `QC Manual` 처럼)
- Revision Description 을 반드시 채우도록 안내하세요. 나중에 "왜 개정했는지"
  를 알 수 있는 유일한 근거입니다
- Legacy 문서를 등록할 때는 업로드 시 "Current 로 지정" 을 **해제**하도록
  안내하세요. Set as Current 로 나중에 고치는 것보다 깔끔합니다

**점검**
- 월 1회 Dashboard 의 "Current 지정 문서" 를 확인해 파일이 없는 문서를 찾으세요
- Audit Log 에서 `로그인 실패` 를 주기적으로 확인하세요
- 서버 운영자에게 백업이 정상 동작 중인지 확인을 요청하세요

---

# 3부. 서버 운영자

## 3.1 구성 요약

```
브라우저 ──HTTP──> nginx :80 ──┬── /      → SPA 정적 파일
                               └── /api/  → uvicorn (127.0.0.1, 로컬 전용)
                                              │
                                    ┌─────────┴─────────┐
                              PostgreSQL           문서 저장소
                              (전용 DB/role)      <DATA_ROOT>/storage
```

```
<APP_ROOT>/               기본 /opt/qa-manual-hub
├── .env                  600 — DB 접속정보 포함. 절대 커밋하지 않습니다
├── venv/                 Python virtualenv
├── logs/                 app.log / error.log / backup.log
├── scripts/              qamh, backup.sh, restore.sh
└── app/
    ├── REVISION          배포된 커밋 해시
    ├── backend/          FastAPI + alembic
    └── frontend/         Vite 빌드 산출물

<DATA_ROOT>/              기본 /srv/qa-manual-hub
├── storage/              750 — 실제 문서 파일 (UUID 경로)
└── backup/               750 — DB 덤프 + storage 아카이브
```

문서 파일은 아래 구조로 저장됩니다. **원본 파일명은 파일시스템에 쓰지 않습니다.**

```
<DATA_ROOT>/storage/<product-uuid>/<document-uuid>/<version-uuid>/<file-uuid>.<ext>
```

원본 파일명은 DB(`stored_files.original_file_name`)에만 있습니다. Path traversal
과 파일명 인코딩 문제를 구조적으로 없애기 위한 설계입니다.

---

## 3.2 설치

### 사전 조건
- systemd 리눅스 (Ubuntu 22.04 / 24.04 검증)
- PostgreSQL 14 이상 구동 중
- Python 3.11 이상
- 개발 PC에 Node.js 20 이상

### 절차

```bash
# 1) 소스를 서버로 전송
#    rsync 가 없으면 tar 파이프
tar --exclude='.git' --exclude='node_modules' --exclude='frontend/dist' \
    --exclude='__pycache__' -czf - . \
  | ssh user@server 'rm -rf ~/qamh-src && mkdir -p ~/qamh-src && tar -xzf - -C ~/qamh-src'

# 2) 설치
ssh user@server 'sudo bash ~/qamh-src/deploy/scripts/install.sh'

# 3) 코드 배포 (개발 PC에서)
./deploy/scripts/deploy.sh user@server

# 4) 최초 관리자
ssh user@server 'sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh bootstrap-admin'

# 5) 기본 분류 + 제품
ssh user@server '<APP_ROOT>/scripts/qamh seed-catalog --product "제품명"'

# 6) 시작
ssh user@server 'sudo systemctl start qa-manual-hub'
ssh user@server 'curl -s http://127.0.0.1/api/health'
```

### install.sh 가 하는 일

**모두 추가 작업입니다.** 기존 서비스를 멈추거나 기존 DB를 초기화하지 않습니다.

1. **사전 점검** — 백엔드 포트가 이미 쓰이고 있거나 PostgreSQL 접속이 안 되면
   **아무것도 바꾸지 않고 중단**
2. 없는 패키지만 apt 설치 (nginx, python3-venv, python3-pip, rsync)
3. `<APP_ROOT>`, `<DATA_ROOT>` 생성
4. 전용 DB / role 생성. **이미 있으면 그대로 사용** (초기화 안 함). 이미 있는 role 의
   비밀번호도 바꾸지 않습니다. role 은 있는데 `.env` 에 비밀번호가 없으면 멈춥니다
   (그 role 을 이 서비스만 쓴다면 `RESET_DB_PASSWORD=1` 로 다시 실행)
5. 무작위 DB 비밀번호로 `.env` 생성 (600). **이미 있으면 유지**
6. virtualenv + 의존성
7. systemd 유닛 설치 및 enable
8. nginx 사이트 설치. 새로 설치된 nginx 의 기본 `default` 사이트만 비활성화
   (파일은 보존)
9. UFW 가 active 면 `80/tcp` 규칙 **1개만** 추가
10. 자동 백업 `/etc/cron.d/qa-manual-hub-backup` 생성 (매일 02:30). 이미 있으면 그대로 둠
    (`SKIP_BACKUP_CRON=1` 로 건너뜀)

멱등입니다. 몇 번이든 다시 실행할 수 있습니다.

환경변수로 조정:

```bash
sudo APP_ROOT=/opt/qamh DATA_ROOT=/data/qamh BACKEND_PORT=9190 \
     DB_NAME=mydocs DB_USER=mydocs SERVICE_USER=www-data \
     SKIP_NGINX=1 SKIP_UFW=1 \
     ./deploy/scripts/install.sh
```

---

## 3.3 업데이트 배포

```bash
./deploy/scripts/deploy.sh user@server
```

1. 프론트엔드 로컬 빌드 (`npm ci && npm run build`)
2. 백엔드 rsync (`tests/`, `__pycache__` 제외)
3. SPA 빌드 산출물 rsync
4. 운영 스크립트(`qamh`, `backup.sh`, `restore.sh`) 갱신
5. `REVISION` 에 커밋 해시 기록
6. pip 의존성 동기화
7. `alembic upgrade head`
8. `systemctl restart qa-manual-hub`
9. 헬스체크 (최대 30초 재시도, 실패 시 비정상 종료)

**롤백**이 필요하면 이전 커밋을 체크아웃해 다시 `deploy.sh` 를 실행합니다.
스키마 변경이 포함된 경우 `alembic downgrade` 를 먼저 검토하세요.

`rsync` 가 없는 환경에서는 tar 파이프로 대체합니다 (README 참조).

---

## 3.4 설정

`<APP_ROOT>/.env` — 템플릿은 `deploy/.env.example`

| 키 | 기본값 | 비고 |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+psycopg://user:pass@127.0.0.1:5432/db` |
| `STORAGE_ROOT` | `<DATA_ROOT>/storage` | |
| `MAX_UPLOAD_MB` | `500` | **nginx `client_max_body_size` 와 함께 조정** |
| `ALLOWED_EXTENSIONS` | `pdf,doc,docx,...` | 쉼표 구분 |
| `SESSION_LIFETIME_HOURS` | `8` | |
| `SESSION_COOKIE_SECURE` | `false` | **HTTPS 적용 시 `true`** |
| `SESSION_COOKIE_SAMESITE` | `lax` | |
| `PASSWORD_MIN_LENGTH` | `1` | `1` = 길이 제한 없음. 올리면 즉시 강제됨 |
| `BOOTSTRAP_ADMIN_*` | | `bootstrap-admin` 에서만 사용. 비밀번호는 주석 유지 권장 |
| `CORS_ORIGINS` | (빈 값) | 운영에서는 비웁니다 |

**변경 후 반드시** `sudo systemctl restart qa-manual-hub`

값에 공백이 들어가면 인용부호로 감싸세요. `KEY=QA Admin` 은 셸에서 소싱할 때
오류가 납니다. `KEY="QA Admin"` 으로 씁니다.

### 업로드 크기 올리기

두 곳을 함께 바꿔야 합니다.

```bash
# 1) 앱
sudo sed -i 's/^MAX_UPLOAD_MB=.*/MAX_UPLOAD_MB=1024/' <APP_ROOT>/.env
sudo systemctl restart qa-manual-hub

# 2) nginx  (앱보다 약간 크게)
sudo sed -i 's/client_max_body_size .*/client_max_body_size 1100M;/' \
    /etc/nginx/sites-available/qa-manual-hub.conf
sudo nginx -t && sudo systemctl reload nginx
```

nginx 를 앱보다 크게 잡는 이유: nginx 가 먼저 거절하면 사용자는 기본 413 HTML
페이지를 보게 됩니다. 앱이 먼저 판단하면 한국어 안내 메시지가 나옵니다.

### HTTPS 적용

1. 인증서를 서버에 배치
2. nginx 에 `listen 443 ssl;` 서버 블록 추가, `ssl_certificate` 지정,
   80 → 443 리다이렉트
3. `.env` 의 `SESSION_COOKIE_SECURE=true`
4. `sudo systemctl restart qa-manual-hub && sudo nginx -t && sudo systemctl reload nginx`

애플리케이션 코드는 수정하지 않습니다.

---

## 3.5 관리 CLI

```bash
<APP_ROOT>/scripts/qamh <command>
```

래퍼가 `.env` 를 자동 로드하므로 어느 경로에서 실행해도 됩니다.
서비스 계정으로 실행하세요 (`sudo -u <SERVICE_USER> ...`).

| 명령 | 용도 |
|---|---|
| `bootstrap-admin` | 최초 관리자 생성. 관리자가 이미 있으면 `--force` 필요 |
| `seed-catalog [--product NAME]` | 기본 분류 10종 + 제품 생성 (이미 있으면 건너뜀) |
| `reset-password <login_id>` | **모든 Admin 이 잠겼을 때의 복구 경로.** 해당 사용자의 세션도 전부 무효화 |
| `list-users` | 사용자 목록 (Login ID / 이름 / Role / Active / 마지막 로그인) |
| `check-storage` | DB에 등록된 모든 버전의 파일 존재·크기 검증. 문제 있으면 종료코드 2 |
| `check-storage --verify-sha256` | 위 검사에 더해 파일 내용을 다시 읽어 업로드 때 SHA-256 과 비교. 크기는 같은데 내용이 손상된 파일도 찾음 (느림) |
| `purge-sessions` | 만료된 세션 행 정리 |

비밀번호는 대화형 프롬프트로 받습니다 (셸 히스토리에 남지 않음).
자동화가 필요하면 환경변수로 1회 전달:

```bash
BOOTSTRAP_ADMIN_PASSWORD='...' <APP_ROOT>/scripts/qamh bootstrap-admin
NEW_PASSWORD='...' <APP_ROOT>/scripts/qamh reset-password admin
```

### 관리자 잠김 복구

모든 Admin 이 비활성화되거나 비밀번호를 잊은 경우:

```bash
ssh user@server
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh list-users        # 어떤 admin 이 있는지
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh reset-password admin
```

계정이 비활성 상태라면 DB에서 직접 되살립니다.

```bash
set -a; . <APP_ROOT>/.env; set +a
psql "$(echo "$DATABASE_URL" | sed 's|postgresql+psycopg|postgresql|')" \
  -c "UPDATE users SET is_active = true WHERE login_id = 'admin'"
```

---

## 3.6 백업

### 수동 실행

```bash
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/backup.sh
```

`<DATA_ROOT>/backup/<YYYYmmdd-HHMMSS>/` 에 3개 파일:

| 파일 | 내용 |
|---|---|
| `database.dump` | `pg_dump --format=custom --compress=6 --no-owner --no-privileges` |
| `storage.tar.gz` | 문서 저장소 전체 (`.env` 의 `STORAGE_ROOT`, 없으면 `<DATA_ROOT>/storage`) |
| `manifest.txt` | 백업 시각, 호스트, DB명, storage 파일 개수, 배포 커밋, 각 산출물 SHA-256 |

`restore.sh` 는 복원 전에 `manifest.txt` 의 SHA-256 을 모든 파일과 맞춰 봅니다.
manifest 가 없거나 값이 다르면 아무것도 바꾸지 않고 멈춥니다. 그래서 **DB 덤프와 파일
세트가 어긋난 조합으로 복구되는 일**이 없습니다.

### 자동 실행

`/etc/cron.d/qa-manual-hub-backup` (`install.sh` 가 없을 때만 만듭니다. 시간을 바꿔도
다시 실행한 `install.sh` 가 덮어쓰지 않습니다.)

```
30 2 * * *  <SERVICE_USER>  <APP_ROOT>/scripts/backup.sh >> <APP_ROOT>/logs/backup.log 2>&1
```

동작 확인:

```bash
tail -30 <APP_ROOT>/logs/backup.log
ls -la <DATA_ROOT>/backup/
systemctl status cron
```

### 보존 정책

`backup.sh` 상단 변수로 조정합니다.

| 변수 | 기본 | 설명 |
|---|---|---|
| `KEEP_DAILY` | `7` | 최근 7본 유지, 그 이전은 삭제 |
| `KEEP_WEEKLY` | `4` | 주간 승격은 현재 수동 |
| `KEEP_MONTHLY` | `3` | 월간 승격은 현재 수동 |

주간·월간 보관본이 필요하면 특정 백업 디렉터리를 다른 이름으로 복사해
자동 삭제 대상에서 제외하세요.

```bash
cp -a <DATA_ROOT>/backup/20260827-023000 <DATA_ROOT>/backup/weekly-2026W35
```

### 백업을 서버 밖으로

**중요** — 기본 설정에서 백업은 원본과 **같은 디스크**에 있습니다. 디스크 장애
시 원본과 백업이 동시에 사라집니다.

```bash
# 예: NAS 마운트 지점으로 백업
sudo BACKUP_ROOT=/mnt/nas/qamh-backup <APP_ROOT>/scripts/backup.sh

# 또는 생성 후 복제
rsync -az <DATA_ROOT>/backup/ backupserver:/vol/qamh/
```

---

## 3.7 복구

```bash
sudo <APP_ROOT>/scripts/restore.sh <DATA_ROOT>/backup/20260827-023000
```

**현재 데이터를 대체하는 작업입니다.** 스크립트는 안전 절차를 강제합니다.

1. **백업 폴더 확인** — `manifest.txt` 의 SHA-256 이 모든 파일과 맞지 않으면 멈춤
2. **무엇을 덮어쓸지 먼저 출력** — 백업 시각, 대상 DB, storage 경로(`STORAGE_ROOT`), 현재 파일 수
3. `RESTORE` 를 타이핑해야 진행 (`--yes` 로 생략 가능, 자동화 전용)
4. **현재 상태를 `backup/pre-restore-<timestamp>/` 에 먼저 백업** ← 되돌릴 수 있음.
   DB 덤프나 저장소 묶기가 실패하면 서비스와 데이터를 건드리지 않고 멈춤
5. 서비스 정지
6. `public` 스키마 DROP → CREATE → `pg_restore`
7. 저장소를 `<STORAGE_ROOT>.replaced-<timestamp>` 로 이동하고 아카이브 전개
8. `alembic upgrade head` (덤프가 구버전 스키마일 수 있으므로)
9. 서비스 시작 + `is-active` 확인
10. `qamh check-storage --verify-sha256` 로 DB↔파일 일치 검증 (내용까지)

정상 출력 예:

```
[restore] 데이터베이스 복원 완료
[restore] 저장소 복원 완료 (기존 폴더는 <STORAGE_ROOT>.replaced-<timestamp> 로 보존)
[restore] 서비스 정상 동작
[restore] 파일 무결성 점검 (존재·크기·SHA-256)
검사한 버전: 8
파일 없음: 0
크기 불일치: 0
SHA-256 불일치: 0
[restore] 복원 완료. 안전 백업 위치: <DATA_ROOT>/backup/pre-restore-...
```

### 복구 후 확인

```bash
curl -s http://127.0.0.1/api/health
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh list-users
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh check-storage
```

브라우저로 로그인해 Dashboard 의 문서·버전 수가 백업 시점과 맞는지 확인합니다.

문제가 없으면 정리:

```bash
sudo rm -rf <STORAGE_ROOT>.replaced-*     # 기본값이면 <DATA_ROOT>/storage.replaced-*
sudo rm -rf <DATA_ROOT>/backup/pre-restore-*
```

### 잘못 복구했을 때

4단계에서 만든 `pre-restore-*` 백업으로 다시 복구합니다.

```bash
sudo <APP_ROOT>/scripts/restore.sh <DATA_ROOT>/backup/pre-restore-<timestamp>
```

---

## 3.8 모니터링과 장애 대응

### 상태 확인

```bash
systemctl status qa-manual-hub --no-pager
journalctl -u qa-manual-hub -n 100 --no-pager
curl -s http://127.0.0.1/api/health

tail -f <APP_ROOT>/logs/app.log
tail -f <APP_ROOT>/logs/error.log
tail -f /var/log/nginx/qa-manual-hub.error.log

du -sh <DATA_ROOT>/storage <DATA_ROOT>/backup
df -h /
free -h
```

### 증상별 대응

| 증상 | 확인 | 조치 |
|---|---|---|
| **502 Bad Gateway** | `systemctl status qa-manual-hub` | 서비스 중단. `journalctl -u qa-manual-hub -n 50` 로 원인 확인 후 재시작. **`.env` 문법 오류가 가장 흔한 원인** (공백 값 미인용 등) |
| **503 / 연결 거부** | `systemctl status nginx` | nginx 중단. `nginx -t` 로 설정 검증 후 시작 |
| 시작 즉시 죽음 | `journalctl -u qa-manual-hub -n 50` | DB 접속 실패(비밀번호 불일치), `STORAGE_ROOT` 권한 문제 |
| DB 인증 실패 | `.env` 의 `DATABASE_URL` | 그 role 을 이 서비스만 쓴다면 `sudo RESET_DB_PASSWORD=1 ./deploy/scripts/install.sh` 로 role 비밀번호를 `.env` 값에 맞춥니다 (그냥 다시 실행하면 비밀번호를 바꾸지 않습니다) |
| **업로드 413** | 파일 크기, `MAX_UPLOAD_MB`, `client_max_body_size` | [3.4](#34-설정) 참조. 두 곳 모두 조정 |
| 업로드 도중 끊김 | `proxy_read_timeout`, 네트워크 | nginx 타임아웃은 기본 600s |
| **다운로드 410 Gone** | `qamh check-storage` | DB 행은 있는데 파일이 없음. 백업에서 storage 복구 |
| 디스크 부족 | `df -h`, `du -sh <DATA_ROOT>/*` | 오래된 백업 정리, `KEEP_DAILY` 축소, 백업을 외부로 이전 |
| 메모리 부족 | `free -h`, `systemctl status qa-manual-hub` | 유닛에 `MemoryMax` 가 설정되어 있습니다. 값 조정 또는 worker 수 축소 |
| 응답이 느림 | `journalctl`, `pg_stat_activity` | 문서 수가 매우 많아졌다면 인덱스 추가 검토 |
| **백업이 안 돎** | `tail <APP_ROOT>/logs/backup.log`, `systemctl status cron` | `/etc/cron.d/qa-manual-hub-backup` 존재 여부와 권한(644) 확인 |
| 호스트명 접속 불가 | `nslookup <호스트명>` | DNS A 레코드 미등록. IP 로 접속 가능 |
| 사용자가 로그인 못 함 | Audit Log 의 `로그인 실패`, `qamh list-users` | 계정 비활성 여부 확인. 필요하면 비밀번호 초기화 |

### 정기 점검 (권장)

**주 1회**
```bash
systemctl status qa-manual-hub nginx --no-pager
tail -20 <APP_ROOT>/logs/backup.log
ls -la <DATA_ROOT>/backup/ | tail -10
df -h /
```

**월 1회**
```bash
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh check-storage
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh purge-sessions
du -sh <DATA_ROOT>/storage
```
- Audit Log 에서 `로그인 실패` 검토
- 백업 1건을 골라 **실제로 복구가 되는지** 검증용 환경에서 확인

**분기 1회**
- OS 보안 업데이트
- 백업을 서버 밖으로 보관 중인지 확인
- 사용자 목록 검토 (퇴사자 계정 비활성화 여부)

---

## 3.9 한글 파일명 업로드 주의

파일 업로드는 **브라우저로** 하십시오.

한국어 Windows 로케일의 **Git Bash `curl`** 은 multipart 필드와 파일명을
**CP949** 로 인코딩해 전송합니다. 서버는 RFC 에 따라 latin-1 로 디코드하므로
아래처럼 저장됩니다.

```
(¸Å´º¾ó) Operation Manual.V1.0.12W1_KO_È®ÀÎ¿Ï·á.docx
```

**애플리케이션 결함이 아닙니다.** UTF-8 로 전송하면 저장·다운로드 양방향이
정상 동작하며, 브라우저는 UTF-8 페이지에서 항상 UTF-8 로 보냅니다.

스크립트로 대량 업로드가 필요하면 UTF-8 로 인코딩하는 클라이언트를 쓰세요.
Python 예시:

```python
import uuid, urllib.request, http.cookiejar, json
from pathlib import Path

BASE = "http://<서버>"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

# 로그인
op.open(urllib.request.Request(
    f"{BASE}/api/auth/login",
    data=json.dumps({"login_id": "admin", "password": "..."}).encode(),
    headers={"Content-Type": "application/json"}))

# 업로드 — 필드와 파일명을 모두 UTF-8 로 인코딩
def upload(document_id: str, path: Path, fields: dict[str, str]):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        parts.append(v.encode("utf-8") + b"\r\n")
    parts.append(
        f'--{b}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{path.name}"\r\n\r\n'.encode("utf-8"))
    parts.append(path.read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(
        f"{BASE}/api/documents/{document_id}/versions",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    return json.load(op.open(req))
```

이미 깨져서 저장된 파일명은 고칠 수 없습니다(원본 파일명은 업로드 당시 기록이라
수정 API 가 없습니다). 브라우저로 새 버전을 올린 뒤 깨진 버전을 보관 처리하세요.

---

## 3.10 보안 점검 항목

| 항목 | 확인 |
|---|---|
| `.env` 권한 | `600`, 서비스 계정 소유. `ls -l <APP_ROOT>/.env` |
| 백엔드 포트 | 외부에 노출되지 않아야 합니다. `ss -tulpn \| grep <port>` → `127.0.0.1` |
| 저장소 권한 | `<DATA_ROOT>/storage` 750, 파일 640, 실행 권한 없음 |
| 방화벽 | 80(및 HTTPS 시 443)만 개방 |
| HTTPS | 적용 시 `SESSION_COOKIE_SECURE=true` 로 변경했는지 |
| 초기 관리자 비밀번호 | 인수 후 변경했는지 |
| SSH | 키 인증 사용, 비밀번호 인증 최소화 |
| 백업 위치 | 원본과 다른 디스크/장치에 사본이 있는지 |
| 비밀정보 유출 | 저장소에 `.env`, 비밀번호, 실제 문서 파일이 커밋되지 않았는지 |

`.gitignore` 가 아래를 제외합니다: `.env`, `storage/`, `backup/`, `uploads/`,
`*.log`, `pgdata/`, `*.dump`, `*.pem`, `*.key`, `node_modules/`,
`frontend/dist/`, `docs/local/`.

---

## 3.11 확장

### 새 제품 추가
화면에서 **Products → + 제품 추가**. 서버 작업 없습니다.

### 문서 수가 크게 늘어난 경우
- 저장소 용량: `du -sh <DATA_ROOT>/storage`, `df -h`
- 백업 시간이 길어지면 storage 를 증분 백업으로 전환 검토
- 검색이 느려지면 `documents.name` / `document_versions.*` 에 trigram 인덱스
  (`pg_trgm`) 추가 검토

### 저장소를 NAS / S3 로 이전
`backend/app/storage.py` 의 `StorageBackend` 프로토콜을 구현한 클래스를 추가하고
`get_storage()` 팩토리를 바꿉니다. `stored_files.storage_backend` /
`storage_key` 컬럼이 이미 있어 라우터·서비스 코드는 수정하지 않습니다.
기존 데이터는 마이그레이션 스크립트로 옮기고 `storage_backend` 를 갱신하면 됩니다.

### 다른 서버로 이전

```bash
# 이전 서버
sudo <APP_ROOT>/scripts/backup.sh
scp -r <DATA_ROOT>/backup/<latest> newserver:/tmp/

# 새 서버
sudo ./deploy/scripts/install.sh
./deploy/scripts/deploy.sh user@newserver
sudo <APP_ROOT>/scripts/restore.sh /tmp/<latest>
```

---

## 3.12 명령 요약

```bash
# 상태
systemctl status qa-manual-hub --no-pager
journalctl -u qa-manual-hub -n 100 --no-pager
curl -s http://127.0.0.1/api/health

# 재시작
sudo systemctl restart qa-manual-hub          # .env 변경 후
sudo nginx -t && sudo systemctl reload nginx  # nginx 변경 후

# 관리
<APP_ROOT>/scripts/qamh list-users
<APP_ROOT>/scripts/qamh bootstrap-admin
<APP_ROOT>/scripts/qamh reset-password <login_id>
<APP_ROOT>/scripts/qamh seed-catalog --product "새 제품"
<APP_ROOT>/scripts/qamh check-storage
<APP_ROOT>/scripts/qamh purge-sessions

# 백업 / 복구
<APP_ROOT>/scripts/backup.sh
sudo <APP_ROOT>/scripts/restore.sh <DATA_ROOT>/backup/<timestamp>

# 배포 (개발 PC)
./deploy/scripts/deploy.sh user@server

# 용량
du -sh <DATA_ROOT>/storage <DATA_ROOT>/backup
df -h /
```

---

## 부록. API

로그인 후 `/api/docs` 에서 OpenAPI 문서를 볼 수 있습니다.
모든 엔드포인트는 세션 쿠키 인증을 요구합니다 (`/api/health` 제외).

| 그룹 | 주요 엔드포인트 |
|---|---|
| 인증 | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `POST /api/auth/change-password` |
| 사용자 (Admin) | `GET/POST /api/users`, `PATCH /api/users/{id}`, `POST /api/users/{id}/reset-password` |
| 제품 | `GET /api/products`, `POST /api/products` (Admin), `PATCH /api/products/{id}` (Admin) |
| 분류 | `GET /api/categories`, `POST /api/categories` (Admin), `PATCH /api/categories/{id}` (Admin) |
| 문서 | `GET/POST /api/documents`, `GET/PATCH /api/documents/{id}`, `POST .../archive`, `POST .../restore` |
| 버전 | `GET/POST /api/documents/{id}/versions`, `PATCH .../versions/{vid}`, `POST .../versions/{vid}/archive`, `.../restore` |
| Current | `POST /api/documents/{id}/set-current` |
| 파일 | `GET .../versions/{vid}/download`, `GET .../versions/{vid}/preview`, `GET /api/documents/{id}/current/download` |
| 중복 확인 | `GET /api/documents/duplicate-check/{sha256}` |
| 검색 | `GET /api/search` |
| 조회 | `GET /api/dashboard`, `GET /api/recent-updates`, `GET /api/audit-logs`, `GET /api/audit-actions`, `GET /api/settings` |
| 헬스 | `GET /api/health` (인증 불필요) |

주요 응답 코드:

| 코드 | 의미 |
|---|---|
| 401 | 로그인 필요 (세션 없음/만료/무효화) |
| 403 | 권한 부족 또는 비활성 계정 |
| 409 | 중복, 상태 충돌 (이미 Current, 이미 보관 등) |
| 413 | 파일 크기 초과 |
| 428 | 비밀번호를 먼저 변경해야 함 |
| 400 | 입력 오류 (확장자, 형식 불일치, Revision/Version 누락 등) |
| 410 | 저장된 파일을 찾을 수 없음 |
