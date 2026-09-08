from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings

# `${NAME}` 또는 `${NAME:-기본값}`. 기본값에는 Windows 경로(`C:/...`)가 들어가므로 `}` 만
# 종료로 본다.
_ENV_REF_RE = re.compile(r"\$\{([^}]+)\}")


class SpecificationSyncConfig(BaseModel):
    source: str = "manual"
    crawler_output_dir: str = ""
    filename_patterns: list[str] = Field(default_factory=list)


class TestCaseSyncConfig(BaseModel):
    source: str = "manual"


class SyncScheduleConfig(BaseModel):
    day_of_week: str = "*"
    schedule_time: str = "07:00"


class KnowledgeSourceConfig(BaseModel):
    """제품의 지식 자산(사양서·매뉴얼·TC·QA 규칙)이 최신본으로 갱신되는 폴더.

    QA가 직접 관리하는 폴더이므로 앱은 **읽기만** 한다. 분류는 파일명 규약으로 하며
    (`(사양서)`/`(매뉴얼)`/`(TC)`/`[QA 작성 규칙]`/`지침 프롬프트`), 제품별로 규약이 다르면
    `classify`로 덮어쓴다. 비워두면 `app/core/product_knowledge.DEFAULT_CLASSIFIERS`를 쓴다.
    """

    dir: str = ""
    classify: dict[str, list[str]] = Field(default_factory=dict)
    extensions: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)

    @field_validator("dir")
    @classmethod
    def _expand(cls, value: str) -> str:
        """`${ENV}` / `${ENV:-기본값}` 형태를 환경변수로 치환한다.

        같은 폴더를 담당자 PC 와 운영 서버가 서로 다른 경로로 본다 (PC 는 로컬 경로, 서버는
        마운트 지점). `${ENV:-기본값}` 을 쓰면 **YAML 에 PC 경로를 기본값으로 두고 서버에서만
        환경변수로 덮어쓸** 수 있다 — 양쪽 모두 환경변수를 설정하지 않아도 된다.

        기본값이 없는 `${ENV}` 인데 환경변수도 없으면 빈 문자열로 만든다. `${...}` 문자열이
        그대로 경로로 쓰여 "폴더 없음"이 아니라 이상한 폴더를 만드는 일을 막는다.
        """
        if not value:
            return value

        def substitute(match: re.Match[str]) -> str:
            name, _, fallback = match.group(1).partition(":-")
            return os.environ.get(name.strip(), fallback)

        expanded = _ENV_REF_RE.sub(substitute, value)
        expanded = os.path.expandvars(expanded)
        return "" if "${" in expanded or expanded.startswith("$") else expanded


class IssueSourceConfig(BaseModel):
    """Polarion Issue Export 산출물이 쌓이는 폴더.

    별도 프로젝트(`alm-issue-export`)가 Polarion REST API로 수집해 `<ISSUE-ID>/backup.json`과
    `report.html`을 만든다. 이 프로젝트는 그 결과물만 읽고 크롤러 코드·설정은 건드리지 않는다.
    """

    source: str = "manual"
    export_dir: str = ""
    # Issue ID 접두사. Polarion 프로젝트마다 다르다 (VXvue=VP, Bellalun=BV 등).
    id_prefix: str = ""


class ProductConfig(BaseModel):
    product: str
    version: str = ""
    manual_types: list[str] = Field(default_factory=list)
    specification: SpecificationSyncConfig = Field(default_factory=SpecificationSyncConfig)
    testcase: TestCaseSyncConfig = Field(default_factory=TestCaseSyncConfig)
    knowledge_source: KnowledgeSourceConfig = Field(default_factory=KnowledgeSourceConfig)
    issue_source: IssueSourceConfig = Field(default_factory=IssueSourceConfig)
    sync: SyncScheduleConfig = Field(default_factory=SyncScheduleConfig)


def _products_dir(root: Path | None = None) -> Path:
    return (root or get_settings().root) / "config" / "products"


def load_product_config(name: str, root: Path | None = None) -> ProductConfig | None:
    path = _products_dir(root) / f"{name.lower()}.yaml"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return ProductConfig.model_validate(raw)


def list_product_configs(root: Path | None = None) -> list[ProductConfig]:
    directory = _products_dir(root)
    if not directory.is_dir():
        return []
    configs = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        configs.append(ProductConfig.model_validate(raw))
    return configs
