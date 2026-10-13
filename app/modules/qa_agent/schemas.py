"""공용 Regression 축과 화면 표시 이름 (규칙 §32)."""

# --- S05 Regression 축 (규칙 §32) ---------------------------------------------
# 축은 코드가 고정한다. 모델은 "이 축에 해당하는가"와 그 근거만 채운다. 축을 모델이 만들면
# 실행마다 달라져 결과를 비교할 수 없다.

AXIS_DIRECT = "DIRECT"
AXIS_STATE = "STATE"
AXIS_DATA = "DATA"
AXIS_PERSISTENCE = "PERSISTENCE"
AXIS_INTEGRATION = "INTEGRATION"
AXIS_PERMISSION = "PERMISSION"
AXIS_PRIOR_ISSUE = "PRIOR_ISSUE"
AXIS_GENERATOR = "GENERATOR"

REGRESSION_AXES: tuple[tuple[str, str, str], ...] = (
    (AXIS_DIRECT, "직접", "변경된 기능 자체"),
    (AXIS_STATE, "상태", "A→B, B→A, A→B→A 전이 · Error→정상 · 연결→해제→재연결"),
    (AXIS_DATA, "데이터", "Cache · ErrorCode · List · Patient · Study 잔존·오염"),
    (AXIS_PERSISTENCE, "저장", "Save · Reload · 재진입 · 재시작 후 값 유지"),
    (AXIS_INTEGRATION, "연동", "DICOM · API · WebSocket · 외부 장치"),
    (AXIS_PERMISSION, "권한", "계정 권한별 노출·동작 차이"),
    (AXIS_PRIOR_ISSUE, "기존 Issue", "같은 Root Cause 로 과거에 났던 Issue 의 재발"),
    (AXIS_GENERATOR, "Generator", "Step · Size · Study · Dose Mode · Focal Spot · Save Dose"),
)

AXIS_LABELS = {code: label for code, label, _ in REGRESSION_AXES}
AXIS_DESCRIPTIONS = {code: description for code, _, description in REGRESSION_AXES}
