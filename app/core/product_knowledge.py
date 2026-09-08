"""제품 지식 자산 레지스트리 — 제품별 "지식 폴더"를 프로젝트로 수집한다.

QA는 제품마다 사양서·매뉴얼·TC·QA 규칙을 한 폴더에 모아 최신본으로 갈아끼운다. 이 모듈은
그 폴더를 **읽기만** 하고, 프로젝트 안(`data/product_knowledge/<slug>/`)으로 사본과 정규화
텍스트를 만든 뒤 무엇이 최신인지 판단한 근거를 manifest에 남긴다. 원본 폴더는 절대 쓰지
않는다 (사용자가 직접 관리하는 영역).

제품별로 코드를 분기하지 않는다. 제품 간 공통 규약은 **파일명**이다.

    (사양서) VXvue 사양서1(260907).pdf
    (매뉴얼) VXvue Operation Manual.V1.0.11_KO.pdf
    (TC) RA16-148-002_VXvue_TestCase.xlsx
    [QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드_Rev1.12.md
    VXvue 업무 자동화 지침 프롬프트.txt

VXvue와 Bellalun Viewer가 이미 이 규약을 쓰고 있어 그대로 기준으로 삼았다. 새 제품은
`config/products/<slug>.yaml`에 `knowledge_source.dir`만 넣으면 코드 변경 없이 편입된다.

같은 논리 문서(파일명에서 리비전만 다른 것)가 여러 개 있으면 하나만 고른다. 사람이 미리
뽑아둔 `.txt` 추출본이 원본 PDF보다 오래된 경우가 실제로 있었기 때문에(`VXvue 사양서1(260824).txt`
vs `(사양서) VXvue 사양서1(260907).pdf`), 제외한 파일과 그 이유를 manifest에 남겨 QA가
확인할 수 있게 한다.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.product_config import ProductConfig

# 자산 종류. `documents` 테이블에 등록되는 것은 specification / manual / testcase 셋이고,
# qa_rules / instruction_prompt 는 app/core/qa_rules.py 가 읽는 규칙 자산이다.
KIND_SPECIFICATION = "specification"
KIND_MANUAL = "manual"
KIND_TESTCASE = "testcase"
KIND_QA_RULES = "qa_rules"
KIND_INSTRUCTION_PROMPT = "instruction_prompt"
KIND_UNKNOWN = "unknown"

REGISTERABLE_KINDS = (KIND_SPECIFICATION, KIND_MANUAL, KIND_TESTCASE)
RULE_KINDS = (KIND_QA_RULES, KIND_INSTRUCTION_PROMPT)

# 같은 논리 문서에 여러 형식이 있으면 이 순서로 하나만 고른다. 원본(PDF/DOCX/XLSX)이
# 사람이 뽑아둔 추출본(.txt)보다 항상 우선한다 — 추출본은 갱신을 잊기 쉽다.
FORMAT_PRIORITY = {".pdf": 40, ".docx": 40, ".xlsx": 40, ".xlsm": 40, ".md": 30, ".txt": 10}

# 텍스트를 추출할 수 있는 형식만 수집한다.
DEFAULT_EXTENSIONS = (".pdf", ".docx", ".xlsx", ".xlsm", ".md", ".txt")

# Office 임시 파일과 백업 파일은 건너뛴다.
DEFAULT_IGNORE = ("~$*", "*.tmp", ".~*")

# 파일명 접두사 → 자산 종류. 제품 무관 기본 규약이며 제품 YAML에서 덮어쓸 수 있다.
DEFAULT_CLASSIFIERS: dict[str, tuple[str, ...]] = {
    KIND_SPECIFICATION: ("(사양서)*", "*SRS 사양서*", "*사양서[0-9]*"),
    KIND_MANUAL: ("(매뉴얼)*", "System Integration Guide*", "*Conformance Statement*", "*Operation Manual*", "*Service Manual*", "*API Protocol Manual*"),
    KIND_TESTCASE: ("(TC)*", "*Test Case*", "*TestCase*", "*Checklist*"),
    KIND_QA_RULES: ("[[]QA 작성 규칙[]]*",),
    KIND_INSTRUCTION_PROMPT: ("*지침 프롬프트*",),
}

# 분류 우선순위. 예: "(TC) R-20-643_VXvue_변경사항 영향성평가_Checklist.xlsx"는
# `(TC)` 접두사가 있으므로 Checklist 패턴보다 먼저 testcase 로 확정돼야 한다.
CLASSIFY_ORDER = (KIND_QA_RULES, KIND_INSTRUCTION_PROMPT, KIND_SPECIFICATION, KIND_MANUAL, KIND_TESTCASE)

KIND_PREFIX_RE = re.compile(r"^\s*(?:\((?:사양서|매뉴얼|TC)\)|\[[^\]]+\])\s*")
# (260907) 형태의 날짜 리비전. 사양서가 쓰는 방식이다.
DATE_REVISION_RE = re.compile(r"\((\d{6})\)")
# V1.0.11 / V1.0.12W1 / v1.4.4 / V4.2 형태. 매뉴얼이 쓰는 방식이다.
VERSION_REVISION_RE = re.compile(r"[.\s_]([Vv]\d+(?:\.\d+)*(?:W\d+)?)(?=[._\s]|$)")
# Rev1.12 / Rev.1.12 형태. QA 규칙 문서가 쓰는 방식이다.
REV_REVISION_RE = re.compile(r"_?Rev\.?\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
# R-20-643 / RA16-148-002 / RA16-14B-010 / R-23-2346 형태의 사내 문서번호. 리비전이 아니다.
# 뒤 경계를 `\b`가 아니라 부정 룩어헤드로 잡는다 — `RA16-148-002_VXvue`처럼 `_`가 이어지면
# `\b`는 `002` 앞에서 끊겨 문서번호가 반만 잘린다(실제로 그렇게 잘렸다).
DOC_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z])(R[A-Z]?\d{0,2}(?:[-\s][0-9A-Z]{2,4}){1,3})(?![0-9A-Za-z])")
LANGUAGE_RE = re.compile(r"_(KO|EN|JP|CN)(?=[._\s]|$)", re.IGNORECASE)
# 사람이 붙인 진행 상태 꼬리표. 논리 문서 식별에서 제외한다. `_확인완료`처럼 밑줄로 붙는 것과
# `(개정)`처럼 괄호로 붙는 것 둘 다 실제로 쓰인다.
STATUS_NOTE_PATTERNS = (
    re.compile(r"_(수정_확인완료|확인완료|검토완료|개정본|개정|완료)(?=[._\s]|$)"),
    re.compile(r"\s*\((개정본|개정|수정|확인완료|완료)\)"),
)


@dataclass
class KnowledgeAsset:
    """지식 폴더에서 발견한 파일 하나. 원본은 수정하지 않는다."""

    kind: str
    file_name: str
    source_path: str
    base_name: str
    revision: str = ""
    revision_kind: str = ""
    doc_number: str = ""
    language: str = ""
    status_note: str = ""
    extension: str = ""
    size: int = 0
    sha256: str = ""
    modified: str = ""
    #: 파일명을 정규화·복구했을 때의 메모. 비어 있으면 원본 그대로 쓴 것이다.
    name_note: str = ""
    selected: bool = True
    exclude_reason: str = ""
    superseded_by: str = ""

    @property
    def logical_id(self) -> str:
        """같은 논리 문서인지 판단하는 키. 리비전·언어·상태 꼬리표를 뺀 이름이다."""
        parts = [self.kind, self.base_name.casefold()]
        if self.language:
            parts.append(self.language.upper())
        return "|".join(parts)


@dataclass
class ScanResult:
    product: str
    source_dir: str
    exists: bool
    assets: list[KnowledgeAsset] = field(default_factory=list)

    @property
    def selected(self) -> list[KnowledgeAsset]:
        return [asset for asset in self.assets if asset.selected]

    def by_kind(self, kind: str) -> list[KnowledgeAsset]:
        return [asset for asset in self.selected if asset.kind == kind]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for asset in self.selected:
            counts[asset.kind] = counts.get(asset.kind, 0) + 1
        return counts


HANGUL_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
# 한글 자모(NFD 로 분해된 형태). 이 문자가 보이면 이름이 분해 정규화 상태다.
HANGUL_JAMO_RE = re.compile(r"[ᄀ-ᇿ㄰-㆏ꥠ-꥿ힰ-퟿]")
# mojibake 로 흔히 나타나는 latin-1 확장 영역. 정상 한글 파일명에는 나오지 않는다.
MOJIBAKE_HINT_RE = re.compile(r"[ -ÿ]{2,}")

# 깨진 이름을 되살릴 (읽은 인코딩, 실제 인코딩) 조합. 왕복 결과에 한글이 생길 때만 채택한다.
MOJIBAKE_RECOVERIES: tuple[tuple[str, str], ...] = (("latin-1", "cp949"), ("latin-1", "utf-8"), ("cp1252", "cp949"), ("cp1252", "utf-8"))


def normalize_file_name(file_name: str) -> tuple[str, str]:
    """파일명을 분류·비교에 쓸 수 있는 형태로 만든다. 반환값은 (정규화된 이름, 변환 메모).

    운영 서버가 지식 폴더를 네트워크 마운트로 볼 때 한글 파일명이 두 가지로 어긋난다.
    둘 다 그대로 두면 `fnmatch` 패턴이 하나도 맞지 않아 **모든 파일이 unknown 으로 빠진다.**

    1. **NFD(분해) 정규화.** macOS·일부 SMB 구현이 `사양서` 를 자모로 분해해 넘긴다.
       NFC 로 합치면 해결되고 정보 손실이 없으므로 **조용히 변환한다.**
    2. **mojibake.** `iocharset` 이 잘못 설정되면 cp949/utf-8 바이트를 latin-1 로 읽은
       형태가 된다(`»ç¾ç¼­`). 되살릴 수는 있지만 마운트 설정이 잘못됐다는 신호이므로
       **복구하되 메모를 남긴다** — 근본 원인(`iocharset=utf8`)을 고쳐야 한다.

    복구는 **왕복 결과에 한글이 생길 때만** 채택한다. 원래 한글이 없는 파일명(영문 매뉴얼
    등)을 건드려 망가뜨리지 않기 위함이다.
    """
    normalized = unicodedata.normalize("NFC", file_name)
    note = "NFD(분해) 정규화를 NFC로 합쳤습니다" if HANGUL_JAMO_RE.search(file_name) else ""

    if HANGUL_RE.search(normalized) or not MOJIBAKE_HINT_RE.search(normalized):
        return normalized, note

    for read_as, actual in MOJIBAKE_RECOVERIES:
        try:
            recovered = unicodedata.normalize("NFC", normalized.encode(read_as).decode(actual))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if HANGUL_RE.search(recovered):
            return recovered, f"깨진 파일명을 복구했습니다 ({read_as}→{actual}). 마운트에 iocharset=utf8 이 필요합니다"
    return normalized, "파일명이 깨져 있고 복구하지 못했습니다. 마운트 옵션(iocharset=utf8)을 확인하세요"


def classify(file_name: str, classifiers: dict[str, list[str]] | None = None) -> str:
    """파일명으로 자산 종류를 정한다. 어느 패턴에도 걸리지 않으면 unknown.

    이름을 먼저 정규화한다 — 마운트에서 NFD 로 오거나 깨져 온 이름을 그대로 비교하면
    패턴이 하나도 맞지 않는다 (`normalize_file_name`).
    """
    file_name, _ = normalize_file_name(file_name)
    patterns: dict[str, tuple[str, ...]] = {kind: tuple(value) for kind, value in (classifiers or {}).items() if value}
    for kind in CLASSIFY_ORDER:
        for pattern in patterns.get(kind, DEFAULT_CLASSIFIERS.get(kind, ())):
            if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(file_name.casefold(), pattern.casefold()):
                return kind
    return KIND_UNKNOWN


def _revision_sort_key(revision: str, revision_kind: str) -> tuple:
    """리비전 비교용 키. 종류가 다르면 비교하지 않으므로 같은 종류 안에서만 쓴다."""
    if revision_kind == "date":
        return (int(revision),)
    numbers = [int(part) for part in re.findall(r"\d+", revision)]
    return tuple(numbers) if numbers else (0,)


def parse_asset_name(file_name: str) -> dict:
    """파일명을 논리 문서 이름 + 리비전 + 부가 정보로 분해한다.

    제품명을 알 필요가 없다 — 접두사·리비전·문서번호·언어·상태 꼬리표만 떼어낸다.
    이름은 먼저 정규화한다 (`normalize_file_name`).
    """
    file_name, _ = normalize_file_name(file_name)
    stem = Path(file_name).stem
    extension = Path(file_name).suffix.lower()

    base = KIND_PREFIX_RE.sub("", stem).strip()

    revision, revision_kind = "", ""
    match = REV_REVISION_RE.search(base)
    if match:
        revision, revision_kind = f"Rev{match.group(1)}", "rev"
        base = base[: match.start()] + base[match.end() :]
    if not revision:
        match = DATE_REVISION_RE.search(base)
        if match:
            revision, revision_kind = match.group(1), "date"
            base = base[: match.start()] + base[match.end() :]
    if not revision:
        match = VERSION_REVISION_RE.search(base)
        if match:
            # 같은 제품 안에서도 V1.0.11 / v1.4.4 로 대소문자가 섞여 있어 표기를 통일한다.
            revision, revision_kind = "V" + match.group(1)[1:], "version"
            base = base[: match.start()] + base[match.end() :]

    doc_number = ""
    match = DOC_NUMBER_RE.search(base)
    if match:
        doc_number = match.group(1)
        base = base[: match.start()] + base[match.end() :]

    language = ""
    match = LANGUAGE_RE.search(base)
    if match:
        language = match.group(1).upper()
        base = base[: match.start()] + base[match.end() :]

    status_notes = []
    changed = True
    while changed:
        changed = False
        for pattern in STATUS_NOTE_PATTERNS:
            match = pattern.search(base)
            if match:
                status_notes.append(match.group(1))
                base = base[: match.start()] + base[match.end() :]
                changed = True

    base = re.sub(r"[\s_.\-]+", " ", base).strip(" _-.")
    return {
        "base_name": base,
        "revision": revision,
        "revision_kind": revision_kind,
        "doc_number": doc_number,
        "language": language,
        "status_note": "_".join(status_notes),
        "extension": extension,
    }


def _sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _should_ignore(file_name: str, ignore: tuple[str, ...]) -> bool:
    """제외 패턴 비교. 이름을 정규화한다 — `[자동화*` 처럼 한글이 든 패턴이 NFD 이름에
    맞지 않으면 제외돼야 할 파일이 수집된다."""
    normalized, _ = normalize_file_name(file_name)
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in ignore)


def _resolve_duplicates(assets: list[KnowledgeAsset]) -> None:
    """같은 논리 문서가 여러 개면 하나만 selected 로 남기고 나머지에 이유를 붙인다.

    우선순위: ① 리비전이 더 최신 ② 원본 형식(PDF/DOCX/XLSX) ③ 파일 수정시각이 더 최근.
    리비전 표기 방식이 서로 다르면(날짜 vs 버전) 비교하지 않고 형식·시각으로만 판단한다.
    """
    groups: dict[str, list[KnowledgeAsset]] = {}
    for asset in assets:
        if asset.kind == KIND_UNKNOWN:
            continue
        groups.setdefault(asset.logical_id, []).append(asset)

    for members in groups.values():
        if len(members) < 2:
            continue
        revision_kinds = {member.revision_kind for member in members if member.revision_kind}
        comparable = len(revision_kinds) == 1

        def rank(asset: KnowledgeAsset) -> tuple:
            revision_rank = _revision_sort_key(asset.revision, asset.revision_kind) if comparable and asset.revision else (0,)
            return (revision_rank, FORMAT_PRIORITY.get(asset.extension, 0), asset.modified)

        winner = max(members, key=rank)
        for member in members:
            if member is winner:
                continue
            member.selected = False
            member.superseded_by = winner.file_name
            if comparable and member.revision and winner.revision and member.revision != winner.revision:
                member.exclude_reason = f"같은 문서의 이전 리비전({member.revision} < {winner.revision})"
            elif FORMAT_PRIORITY.get(member.extension, 0) < FORMAT_PRIORITY.get(winner.extension, 0):
                member.exclude_reason = f"원본 {winner.extension}이 있는 추출본({member.extension})"
            else:
                member.exclude_reason = "같은 논리 문서의 중복 파일 (수정시각이 더 오래됨)"


def scan_source(config: ProductConfig) -> ScanResult:
    """제품 지식 폴더를 읽어 자산 목록을 만든다. 파일을 쓰지 않는다."""
    source = config.knowledge_source
    source_dir = Path(source.dir) if source.dir else None
    result = ScanResult(product=config.product, source_dir=str(source_dir or ""), exists=bool(source_dir and source_dir.is_dir()))
    if not result.exists or source_dir is None:
        return result

    extensions = tuple(ext.lower() for ext in (source.extensions or DEFAULT_EXTENSIONS))
    ignore = tuple(source.ignore or ()) + DEFAULT_IGNORE
    classifiers = source.classify or {}

    for path in sorted(source_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in extensions or _should_ignore(path.name, ignore):
            continue

        display_name, name_note = normalize_file_name(path.name)
        parsed = parse_asset_name(display_name)
        kind = classify(display_name, classifiers)
        stat = path.stat()
        asset = KnowledgeAsset(
            kind=kind,
            # 정규화된 이름을 자산 이름으로 쓴다. 원본 경로는 그대로 두므로 파일은 정상 복사된다.
            file_name=display_name,
            source_path=str(path),
            name_note=name_note,
            size=stat.st_size,
            sha256=_sha256(path),
            modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            **parsed,
        )
        if kind == KIND_UNKNOWN:
            asset.selected = False
            asset.exclude_reason = "파일명이 알려진 지식 자산 규약에 맞지 않습니다"
            if name_note:
                asset.exclude_reason += f" ({name_note})"
        result.assets.append(asset)

    _resolve_duplicates(result.assets)
    return result


def product_slug(product: str) -> str:
    """제품 표시명 → 폴더 이름. 제품명에 공백·대문자가 있어도 안전한 경로를 만든다."""
    return re.sub(r"[^a-z0-9]+", "-", product.casefold()).strip("-") or "product"


def product_dir(product: str, root: Path | None = None) -> Path:
    return (root or get_settings().root) / "data" / "product_knowledge" / product_slug(product)


def manifest_path(product: str, root: Path | None = None) -> Path:
    return product_dir(product, root) / "manifest.json"


def load_manifest(product: str, root: Path | None = None) -> dict:
    path = manifest_path(product, root)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _normalized_suffix(extension: str) -> str:
    return ".md" if extension in (".md", ".txt") else ".txt"


def _extract_text(path: Path) -> str:
    """자산 하나를 정규화 텍스트로 만든다. 형식별 파서는 app/parsers 에 이미 있는 것을 쓴다."""
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".xlsx", ".xlsm"):
        from app.parsers.excel_parser import preview_workbook

        sheets = preview_workbook(path, max_rows=40)
        lines = []
        for sheet_name, rows in sheets.items():
            lines.append(f"# {sheet_name}")
            lines.extend("\t".join(cell for cell in row) for row in rows)
        return "\n".join(lines)
    from app.parsers.document_parser import extract_document_text

    return extract_document_text(path)


@dataclass
class SyncOutcome:
    product: str
    status: str
    detail: str
    copied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)


def sync_product(config: ProductConfig, root: Path | None = None, dry_run: bool = False, normalize: bool = True) -> SyncOutcome:
    """지식 폴더의 최신 자산을 `data/product_knowledge/<slug>/`로 수집한다.

    이미 같은 sha256으로 수집돼 있으면 복사도 텍스트 추출도 다시 하지 않는다 (매주 실행해도
    변경된 파일만 처리된다). 실패한 파일이 있어도 나머지는 계속 수집하고, 무엇이 실패했는지
    manifest 와 반환값에 남긴다.
    """
    scan = scan_source(config)
    outcome = SyncOutcome(product=config.product, status="SUCCESS", detail="")
    outcome.excluded = [f"{asset.file_name} — {asset.exclude_reason}" for asset in scan.assets if not asset.selected]

    if not scan.exists:
        outcome.status = "NEEDS_CONFIG"
        outcome.detail = f"지식 폴더를 찾을 수 없습니다: {scan.source_dir or '(미설정)'}"
        return outcome

    previous = {entry["file_name"]: entry for entry in load_manifest(config.product, root).get("assets", [])}
    target = product_dir(config.product, root)

    entries: list[dict] = []
    for asset in scan.selected:
        record = asset_record(asset)
        known = previous.get(asset.file_name)
        original_target = target / "original" / asset.kind / asset.file_name
        if known and known.get("sha256") == asset.sha256 and original_target.is_file():
            record["normalized_path"] = known.get("normalized_path", "")
            record["normalized_chars"] = known.get("normalized_chars", 0)
            record["collected_at"] = known.get("collected_at", "")
            outcome.unchanged.append(asset.file_name)
            entries.append(record)
            continue

        if dry_run:
            outcome.copied.append(asset.file_name)
            entries.append(record)
            continue

        try:
            original_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset.source_path, original_target)
            if normalize:
                text = _extract_text(original_target)
                normalized_target = target / "normalized" / asset.kind / f"{Path(asset.file_name).stem}{_normalized_suffix(asset.extension)}"
                normalized_target.parent.mkdir(parents=True, exist_ok=True)
                normalized_target.write_text(text, encoding="utf-8")
                record["normalized_path"] = str(normalized_target.relative_to(target)).replace("\\", "/")
                record["normalized_chars"] = len(text)
            record["collected_at"] = datetime.now(timezone.utc).isoformat()
            outcome.copied.append(asset.file_name)
            entries.append(record)
        except Exception as exc:  # 파일 하나가 실패해도 전체 수집을 멈추지 않는다
            record["error"] = f"{type(exc).__name__}: {exc}"
            outcome.failed.append(asset.file_name)
            entries.append(record)

    if not dry_run:
        manifest = {
            "product": config.product,
            "slug": product_slug(config.product),
            "source_dir": scan.source_dir,
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "counts": scan.counts(),
            "assets": entries,
            "excluded": [asset_record(asset) | {"exclude_reason": asset.exclude_reason, "superseded_by": asset.superseded_by} for asset in scan.assets if not asset.selected],
        }
        path = manifest_path(config.product, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if outcome.failed:
        outcome.status = "PARTIAL" if outcome.copied else "FAILED"
    elif dry_run:
        outcome.status = "DRY_RUN"
    outcome.detail = (
        f"수집 {len(outcome.copied)}건, 미변경 {len(outcome.unchanged)}건, "
        f"실패 {len(outcome.failed)}건, 제외 {len(outcome.excluded)}건"
    )
    if outcome.failed:
        outcome.detail += f" / 실패 파일: {', '.join(outcome.failed)}"
    return outcome


def asset_record(asset: KnowledgeAsset) -> dict:
    record = asdict(asset)
    record.pop("selected", None)
    record.pop("exclude_reason", None)
    record.pop("superseded_by", None)
    return record


def collected_assets(product: str, kind: str | None = None, root: Path | None = None) -> list[dict]:
    """수집된 자산 목록을 manifest 에서 읽는다. 원본 폴더에 접근하지 않는다 (서버에서도 동작)."""
    manifest = load_manifest(product, root)
    assets = manifest.get("assets", [])
    if kind:
        assets = [asset for asset in assets if asset.get("kind") == kind]
    return [asset for asset in assets if not asset.get("error")]


def collected_path(product: str, asset: dict, root: Path | None = None) -> Path:
    return product_dir(product, root) / "original" / asset["kind"] / asset["file_name"]


def source_available(config: ProductConfig) -> bool:
    """이 호스트에서 원본 지식 폴더에 접근할 수 있는지. 서버에서는 보통 False."""
    return bool(config.knowledge_source.dir) and Path(config.knowledge_source.dir).is_dir()


def resolve_config(product: str) -> ProductConfig | None:
    """제품 표시명이나 slug 로 제품 설정을 찾는다 ("Bellalun Viewer" → bellalun-viewer.yaml)."""
    from app.core.product_config import list_product_configs, load_product_config

    direct = load_product_config(product)
    if direct is not None:
        return direct
    direct = load_product_config(product_slug(product))
    if direct is not None:
        return direct
    for config in list_product_configs():
        if config.product.casefold() == product.casefold():
            return config
    return None


# --- 수집 결과를 분석 대상 문서로 등록 ----------------------------------------


@dataclass
class RegisterOutcome:
    product: str
    registered: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    #: 다른 경로로 이미 등록돼 있어 이 수집본과 겹칠 수 있는 문서. 지우지 않고 보고만 한다.
    duplicates: list[str] = field(default_factory=list)
    detail: str = ""


def _is_strictly_older(existing_name: str, asset: dict) -> bool:
    """등록돼 있는 문서가 이 자산의 **같은 논리 문서이면서 확실히 더 오래된** 리비전인가.

    리비전은 **파일명에서만** 유도한다. `documents.revision` 컬럼은 등록 순번(`Rev.1`,
    `Rev.2`)이 들어 있어 문서 리비전이 아니다 — 그 값으로 비교하면 260831 이 260907 보다
    최신으로 판정된다.

    리비전 표기 방식이 다르거나(날짜 vs 버전) 비교가 불가능하면 False 를 준다 — 확실할
    때만 지운다.
    """
    new_revision, new_kind = asset.get("revision", ""), asset.get("revision_kind", "")
    if not new_revision:
        return False
    existing_parsed = parse_asset_name(existing_name)
    if not existing_parsed["revision"] or existing_parsed["revision_kind"] != new_kind:
        return False
    try:
        return _revision_sort_key(existing_parsed["revision"], existing_parsed["revision_kind"]) < _revision_sort_key(new_revision, new_kind)
    except ValueError:
        return False


def _is_same_file(document_path: str, asset: dict) -> bool:
    """등록된 파일이 이 수집본과 **바이트까지 같은가**. 같으면 같은 파일이 두 번 등록된 것이다."""
    digest = asset.get("sha256")
    if not digest:
        return False
    path = Path(document_path)
    if not path.is_file():
        return False
    try:
        return _sha256(path) == digest
    except OSError:
        return False


def register_collected(product: str, storage=None, root: Path | None = None, kinds: tuple[str, ...] = REGISTERABLE_KINDS) -> RegisterOutcome:
    """수집된 자산을 `documents` 테이블에 등록해 분석 대상으로 만든다.

    수집(파일 복사)만으로는 분석에 쓰이지 않는다 — `Storage.active_documents` 가 보는 것은
    `documents` 테이블이다. 이 함수가 그 사이를 잇는다.

    **같은 논리 문서의 확실히 더 오래된 리비전만 지운다.** "어느 것이 최신인가"에 답하는 것이
    이 시스템의 존재 이유이므로, 사양서1(260831)이 남아 있는데 사양서1(260907)을 등록하면
    옛 사양이 계속 검색된다. 반면 리비전 비교가 불가능하거나 종류(kind)가 다르게 등록된
    문서는 지우지 않고 `duplicates` 로 보고한다 — 사람이 다른 기준으로 올려둔 것일 수 있다.
    """
    from app.core import document_cache
    from app.core.storage import Storage

    storage = storage or Storage()
    outcome = RegisterOutcome(product=product)
    config = resolve_config(product)
    version = config.version if config else ""
    base = product_dir(product, root)

    storage.ensure_product(product)
    if version:
        storage.ensure_version(product, version)

    # 종류가 어긋나게 등록된 문서까지 보려면 전 종류를 함께 본다 (매뉴얼이 specification 으로
    # 등록돼 있는 실제 사례가 있었다).
    existing_all: list[dict] = []
    for kind in REGISTERABLE_KINDS:
        for document in storage.active_documents(kind, product):
            existing_all.append({**document, "kind": kind})

    removed_ids: set[int] = set()
    registered_paths = {str((base / "original" / kind / asset["file_name"]).resolve()).casefold() for kind in kinds for asset in collected_assets(product, kind=kind, root=root)}

    for kind in kinds:
        for asset in collected_assets(product, kind=kind, root=root):
            path = base / "original" / kind / asset["file_name"]
            if not path.is_file():
                continue
            resolved = str(path.resolve()).casefold()
            already = next((document for document in existing_all if str(Path(document["path"]).resolve()).casefold() == resolved), None)
            if already is not None:
                outcome.unchanged.append(asset["file_name"])
            else:
                storage.add_document(
                    kind=kind,
                    product=product,
                    version=version,
                    revision=asset.get("revision", ""),
                    name=asset["file_name"],
                    path=path,
                    metadata={
                        "source": "product_knowledge",
                        "base_name": asset.get("base_name", ""),
                        "revision_kind": asset.get("revision_kind", ""),
                        "language": asset.get("language", ""),
                        "doc_number": asset.get("doc_number", ""),
                        "sha256": asset.get("sha256", ""),
                    },
                )
                outcome.registered.append(asset["file_name"])

            asset_base = (asset.get("base_name") or "").casefold()
            for document in existing_all:
                if document["id"] in removed_ids or str(Path(document["path"]).resolve()).casefold() in registered_paths:
                    continue
                metadata = json.loads(document.get("metadata_json") or "{}")
                document_base = (metadata.get("base_name") or parse_asset_name(document["name"])["base_name"]).casefold()
                if document_base != asset_base:
                    continue
                older = document["kind"] == kind and _is_strictly_older(document["name"], asset)
                identical = document["kind"] == kind and _is_same_file(document["path"], asset)
                # 파일이 사라진 등록은 검색에 아무것도 기여하지 못하면서 "사양 없음" 오판만
                # 만든다. 같은 논리 문서를 수집본이 대체하므로 잃는 것이 없다.
                dead = not Path(document["path"]).is_file()
                if older or identical or dead:
                    storage.delete_document(document["id"])
                    document_cache.delete(document["id"])
                    removed_ids.add(document["id"])
                    reason = "이전 리비전" if older else ("동일 파일 중복 등록" if identical else "원본 파일 없음")
                    outcome.replaced.append(f"{document['name']} (kind={document['kind']}, {reason})")
                else:
                    note = f"{document['name']} (등록 kind={document['kind']}"
                    if document["kind"] != kind:
                        note += f", 수집 분류={kind}"
                    outcome.duplicates.append(note + ")")

    outcome.duplicates = list(dict.fromkeys(outcome.duplicates))
    outcome.detail = (
        f"등록 {len(outcome.registered)}건, 미변경 {len(outcome.unchanged)}건, "
        f"이전 리비전 정리 {len(outcome.replaced)}건, 중복 확인 필요 {len(outcome.duplicates)}건"
    )
    return outcome


def sync_and_register(product: str, storage=None, root: Path | None = None) -> dict:
    """지식 폴더 수집 → 문서 등록을 한 번에. `/knowledge` 화면 버튼과 스케줄러가 함께 쓴다."""
    config = resolve_config(product)
    if config is None:
        return {"status": "NEEDS_CONFIG", "detail": f"config/products/ 에 '{product}' 설정이 없습니다."}
    collected = sync_product(config, root=root)
    if collected.status in ("NEEDS_CONFIG", "FAILED"):
        return {"status": collected.status, "detail": collected.detail, "collected": collected.detail}
    registered = register_collected(product, storage=storage, root=root)
    status = "PARTIAL" if collected.status == "PARTIAL" else "SUCCESS"
    return {
        "status": status,
        "detail": f"{collected.detail} / {registered.detail}",
        "collected": collected.detail,
        "registered": registered.detail,
        "duplicates": registered.duplicates,
        "excluded": collected.excluded,
    }
