"""공용 진행원의 중단 요청·다른 사용자 상태·종료 표시. Validates: REQ-QAINTEL-021, REQ-QAINTEL-036, REQ-QAINTEL-042."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", ["cancel", "shared", "rejected", "lost", "email", "prepared"])
def test_common_progress_cancel_behavior(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("공용 JavaScript 실행 검사는 Node.js가 필요합니다")
    result = subprocess.run([node, str(ROOT / "tests/qa_run_progress_ui.cjs"), scenario], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
