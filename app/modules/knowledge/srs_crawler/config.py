"""현재 프로젝트의 설정과 비밀 설정으로 SRS 수집을 구성한다."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml
from app.core.config import get_settings
from app.core.product_config import load_product_config

@dataclass
class ProjectSpec:
    id: str
    query: str
    fetch_comments: bool = True


@dataclass
class VXvueGroup:
    file_key: str
    display_name: str
    modules: list[str]


@dataclass
class SingleFileProject:
    project_id: str
    file_key: str
    display_name: str


@dataclass
class Config:
    host: str
    token: str
    verify_ssl: bool
    timeout_seconds: int
    request_interval_seconds: float
    page_size: int
    projects: list[ProjectSpec]
    content_field_priority: list[str]
    vxvue_project_id: str
    vxvue_groups: list[VXvueGroup]
    single_file_projects: list[SingleFileProject]
    base_dir: Path
    archive_dir: Path
    snapshots_dir: Path
    logs_dir: Path
    knowledge_folder: Path | None
    filename_prefix: str
    filename_date_format: str
    min_expected_srs_ratio: float
    require_all_pdfs: bool
    org_folder: Path | None = None
    known_problem_srs: list[str] = field(default_factory=list)
    pdf_timeout_seconds: int = 300
    raw: dict[str, Any] = field(default_factory=dict)


class ConfigError(RuntimeError):
    pass


def load_config(root: Path | None = None) -> Config:
    settings = get_settings()
    root = root or settings.root
    raw = yaml.safe_load((root / 'config/srs-crawler.yaml').read_text(encoding='utf-8')) or {}
    host, token = settings.secrets.polarion_host, settings.secrets.polarion_token
    if not host or not token:
        raise ConfigError('POLARION_HOST / POLARION_TOKEN 설정이 필요합니다.')
    projects = [ProjectSpec(id=p['id'], query=p.get('query', 'type:srs'),
                            fetch_comments=p.get('fetch_comments', True)) for p in raw.get('projects', [])]
    if not projects:
        raise ConfigError('수집할 projects가 없습니다.')
    partition = raw.get('partition', {})
    product = load_product_config(raw.get('product_slug', 'vxvue'))
    folder = product.knowledge_source.dir if product else ''
    if not folder:
        raise ConfigError('제품의 knowledge_source.dir 설정이 필요합니다.')
    validation = raw.get('validation', {})
    render = raw.get('render', {})
    return Config(
        host=host.rstrip('/'), token=token,
        verify_ssl=bool(settings.get('daily_qa.polarion.verify_ssl', True)),
        timeout_seconds=int(settings.get('daily_qa.polarion.timeout_seconds', 90)),
        request_interval_seconds=float(settings.get('daily_qa.polarion.request_interval_seconds', .15)),
        page_size=int(settings.get('daily_qa.polarion.page_size', 100)),
        projects=projects, content_field_priority=raw.get('content', {}).get('field_priority', ['descriptionKR', 'description']),
        vxvue_project_id=partition.get('vxvue_project_id', ''),
        vxvue_groups=[VXvueGroup(**g) for g in partition.get('vxvue_groups', [])],
        single_file_projects=[SingleFileProject(**g) for g in partition.get('single_file_projects', [])],
        base_dir=root / 'output/srs_crawler', archive_dir=root / 'data/srs_crawler/archive',
        snapshots_dir=root / 'data/srs_crawler/snapshots', logs_dir=root / 'output/logs/srs_crawler',
        knowledge_folder=Path(folder) if Path(folder).is_absolute() else root / folder,
        filename_prefix='(사양서) ', filename_date_format='%y%m%d',
        min_expected_srs_ratio=float(validation.get('min_expected_srs_ratio', .95)),
        require_all_pdfs=bool(validation.get('require_all_pdfs', True)),
        known_problem_srs=render.get('known_problem_srs', []),
        pdf_timeout_seconds=int(render.get('pdf_timeout_seconds', 300)), raw=raw,
    )


