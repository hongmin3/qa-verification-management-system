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


# 자료 종류별 출처(REQ-KNOW-018). 공통 Knowledge 코드는 제품 이름이 아니라 이 값으로
# 등록 허용·화면 버튼·상태 판정을 정한다.
SOURCE_MANUAL = "manual"
SOURCE_ALM = "alm_crawler"
SOURCE_FOLDER = "knowledge_folder"
SOURCE_EXTERNAL = "external_sync"
SOURCE_UNKNOWN = "unknown"
KNOWN_SOURCES = (SOURCE_MANUAL, SOURCE_ALM, SOURCE_FOLDER, SOURCE_EXTERNAL)
AUTOMATIC_SOURCES = (SOURCE_ALM, SOURCE_EXTERNAL)
_SOURCE_ALIASES = {"alm": SOURCE_ALM}

# 자료 종류. 값은 `app/core/product_knowledge.KIND_*` 와 같다 (이 모듈이 그 모듈을 import 하면
# 순환이 생겨 문자열로 둔다). 순서가 화면 순서다.
ASSET_KINDS = ("specification", "testcase", "manual", "qa_rules", "instruction_prompt")
# 사람이 화면에서 올릴 수 있는 종류. 규칙 자산은 규칙 로더가 수집 기록만 읽으므로 뺀다.
UPLOADABLE_KINDS = ("specification", "testcase", "manual")
DEFAULT_REQUIRED = {"specification": True, "testcase": True, "manual": False, "qa_rules": True, "instruction_prompt": False}


def normalize_source(value: str) -> str:
    """출처 값을 비교할 수 있는 형태로. 알 수 없는 값은 `unknown` 이 된다."""
    text = (value or "").strip().lower()
    text = _SOURCE_ALIASES.get(text, text)
    return text if text in KNOWN_SOURCES else SOURCE_UNKNOWN


class KnowledgeAssetSource(BaseModel):
    """한 자료 종류의 출처와 꼭 있어야 하는지. `required` 를 비우면 종류별 기본값을 쓴다."""

    source: str = SOURCE_MANUAL
    required: bool | None = None


def _folder_source() -> KnowledgeAssetSource:
    return KnowledgeAssetSource(source=SOURCE_FOLDER)


class SpecificationSyncConfig(KnowledgeAssetSource):
    crawler_output_dir: str = ""
    filename_patterns: list[str] = Field(default_factory=list)


class TestCaseSyncConfig(KnowledgeAssetSource):
    pass


