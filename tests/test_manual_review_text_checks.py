"""Validates: REQ-MANUAL-020 — 문장 점검 (TEST-MANUAL-023)."""

from app.modules.manual_review.text_checks import duplicate_phrase_issue, paragraph_issues, text_issues_by_change


def _codes(text: str) -> list[str]:
    return [issue["code"] for issue in paragraph_issues(text)]


def test_duplicate_word_is_found_in_korean_and_english():
    assert _codes("설정 설정 화면에서 값을 바꿉니다.") == ["DUPLICATE_WORD"]
    assert _codes("Click the the Save button.") == ["DUPLICATE_WORD"]
    assert _codes("설정 화면에서 설정값을 바꿉니다.") == []


def test_merged_sentence_is_found_but_versions_and_file_names_are_not():
    assert _codes("라이선스를 관리합니다.할 수 있습니다.") == ["MERGED_SENTENCE"]
    assert _codes("Open the screen.Click Save.") == ["MERGED_SENTENCE"]
    assert _codes("VXvue 1.1.0 버전의 config.yaml 파일을 엽니다. 다음을 누릅니다.") == []


def test_double_punctuation_ignores_ellipsis_and_decimal_numbers():
    assert _codes("비활성화됩니다..") == ["DOUBLE_PUNCTUATION"]
    assert _codes("값을 입력하고,, 저장합니다") == ["DOUBLE_PUNCTUATION"]
    assert _codes("설정 . 다음") == ["DOUBLE_PUNCTUATION"]
    assert _codes("잠시 기다리십시오... 값은 .5 단위입니다") == []


def test_double_space_between_characters():
    assert _codes("Preview 버튼  을 클릭합니다.") == ["DOUBLE_SPACE"]
    assert _codes("Preview 버튼을 클릭합니다.") == []


def test_excerpt_is_short_and_contains_the_problem():
    issue = paragraph_issues("앞부분이 아주 긴 문장입니다 그리고 이어서 설명이 계속됩니다 설정 설정 다음 내용도 아주 깁니다 정말로 깁니다")[0]
    assert "설정 설정" in issue["excerpt"]
    assert len(issue["excerpt"]) <= 40
    assert issue["label"] == "같은 단어 반복"


def test_duplicate_phrase_needs_two_words_and_insertion():
    paragraph = "Security 메뉴에서 보안 옵션을 설정합니다.Security 메뉴에서 보안 옵션을 설정합니다."
    assert duplicate_phrase_issue("insertion", "Security 메뉴에서 보안 옵션을 설정합니다.", paragraph)["code"] == "DUPLICATE_PHRASE"
    assert duplicate_phrase_issue("deletion", "Security 메뉴에서 보안 옵션을 설정합니다.", paragraph) is None
    # 한 단어짜리 추가는 정상 문장에도 두 번 나온다.
    assert duplicate_phrase_issue("insertion", "Exposure", "Exposure 모드에서 Exposure Guide 버튼을 표시합니다.") is None


def test_paragraph_issues_go_to_first_functional_change_only():
    paragraph = "설정 설정 화면입니다."
    rows = [
        ("a", "deletion", "12", paragraph, 3, None, False),  # 단순 변경에는 붙이지 않는다
        ("b", "insertion", "화면", paragraph, 3, None, True),
        ("c", "insertion", "입니다", paragraph, 3, None, True),
        ("d", "pdf_modification", "설정 설정", "설정 설정", 4, 2, True),  # PDF 는 보지 않는다
        ("e", "image_insertion", "이미지 변경", "설정 설정", 5, None, True),  # 이미지도 보지 않는다
    ]
    result = text_issues_by_change(rows)
    assert list(result) == ["b"]
    assert [issue["code"] for issue in result["b"]] == ["DUPLICATE_WORD"]
