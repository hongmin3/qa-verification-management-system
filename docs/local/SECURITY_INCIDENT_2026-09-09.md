# 공개 저장소 사내 데이터 노출 — 2026-09-09

이 파일은 `docs/local/` 에 있다. 공개 저장소일 때는 "어느 커밋에 무엇이 있었다"를 적으면
찾아가는 길을 알려주는 셈이라 Git 에서 뺐다. 2026-09-30 에 저장소를 비공개로 바꾼 뒤 함께 올린다.

> 2026-09-30: 저장소가 비공개가 되어 아래 옛 커밋 주소는 외부에서 더 열리지 않는다. 다시 공개로
> 돌릴 계획이 있으면 그 전에 Support 삭제 요청을 끝내야 한다.

## 무슨 일이 있었나

모델 비교(`scripts/compare_models.py`) 작업 중 만든 중간 산출물 2개가 커밋에 섞여 들어가
공개 저장소(github.com/hongmin3/qa-verification-management-system)에 푸시됐다.

| | |
|---|---|
| 커밋 | `e08068a` (2026-09-08) — 이후 `6051a68` 까지 공개 상태로 유지 |
| 파일 | `.pro_compare_payload.json` (25,199자), `.pro_compare_suffix.txt` (5,019자) |
| 성격 | 파이프라인이 조립한 Evidence Pack **원문**. `GeminiClient` 마스킹을 거치기 전 상태다 |

## 실제로 무엇이 들어 있었나 (전수 확인함)

- **실제 Polarion Issue 28건**의 ID 와 본문 — 변경 내용, Root Cause, TC 단계, 사양서 발췌
- **사내 IP `10.14.0.170`** 4회
- QA 작성 규칙 문서 발췌 (`.pro_compare_suffix.txt`)

확인했으나 **없었던 것**:

- 개인정보 — `환자`/`Patient` 37건은 전부 기능 이름·TC 단계였다 (`Edit Patient 화면`,
  `Patient Birth Date`). 실제 환자 데이터가 아니다.
- 직원 이름 — `책임` 은 "호출자 책임", "승인 책임" 같은 일반 명사였다 (최초 자동 스캔의 오탐).
- 자격증명 — 이메일·API 키 없음. `tests/test_secrets_file.py` 의 `AIza-test-key-0000…` 은
  합성 fixture 다. `secrets.txt` 는 계속 gitignore 상태였다.

## 조치한 것

1. 작업 트리에서 제거하고 `.gitignore` 에 패턴 등록 (`.pro_compare_*`, `*.payload.json`,
   `scratch/`) — 커밋 `4724ae3`
2. `git filter-repo` 로 전체 히스토리에서 두 파일 제거 (커밋 108개는 그대로 유지)
3. `origin/master` 에 force push (`6051a68` → `b6724be`)
4. 원격 히스토리에 두 파일과 `10.14.0.170` 이 남아 있지 않음을 확인
5. 재작성 전 백업 bundle 을 세션 scratchpad 에 보관 (세션 종료 시 사라진다 — 필요하면 옮길 것)

## 아직 남은 것 — 사람이 해야 한다

**옛 커밋 SHA 로는 여전히 접근된다.** force push 는 참조만 끊을 뿐 객체를 지우지 않는다.
확인한 결과 아래가 아직 HTTP 200 을 반환한다.

    https://github.com/hongmin3/qa-verification-management-system/commit/e08068a
    https://raw.githubusercontent.com/hongmin3/qa-verification-management-system/e08068a/.pro_compare_payload.json

GitHub 은 도달 불가 객체를 스스로 지우지 않으므로 **Support 에 삭제를 요청해야 한다.**

    https://support.github.com/contact  →  "Removing sensitive data"

요청에 넣을 내용:

    Repository: hongmin3/qa-verification-management-system
    Please permanently remove the cached view and blobs for commit e08068a
    (and any other unreachable objects). Sensitive files .pro_compare_payload.json
    and .pro_compare_suffix.txt were force-pushed out of history on 2026-09-09.

Fork 이 있으면 Support 에 함께 알려야 한다 (Fork 에는 force push 가 전파되지 않는다).
지금 이 저장소에는 Fork 가 없는 것으로 보이지만 요청 전에 다시 확인할 것.

## 재발 방지

- `.gitignore` 패턴 (조치 1)
- 진단 산출물은 저장소가 아니라 세션 scratchpad 에 쓴다
- Evidence Pack 원문을 파일로 남길 일이 있으면 `mask_text()` 를 통과시킨 뒤 남긴다