class SyncScheduleConfig(BaseModel):
    day_of_week: str = "*"
    schedule_time: str = "07:00"
    # 마지막 성공 수집이 이 날수보다 오래되면 상태 판정이 "업데이트 필요" 로 본다 (REQ-KNOW-019).
    stale_after_days: int = 14


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

    별도 프로젝트 ALM-QA-Automation 의 issue-export 앱(통합 전 `alm-issue-export`)이 Polarion REST API로
    수집해 `<실행폴더>/<ISSUE-ID>/backup.json`과 `report.html`을 만든다 (예전 구조는 `<ISSUE-ID>/` 바로 아래). 이 프로젝트는 그 결과물만 읽고 크롤러 코드·설정은 건드리지 않는다.
    """

    source: str = "manual"
    export_dir: str = ""
    # Issue ID 접두사. Polarion 프로젝트마다 다르다 (VXvue=VP, Bellalun=BV 등).
    id_prefix: str = ""


FieldNames = str | list[str]


class AlmQueries(BaseModel):
    srs: str = ""
    issue: str = ""


class AlmFields(BaseModel):
    """공통 모델 필드 → Polarion 원본 필드 이름 (SPEC REQ-QAINTEL-002).

    적지 않은 공통 필드는 Polarion 표준 필드 이름을 쓴다(`product_adapter.STANDARD_*`). 제품 고유
    필드(Legacy 번호, 연구소 결과, 재현 절차 등)는 여기 적은 것만 읽는다. 이름을 목록으로 적으면
    앞에서부터 값이 있는 첫 필드를 쓴다.
    """

    srs: dict[str, FieldNames] = Field(default_factory=dict)
    issue: dict[str, FieldNames] = Field(default_factory=dict)


class AlmConfig(BaseModel):
    """제품이 쓰는 ALM(Polarion) 프로젝트와 필드 대응 (SPEC REQ-QAINTEL-002)."""

    project_id: str = ""
    queries: AlmQueries = Field(default_factory=AlmQueries)
    fields: AlmFields = Field(default_factory=AlmFields)
    #: 공통 관계 이름 → Polarion relationships 이름 (occurred_versions, target_versions, linked_items, comments)
    relations: dict[str, str] = Field(default_factory=dict)
    #: 연구소 결과 원본 값 → 공통 값 (FIXED, SPEC, NOT_BUG, DUPLICATE, PENDING, NO_ACTION)
    rd_result_mapping: dict[str, str] = Field(default_factory=dict)


class QaRulesProfile(BaseModel):
    #: Skill 이 기준으로 삼는 QA 규칙 판. 비우면 판을 검사하지 않는다.
    supported_rev: str = ""
    #: 제품 규칙 Skill 이름. `config/products/<slug>/skills/<이름>/` 에 둔다.
    product_skill: str = ""


class ChecklistProfile(BaseModel):
    """제품의 영향성평가 Checklist 형식 (SPEC REQ-QAINTEL-018)."""

    #: 지식 사본의 TC 가운데 이 글자가 파일 이름에 있으면 Checklist 본보기로 쓴다.
    template_name_contains: str = ""
    category: str = ""
    #: 초안 시트의 열 이름. 순서대로 쓴다. 비우면 checklist_xlsx.DEFAULT_HEADERS.
    headers: list[str] = Field(default_factory=list)
    #: 초안 필드 → 열 이름. 비우면 checklist_xlsx.DEFAULT_COLUMNS.
    columns: dict[str, str] = Field(default_factory=dict)


class QaIntelligenceConfig(BaseModel):
    enabled: bool = True
    rules: QaRulesProfile = Field(default_factory=QaRulesProfile)
    #: 수정 완료 이슈 분석의 Regression 축 (app/modules/qa_agent/schemas.py 의 축 코드). 비우면 공통 7축.
    regression_axes: list[str] = Field(default_factory=list)
    checklist: ChecklistProfile = Field(default_factory=ChecklistProfile)
    #: 의미 없는 댓글(진행 상태 문장) 정규식. 비우면 change_events.DEFAULT_COMMENT_NOISE.
    comment_noise_patterns: list[str] = Field(default_factory=list)


class ProductConfig(BaseModel):
    product: str
    version: str = ""
    manual_types: list[str] = Field(default_factory=list)
    specification: SpecificationSyncConfig = Field(default_factory=SpecificationSyncConfig)
    testcase: TestCaseSyncConfig = Field(default_factory=TestCaseSyncConfig)
    manual: KnowledgeAssetSource = Field(default_factory=_folder_source)
    qa_rules: KnowledgeAssetSource = Field(default_factory=_folder_source)
    instruction_prompt: KnowledgeAssetSource = Field(default_factory=_folder_source)
    knowledge_source: KnowledgeSourceConfig = Field(default_factory=KnowledgeSourceConfig)
    issue_source: IssueSourceConfig = Field(default_factory=IssueSourceConfig)
    sync: SyncScheduleConfig = Field(default_factory=SyncScheduleConfig)
    alm: AlmConfig = Field(default_factory=AlmConfig)
    qa_intelligence: QaIntelligenceConfig = Field(default_factory=QaIntelligenceConfig)
    #: 설정 파일 이름(확장자 제외). 화면 주소 `/knowledge/products/<slug>` 에 쓴다. 읽을 때 채운다.
    slug: str = ""

    def asset_source(self, kind: str) -> KnowledgeAssetSource:
        """자료 종류의 출처 프로필. 모르는 종류는 KeyError 다 — 조용히 기본값을 주면 오타가 숨는다."""
        if kind not in ASSET_KINDS:
            raise KeyError(kind)
        return getattr(self, kind)

    def source_of(self, kind: str) -> str:
        return normalize_source(self.asset_source(kind).source)

    def is_required(self, kind: str) -> bool:
        required = self.asset_source(kind).required
        return DEFAULT_REQUIRED[kind] if required is None else required


def can_manual_upload(config: ProductConfig, kind: str) -> bool:
    """사람이 화면·업로드 주소로 이 종류를 등록할 수 있는가 (REQ-KNOW-018).

    화면 버튼과 등록 주소가 이 함수 하나로 판단한다 — 버튼만 숨기고 주소는 열어 두는 일을 막는다.
    """
    return kind in UPLOADABLE_KINDS and config.source_of(kind) == SOURCE_MANUAL


def registration_refusal(config: ProductConfig, kind: str, declared_source: str = "") -> str:
    """이 등록 요청을 받을 수 없는 이유. 받을 수 있으면 빈 문자열.

    `declared_source` 는 부르는 쪽이 스스로 밝히는 출처다(사양서 동기화는 `alm_crawler`).
    사람이 자동 자료를 실수로 덮어쓰지 않게 막는 장치이며 인증이 아니다 (NFR-SEC-004).
    """
    if kind not in UPLOADABLE_KINDS:
        return f"{kind} 자료는 지식 폴더로만 관리합니다."
    configured = config.source_of(kind)
    declared = normalize_source(declared_source) if declared_source.strip() else SOURCE_MANUAL
    if configured == SOURCE_MANUAL and declared == SOURCE_MANUAL:
        return ""
    if configured in AUTOMATIC_SOURCES and declared == configured:
        return ""
    return f"{config.product} {KIND_LABELS.get(kind, kind)}는 {SOURCE_LABELS.get(configured, '알 수 없는 출처')}(으)로 관리하는 자료라 여기서 등록할 수 없습니다."


KIND_LABELS = {
    "specification": "사양서",
    "testcase": "Test Case / Checklist",
    "manual": "Manual / Protocol",
    "qa_rules": "QA 규칙",
    "instruction_prompt": "지침 프롬프트",
}
SOURCE_LABELS = {
    SOURCE_ALM: "ALM 자동",
    SOURCE_EXTERNAL: "외부 자동화",
    SOURCE_FOLDER: "지식 폴더",
    SOURCE_MANUAL: "사람이 등록",
    SOURCE_UNKNOWN: "알 수 없는 출처",
}


def _products_dir(root: Path | None = None) -> Path:
    return (root or get_settings().root) / "config" / "products"


def load_product_config(name: str, root: Path | None = None) -> ProductConfig | None:
    path = _products_dir(root) / f"{name.lower()}.yaml"
    if not path.is_file():
        return None
    return _read(path)


def _read(path: Path) -> ProductConfig:
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return ProductConfig.model_validate(raw).model_copy(update={"slug": path.stem})


def list_product_configs(root: Path | None = None) -> list[ProductConfig]:
    directory = _products_dir(root)
    if not directory.is_dir():
        return []
    configs = []
    for path in sorted(directory.glob("*.yaml")):
        configs.append(_read(path))
    return configs
