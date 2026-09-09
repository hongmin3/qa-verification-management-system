import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REAL_DB = Path(__file__).resolve().parents[1] / "data" / "app.db"


def _document_rows() -> int | None:
    """실서비스 `documents` 행 수. DB 가 없는 환경(CI 최초 실행)에서는 None."""
    if not REAL_DB.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True) as db:
            return db.execute("select count(*) from documents").fetchone()[0]
    except sqlite3.Error:
        return None


@pytest.fixture(autouse=True)
def _no_writes_to_the_real_database():
    """테스트가 개발용 `data/app.db` 의 `documents` 를 건드리면 그 자리에서 실패시킨다.

    테스트가 직접 `Storage()` 를 만들지 않아도 오염될 수 있다 — `register_collected` 처럼
    안에서 기본 `Storage()` 를 만드는 코드를 부르면 그 경로로 들어간다. 실제로 지식 업로드
    테스트를 처음 쓸 때 개발 DB 에 문서 5건이 등록됐고, 등록 로직이 "원본 파일 없음"으로
    판단한 기존 등록을 지울 뻔했다.

    `documents` 만 본다. 이 테이블이 분석 대상을 정하므로 오염되면 결과가 조용히 달라진다
    (`ai_cache` 같은 캐시는 지워도 다시 만들어진다).

    걸리면 그 테스트에 격리된 `root`/`storage` 를 넘겨라 —
    `tests/test_knowledge_upload.py` 의 `server` fixture 가 예시다.
    """
    before = _document_rows()
    yield
    after = _document_rows()
    if before is not None and after is not None and before != after:
        pytest.fail(
            f"이 테스트가 실서비스 DB({REAL_DB})의 documents 를 바꿨습니다 "
            f"({before} → {after}행). 격리된 Storage(db_path=...) 와 root 를 넘기세요."
        )
