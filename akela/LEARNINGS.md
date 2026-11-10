# Learnings

One `## LRN-YYYYMMDD-NN: title` block per learning. Fields: Status · Scope · Statement · Overrides · Evidence · Fingerprint (optional) · Profile (optional).

## LRN-20261110-01: Windows pytest basetemp는 저장소 밖의 짧은 경로에 둔다
- **Status:** active
- **Scope:** testing
- **Statement:** Windows에서 pytest --basetemp를 지정할 때는 저장소 밖의 짧은 경로(예: %LOCALAPPDATA%/Temp/qapt)를 쓴다. 저장소 안이나 CLAUDE.md·AGENTS.md가 있는 상위 폴더 아래면 Claude CLI 격리 검사(validate_workdir)가 실행을 거부해 claude_cli·QA Agent 시험이 대량 실패하고, 긴 경로는 Windows 경로 길이 제한으로 Bellalun 공유 시험이 FileNotFoundError로 실패한다. 이 실패들은 제품 결함이 아니다.
- **Overrides:** none
- **Evidence:** 2026-11-10 신규 PC 전체 시험. `--basetemp=data/test-...`(저장소 안)에서는 `test_claude_cli_provider.py`가 "작업 폴더 또는 그 상위 폴더에 ...CLAUDE.md 가 있어" 오류로 실패했고, 같은 파일이 저장소 밖 basetemp의 `791e144` 깨끗한 사본에서는 50 passed였다. Claude 작업 scratchpad 아래 긴 basetemp에서는 `test_bellalun_shared.py`가 "vxvue-desktop source lookup failed (FileNotFoundError)"로 실패했고, `C:/Users/<user>/AppData/Local/Temp/qapt1110`에서는 같은 파일과 claude_cli 시험이 31 passed였다. 짧은 경로로 다시 돌린 전체 시험은 2418 passed·12 skipped·1 failed(브라우저 부하, 단독 재실행 통과)였다. 사용자가 학습 등록을 요청하고 이 근거를 승인했다.
