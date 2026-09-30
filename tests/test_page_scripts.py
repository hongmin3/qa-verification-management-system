"""화면 안 스크립트가 문법 오류 없이 읽히는지 검사한다.

`<script>` 블록 하나에 문법 오류가 있으면 그 블록의 모든 동작(삭제 확인 창, `지금 수집` 결과 알림,
`죽은 등록 정리` 버튼, 제품 필터)이 한꺼번에 멈춘다. 화면 제목·링크만 보는 테스트로는 잡히지 않는다
(docs/SPEC_CODE_MISMATCH.md 5절 1번).

node 가 있으면 `node --check` 로 검사하고, 없으면 작은따옴표·큰따옴표 문자열 안에 줄바꿈이 그대로
들어간 곳만 찾는 최소 검사를 한다.
"""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app

SCRIPT_BLOCK = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)

PAGES = [
    "/",
    "/knowledge",
    "/knowledge/guide",
    "/cost-dashboard",
    "/cost-dashboard/guide",
]


def inline_scripts(html: str) -> list[str]:
    scripts = []
    for match in SCRIPT_BLOCK.finditer(html):
        attrs = match.group("attrs").lower()
        if "src=" in attrs or "application/json" in attrs or "application/ld+json" in attrs:
            continue
        if match.group("body").strip():
            scripts.append(match.group("body"))
    return scripts


def unterminated_quote_lines(source: str) -> list[int]:
    """작은따옴표·큰따옴표 문자열이 끝나기 전에 줄이 바뀐 줄 번호. 주석과 템플릿 문자열은 건너뛴다."""
    problems: list[int] = []
    i, line, n = 0, 1, len(source)
    template_depth: list[int] = []  # `${` 중첩마다 중괄호 깊이
    in_template = False
    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if in_template:
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                in_template = False
            elif source.startswith("${", i):
                template_depth.append(0)
                in_template = False
                i += 2
                continue
            i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = n if end < 0 else end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            chunk = source[i:(n if end < 0 else end + 2)]
            line += chunk.count("\n")
            i = n if end < 0 else end + 2
            continue
        if ch in "'\"":
            start_line = line
            i += 1
            while i < n and source[i] != ch:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == "\n":
                    break
                i += 1
            if i < n and source[i] == "\n":
                # 줄이 바뀐 곳에서 문자열을 끝난 것으로 보고, 같은 줄의 나머지는 다시 읽지 않는다.
                problems.append(start_line)
                end = source.find("\n", i + 1)
                line += 1
                i = n if end < 0 else end
                continue
            i += 1
            continue
        if ch == "`":
            in_template = True
        elif template_depth:
            if ch == "{":
                template_depth[-1] += 1
            elif ch == "}":
                if template_depth[-1] == 0:
                    template_depth.pop()
                    in_template = True
                else:
                    template_depth[-1] -= 1
        i += 1
    return problems


def syntax_error(source: str, tmp_path, name: str) -> str | None:
    node = shutil.which("node")
    if node:
        path = tmp_path / f"{name}.cjs"
        path.write_text(source, encoding="utf-8")
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8")
        return None if result.returncode == 0 else (result.stderr or result.stdout).strip()
    lines = unterminated_quote_lines(source)
    return f"문자열 안 줄바꿈: {lines}행" if lines else None


def test_minimal_checker_finds_raw_newline_in_quoted_string():
    # Validates: REQ-KNOW-001
    broken = "alert('첫 줄\n둘째 줄');\n"
    fine = "alert('첫 줄\\n둘째 줄'); const t = `여러\n줄 ${'a'}`; // '주석\n"
    assert unterminated_quote_lines(broken) == [1]
    assert unterminated_quote_lines(fine) == []


def test_checker_rejects_broken_script(tmp_path):
    # Validates: REQ-KNOW-001
    assert syntax_error("alert('a\n');", tmp_path, "broken") is not None
    assert syntax_error("alert('a\\n');", tmp_path, "fine") is None


# Validates: REQ-KNOW-001, REQ-KNOW-005, REQ-KNOW-013
@pytest.mark.parametrize("page", PAGES)
def test_page_inline_scripts_parse(page, tmp_path):
    response = TestClient(app).get(page)
    assert response.status_code == 200, page
    errors = []
    for index, script in enumerate(inline_scripts(response.text)):
        error = syntax_error(script, tmp_path, f"script{index}")
        if error:
            errors.append(f"{page} <script> #{index}: {error}")
    assert not errors, "\n".join(errors)


def test_knowledge_page_has_scripts():
    """검사 대상이 비어 있어 통과하는 일을 막는다."""
    # Validates: REQ-KNOW-001
    response = TestClient(app).get("/knowledge")
    scripts = inline_scripts(response.text)
    assert scripts
    assert any("cleanup-missing" in script for script in scripts)
