"""HTML -> PDF 변환 실작업을 별도 프로세스로 격리 실행하기 위한 워커.

일부 SRS Rich Text 콘텐츠가 Chromium의 인쇄 페이지네이션에서 수만 페이지짜리
무한 팽창을 유발하는 사례가 실제로 발견되었다(원인 HTML 요소는 특정하지 못함).
이 워커를 별도 프로세스로 실행하고 부모 프로세스에서 시간제한을 걸어, 특정 SRS
때문에 전체 자동화가 20분 이상 멈추는 일을 막는다 - 시간 초과 시 부모가 이
프로세스를 강제 종료(taskkill /T /F)해도 안전하도록 별도 프로세스로 분리했다.

사용법: python -m src.pdf_worker <html_path> <pdf_path>
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    html_path, pdf_path = sys.argv[1], sys.argv[2]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path)
        page.emulate_media(media="print")
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
        )
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
