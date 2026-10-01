"""제품 어댑터: Polarion 원본 응답 → 공통 모델 (SPEC REQ-QAINTEL-002, NFR-QAINTEL-002).

제품마다 다른 것은 필드 이름, 연구소 결과 값, 조회식이다. 그 차이는 제품 설정
(`config/products/<slug>.yaml` 의 `alm:`)과 이 파일에서만 흡수한다. 이벤트 감지·분석·저장·화면은
여기서 만든 공통 모델만 본다 — `rndReviewResult` 같은 원본 이름을 모른다.

공통 모델은 스냅샷에 그대로 저장하는 사전(dict)이다. SRS 기록의 키(`id`, `old_id`, `title`,
`status`, `updated`, `is_category`, `text`)는 개편 전 스냅샷과 같아서 옛 스냅샷도 비교 기준이 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.product_config import ProductConfig, list_product_configs, load_product_config

#: 공통 연구소 결과 값. 제품 설정의 `rd_result_mapping` 이 원본 값을 이 값으로 바꾼다.
RD_FIXED = "FIXED"
RD_SPEC = "SPEC"
RD_NOT_BUG = "NOT_BUG"
RD_DUPLICATE = "DUPLICATE"
RD_PENDING = "PENDING"
RD_NO_ACTION = "NO_ACTION"
RD_UNSET = "UNSET"
RD_OTHER = "OTHER"
RD_VALUES = (RD_FIXED, RD_SPEC, RD_NOT_BUG, RD_DUPLICATE, RD_PENDING, RD_NO_ACTION, RD_UNSET, RD_OTHER)
#: 사양대로 동작한다는 판정 (Spec 판정 이슈 분석 대상).
RD_SPEC_LIKE = frozenset({RD_SPEC, RD_NOT_BUG})

RD_LABELS = {
    RD_FIXED: "수정 완료",
    RD_SPEC: "사양대로",
    RD_NOT_BUG: "결함 아님",
    RD_DUPLICATE: "중복",
    RD_PENDING: "보류",
    RD_NO_ACTION: "조치 없음",
    RD_UNSET: "미정",
    RD_OTHER: "기타",
}

#: Polarion 표준 필드. 제품 설정에 적지 않으면 이 이름을 쓴다.
STANDARD_SRS_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id",),
    "title": ("title",),
    "status": ("status",),
    "updated": ("updated",),
    "description": ("description",),
    "type": ("type",),
}
STANDARD_ISSUE_FIELDS: dict[str, tuple[str, ...]] = {
    "id": ("id",),
    "title": ("title",),
    "status": ("status",),
    "severity": ("severity",),
    "description": ("description",),
    "created": ("created",),
    "updated": ("updated",),
}
STANDARD_RELATIONS = {"linked_items": "linkedWorkItems", "comments": "comments"}

COMMENT_TEXT_LIMIT = 2000
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ProductProfile:
    """공통 엔진이 제품에 대해 아는 전부."""

    product: str
    slug: str
    project_id: str
    srs_query: str
    issue_query: str
    srs_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    issue_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    relations: dict[str, str] = field(default_factory=dict)
    rd_mapping: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    supported_rules_rev: str = ""
    product_skill: str = ""
    regression_axes: tuple[str, ...] = ()
    checklist: dict = field(default_factory=dict)
    comment_noise_patterns: tuple[str, ...] = ()
    manual_types: tuple[str, ...] = ()
    #: 제품 규칙 Skill 이 있는 폴더 (`config/products/<slug>/skills`).
    skills_dir: Path | None = None

    def rd_common(self, raw: str) -> str:
        value = (raw or "").strip()
        if not value:
            return RD_UNSET
        mapped = self.rd_mapping.get(value) or self.rd_mapping.get(value.casefold())
        return mapped if mapped in RD_VALUES else RD_OTHER


def slug_of(product: str) -> str:
    from app.core.product_knowledge import product_slug

    return product_slug(product)


def _names(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(item) for item in value or () if item)


def _find_config(product: str, root: Path | None) -> ProductConfig | None:
    for name in (product, slug_of(product)):
        config = load_product_config(name, root)
        if config is not None:
            return config
    for config in list_product_configs(root):
        if config.product.casefold() == product.casefold() or slug_of(config.product) == slug_of(product):
            return config
    return None


def build_profile(config: ProductConfig | None, product: str, root: Path | None = None, fallback: dict | None = None) -> ProductProfile:
    """제품 설정으로 프로필을 만든다. `alm:` 절이 없으면 `fallback`(config.yaml 의 daily_qa.polarion)을 쓴다."""
    fallback = fallback or {}
    alm = config.alm if config else None
    intel = config.qa_intelligence if config else None
    srs_fields = dict(STANDARD_SRS_FIELDS)
    issue_fields = dict(STANDARD_ISSUE_FIELDS)
    relations = dict(STANDARD_RELATIONS)
    if alm:
        srs_fields.update({name: _names(value) for name, value in alm.fields.srs.items()})
        issue_fields.update({name: _names(value) for name, value in alm.fields.issue.items()})
        relations.update({name: value for name, value in alm.relations.items() if value})
    display = config.product if config else product
    slug = slug_of(display)
    base = root or Path(__file__).resolve().parents[3]
    skills_dir = base / "config" / "products" / slug / "skills"
    checklist = intel.checklist.model_dump() if intel else {}
    return ProductProfile(
        product=display,
        slug=slug,
        project_id=(alm.project_id if alm and alm.project_id else str(fallback.get("project_id") or "")),
        srs_query=(alm.queries.srs if alm and alm.queries.srs else str(fallback.get("srs_query") or "type:srs")),
        issue_query=(alm.queries.issue if alm and alm.queries.issue else str(fallback.get("issue_query") or "type:issue")),
        srs_fields=srs_fields,
        issue_fields=issue_fields,
        relations=relations,
        rd_mapping={str(key): str(value).upper() for key, value in (alm.rd_result_mapping.items() if alm else ())},
        enabled=intel.enabled if intel else True,
        supported_rules_rev=intel.rules.supported_rev if intel else "",
        product_skill=intel.rules.product_skill if intel else "",
        regression_axes=tuple(intel.regression_axes) if intel else (),
        checklist=checklist,
        comment_noise_patterns=tuple(intel.comment_noise_patterns) if intel else (),
        manual_types=tuple(config.manual_types) if config else (),
        skills_dir=skills_dir if skills_dir.is_dir() else None,
    )


def load_profile(product: str, root: Path | None = None, fallback: dict | None = None) -> ProductProfile:
    return build_profile(_find_config(product, root), product, root, fallback)


# -- 원본 값 읽기 --------------------------------------------------------------


def _rich_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or "")
    return str(value or "")


def _enum_text(value: Any) -> str:
    """열거형 필드는 문자열 또는 `{"id": ...}` 로 온다."""
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or "")
    return str(value or "")


def _first(attrs: dict, names: tuple[str, ...]) -> Any:
    for name in names:
        value = attrs.get(name)
        if value not in (None, "", {}) and not (isinstance(value, dict) and not value.get("value") and "id" not in value):
            return value
    return None


def _text(attrs: dict, names: tuple[str, ...]) -> str:
    from app.parsers.polarion_issue import html_to_text

    value, _ = html_to_text(_rich_text(_first(attrs, names)))
    return value.strip()


def _relationship_ids(relationships: dict, name: str) -> list[str] | None:
    """관계의 대상 번호. 관계 자체가 응답에 없으면 None(모름)이다."""
    if not name or name not in (relationships or {}):
        return None
    node = relationships.get(name) or {}
    data = node.get("data")
    if isinstance(data, dict):
        data = [data]
    ids = []
    for entry in data or []:
        raw = str((entry or {}).get("id") or "")
        if raw:
            ids.append(raw)
    return ids


def _short(raw: str) -> str:
    return raw.split("/")[-1]


def normalize_srs(item: dict[str, Any], profile: ProductProfile) -> dict[str, Any]:
    """SRS Work Item 하나를 공통 모델(스냅샷 항목)로 바꾼다. 본문은 HTML 을 걷어낸 텍스트다."""
    attrs = item.get("attributes") or {}
    fields = profile.srs_fields
    item_id = str(_first(attrs, fields.get("id", ("id",))) or _short(str(item.get("id") or "")))
    legacy = _first(attrs, fields.get("legacy_id", ()))
    category = _first(attrs, fields.get("is_category", ()))
    return {
        "id": item_id,
        "old_id": str(legacy or ""),
        "title": str(_first(attrs, fields.get("title", ())) or ""),
        "status": _enum_text(_first(attrs, fields.get("status", ()))),
        "updated": str(_first(attrs, fields.get("updated", ())) or ""),
        "is_category": bool(category) or _enum_text(_first(attrs, fields.get("type", ()))) == "category",
        "text": _text(attrs, fields.get("description", ())),
    }


def normalize_issue(item: dict[str, Any], profile: ProductProfile) -> dict[str, Any]:
    """이슈 Work Item 을 공통 모델로 바꾼다. 첨부 이미지는 넣지 않는다. 댓글은 수집기가 채운다."""
    attrs = item.get("attributes") or {}
    relationships = item.get("relationships") or {}
    fields = profile.issue_fields
    relations = profile.relations
    rd_raw = _enum_text(_first(attrs, fields.get("rd_result", ())))
    linked = _relationship_ids(relationships, relations.get("linked_items", "")) or []
    comment_ids = _relationship_ids(relationships, relations.get("comments", ""))
    return {
        "id": str(_first(attrs, fields.get("id", ("id",))) or _short(str(item.get("id") or ""))),
        "title": str(_first(attrs, fields.get("title", ())) or ""),
        "status": _enum_text(_first(attrs, fields.get("status", ()))),
        "severity": _enum_text(_first(attrs, fields.get("severity", ()))),
        "rd_result_raw": rd_raw,
        "rd_result": profile.rd_common(rd_raw),
        "created": str(_first(attrs, fields.get("created", ())) or ""),
        "updated": str(_first(attrs, fields.get("updated", ())) or ""),
        "description": _text(attrs, fields.get("description", ())),
        "reproduction_step": _text(attrs, fields.get("reproduction_step", ())),
        "occurrence_cause": _text(attrs, fields.get("occurrence_cause", ())),
        "action_details": _text(attrs, fields.get("action_details", ())),
        "occurred_versions": sorted(_short(value) for value in _relationship_ids(relationships, relations.get("occurred_versions", "")) or []),
        "target_versions": sorted(_short(value) for value in _relationship_ids(relationships, relations.get("target_versions", "")) or []),
        # linkedWorkItems 의 번호는 "VXvue/VP-1/relates_to/VXvue/VP-2" 모양이다. 마지막 조각이 대상이다.
        "linked_ids": sorted({_short(value) for value in linked}),
        "comment_ids": sorted(comment_ids) if comment_ids is not None else None,
        "comments": None,
    }


def normalize_comment(comment: dict[str, Any]) -> dict[str, str]:
    from app.parsers.polarion_issue import html_to_text

    attrs = comment.get("attributes") or {}
    text, _ = html_to_text(_rich_text(attrs.get("text")))
    return {
        "id": str(comment.get("id") or attrs.get("id") or ""),
        "created": str(attrs.get("created") or ""),
        "text": text.strip()[:COMMENT_TEXT_LIMIT],
    }


def collapse(value: Any) -> str:
    """비교용 값. 공백·줄바꿈 차이는 의미 없는 변경으로 본다 (REQ-QAINTEL-003)."""
    if isinstance(value, (list, tuple)):
        return "|".join(collapse(item) for item in value)
    return WHITESPACE_RE.sub(" ", str(value or "")).strip()
