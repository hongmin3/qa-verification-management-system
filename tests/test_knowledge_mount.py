"""지식 폴더 마운트 설정 생성 테스트.

`fstab` 은 공백을 `\\040` 으로 써야 하고 `iocharset=utf8` 이 없으면 한글 파일명이 깨져
**모든 파일이 분류되지 않는다.** 손으로 쓰면 틀리기 쉬운 부분이라 생성기를 두고 그 규칙을
테스트로 고정한다.

환경변수 이름이 `config/products/*.yaml` 과 생성기 사이에서 어긋나면 마운트해도 앱이
경로를 못 찾는다 — 그 드리프트도 여기서 잡는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.product_config import list_product_configs
from scripts.make_knowledge_mount import FSTAB_SPACE, env_var_name, escape_fstab, mount_point, remote_path, render

ROOT = Path(__file__).resolve().parents[1]


# --- fstab 이스케이프 ----------------------------------------------------------


def test_space_becomes_octal_escape() -> None:
    """공백은 fstab 의 필드 구분자다. 그대로 두면 마운트가 실패한다."""
    assert escape_fstab("//host/qa/Bellalun Viewer/지식") == f"//host/qa/Bellalun{FSTAB_SPACE}Viewer/지식"


def test_multiple_spaces_are_all_escaped() -> None:
    assert " " not in escape_fstab("//host/a b c/지식 파일")


def test_backslashes_become_forward_slashes() -> None:
    assert escape_fstab(r"\\host\qa\지식") == "//host/qa/지식"


def test_tab_is_escaped() -> None:
    assert "\t" not in escape_fstab("//host/qa\t지식")


def test_hangul_is_left_as_is() -> None:
    """경로의 한글은 이스케이프 대상이 아니다 — iocharset 이 처리한다."""
    assert "지식" in escape_fstab("//host/qa/지식")


# --- 환경변수 이름 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("product", "expected"),
    [
        ("VXvue", "QA_KNOWLEDGE_DIR_VXVUE"),
        ("Bellalun Viewer", "QA_KNOWLEDGE_DIR_BELLALUN_VIEWER"),
        ("VDMS-1100TM", "QA_KNOWLEDGE_DIR_VDMS_1100TM"),
    ],
)
def test_env_var_name_is_derived_from_the_product(product: str, expected: str) -> None:
    assert env_var_name(product) == expected


def test_configured_products_use_the_generated_env_var_name() -> None:
    """YAML 과 생성기가 어긋나면 마운트해도 앱이 경로를 못 찾는다."""
    from app.core.product_knowledge import product_slug

    mismatched = []
    for config in list_product_configs():
        # slug 파일을 직접 읽는다 — 제품명 문자열로 찾으면 다른 파일의 주석에 걸린다.
        path = ROOT / "config" / "products" / f"{product_slug(config.product)}.yaml"
        if not path.is_file():
            continue
        # `dir:` 줄만 본다 — 파일 전체를 훑으면 주석의 예시(`${ENV:-기본값}`)에 걸린다.
        dir_line = next(
            (line for line in path.read_text(encoding="utf-8").splitlines() if line.strip().startswith("dir:")),
            "",
        )
        match = re.search(r"\$\{([A-Z0-9_]+):-", dir_line)
        if match is None:
            continue
        if match.group(1) != env_var_name(config.product):
            mismatched.append(f"{config.product}: YAML={match.group(1)} 생성기={env_var_name(config.product)}")
    assert mismatched == [], f"환경변수 이름 불일치: {mismatched}"


# --- 마운트 지점과 원격 경로 ---------------------------------------------------


def test_mount_point_uses_the_product_slug() -> None:
    assert mount_point("Bellalun Viewer") == "/srv/knowledge/bellalun-viewer"


def test_remote_path_keeps_the_last_two_segments() -> None:
    """서버 공유 구조는 PC 디렉터리 구조와 다르다 — 제품 폴더와 그 부모만 옮긴다."""
    result = remote_path("C:/Users/me/Documents/자동화/VXvue/VXvue 지식파일", "//10.13.0.5/qa")
    assert result == "//10.13.0.5/qa/VXvue/VXvue 지식파일"


def test_remote_path_uses_a_placeholder_without_share() -> None:
    result = remote_path("C:/Users/me/Documents/자동화/VXvue/VXvue 지식파일", "")
    assert result.startswith("//<서버>/<공유>")


def test_remote_path_drops_the_drive_letter() -> None:
    assert "C:" not in remote_path("C:/지식", "//host/qa")


def test_remote_path_survives_an_unset_source_dir() -> None:
    assert remote_path("", "//host/qa") == "//host/qa"


# --- 생성 결과 ----------------------------------------------------------------


def _rendered() -> str:
    return render("VXvue", "C:/Users/me/Documents/자동화/VXvue/VXvue 지식파일", "//10.13.0.5/qa", "qauser")


def test_rendered_fstab_line_has_the_required_options() -> None:
    output = _rendered()
    fstab_line = next(line for line in output.splitlines() if " cifs " in line)
    # 한글 파일명이 깨지지 않게 하는 옵션. 없으면 모든 파일이 분류되지 않는다.
    assert "iocharset=utf8" in fstab_line
    # 앱은 이 폴더를 읽기만 한다.
    assert fstab_line.split("cifs")[1].lstrip().startswith("ro,")
    assert FSTAB_SPACE in fstab_line
    assert "uid=qauser" in fstab_line


def test_rendered_output_includes_the_env_var_line() -> None:
    assert "QA_KNOWLEDGE_DIR_VXVUE=/srv/knowledge/vxvue" in _rendered()


def test_rendered_output_includes_a_verification_step() -> None:
    """마운트 직후 한글 파일명을 눈으로 확인하는 단계가 있어야 한다."""
    output = _rendered()
    assert "ls -1 /srv/knowledge/vxvue" in output


def test_rendered_output_does_not_contain_credentials() -> None:
    output = _rendered()
    assert "password=<비밀번호>" in output  # 자리표는 있어야 한다
    assert "chmod 600" in output
