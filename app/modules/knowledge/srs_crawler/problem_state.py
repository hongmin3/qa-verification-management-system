"""렌더링 문제 SRS의 '확인 당시 본문 해시' 상태 저장소.

config의 `render.known_problem_srs`만으로 이분 탐색을 건너뛰면, 그 사이에 원본 SRS가
수정되어 정상 렌더링이 가능해졌더라도 계속 서식 단순화 상태로 남는 문제가 생긴다.
그래서 "문제로 확인했을 때의 본문 해시"를 함께 저장하고, 다음 실행에서 본문이
바뀌었으면 캐시를 무시하고 정상 렌더링을 다시 시도한다.

- 본문이 그대로다  -> 이분 탐색 생략 (약 10분 절약)
- 본문이 바뀌었다  -> 캐시 무시하고 재확인 (수정 반영 기회를 놓치지 않음)

본문 해시만으로는 부족하다. 렌더링 결과는 SRS 본문뿐 아니라 **렌더링 파이프라인
자체**(HTML 템플릿/CSS = `render.py`, Playwright 인쇄 옵션 = `pdf_worker.py`)에도
좌우된다. 템플릿이나 인쇄 옵션을 고쳐서 폭주가 해소되었는데도 본문 해시가 같아
캐시가 계속 적용되면, 개선이 반영되지 않는다. 그래서 캐시 키에 렌더링 모듈의
해시도 함께 포함해, 파이프라인이 바뀌면 전부 재확인하도록 한다.

주의: SRS 본문 자체의 수집/Snapshot/Diff는 이 캐시와 무관하게 항상 정상 수행된다.
캐시가 영향을 주는 범위는 "PDF 안에서 서식을 단순화할지 여부"뿐이다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("srs_automation")

STATE_FILENAME = "render_problem_state.json"

# 렌더링 결과에 직접 영향을 주는 모듈. 이 파일들이 바뀌면 캐시를 전부 무효화한다.
_RENDER_PIPELINE_FILES = ("render.py", "pdf_worker.py")


def content_hash(content_html: str | None) -> str:
    return hashlib.sha256((content_html or "").encode("utf-8")).hexdigest()


def render_pipeline_hash() -> str:
    """렌더링 파이프라인(HTML 템플릿/CSS + Playwright 인쇄 옵션)의 해시.

    파일을 읽을 수 없는 예외 상황에서는 그 파일의 내용을 해시에 넣지 않는다. 그러면
    정상 상태와 해시가 달라져 캐시가 무효화되므로, '확인을 건너뛰는' 쪽이 아니라
    '다시 확인하는' 쪽으로 안전하게 기운다.
    """
    here = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in _RENDER_PIPELINE_FILES:
        try:
            h.update((here / name).read_bytes())
        except OSError as exc:
            logger.warning("렌더링 파이프라인 해시 계산 실패 (%s): %s - 캐시를 무효화합니다.", name, exc)
        h.update(b"|")  # 파일 경계 구분자
    return h.hexdigest()


@dataclass
class ProblemState:
    """uid -> {content_sha256, render_pipeline_sha256, recorded_at, title, page_count_observed}"""

    path: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    dirty: bool = False

    @classmethod
    def load(cls, snapshots_dir: Path) -> "ProblemState":
        path = snapshots_dir / STATE_FILENAME
        entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    entries = {k: v for k, v in loaded.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError) as exc:
                # 상태 파일은 성능 최적화용 캐시일 뿐이므로, 깨져 있으면 버리고 새로 만든다.
                logger.warning("렌더링 문제 상태 파일을 읽을 수 없어 무시합니다 (%s): %s", path, exc)
        return cls(path=path, entries=entries)

    def is_still_valid(self, uid: str, current_content_html: str | None) -> bool:
        """'문제 있음'으로 기록된 시점과 지금이 본문·렌더링 파이프라인 모두 동일한가.

        둘 중 하나라도 바뀌었으면 캐시를 신뢰하지 않고 정상 렌더링부터 재확인한다.
        """
        entry = self.entries.get(uid)
        if not entry:
            return False
        if entry.get("content_sha256") != content_hash(current_content_html):
            return False
        recorded_pipeline = entry.get("render_pipeline_sha256")
        if recorded_pipeline != render_pipeline_hash():
            logger.warning(
                "%s 는 렌더링 문제로 기록되어 있으나 그 이후 렌더링 파이프라인"
                "(HTML 템플릿/인쇄 옵션)이 변경되었습니다 - 캐시를 무효화하고 재확인합니다.",
                uid,
            )
            return False
        return True

    def record(self, uid: str, *, content_html: str | None, title: str | None, page_count: int | None = None) -> None:
        self.entries[uid] = {
            "content_sha256": content_hash(content_html),
            "render_pipeline_sha256": render_pipeline_hash(),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
            "title": title,
            "page_count_observed": page_count,
        }
        self.dirty = True

    def forget(self, uid: str) -> None:
        if uid in self.entries:
            del self.entries[uid]
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        self.dirty = False
        logger.info("렌더링 문제 상태 저장: %s (%d건)", self.path, len(self.entries))
