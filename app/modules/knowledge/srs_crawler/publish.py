"""상속 권한으로 준비한 PDF를 배포하고 최종 파일을 검사한다."""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable

import fitz

logger = logging.getLogger('srs_automation')


@dataclass
class PublishResult:
    published: list[str] = field(default_factory=list)
    retained: list[str] = field(default_factory=list)
    org_folder: str | None = None
    skipped: bool = False


def inspect_pdf(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        with fitz.open(stream=raw, filetype='pdf') as doc:
            pages = doc.page_count
            if pages < 1 or doc.needs_pass:
                raise ValueError('PDF must have readable pages without a password')
            for page in doc:
                page.get_text()
    except Exception as exc:
        raise ValueError(f'PDF 읽기 실패: {path.name}') from exc
    return {'name': path.name, 'size': len(raw), 'pages': pages,
            'sha256': hashlib.sha256(raw).hexdigest()}


def archive_and_publish(*, knowledge_folder: Path | None, generated_pdfs: list[Path],
                        file_date: str, org_folder: Path | None = None,
                        finalize: Callable[[PublishResult], None] | None = None) -> PublishResult:
    if knowledge_folder is None or not generated_pdfs:
        raise ValueError('배포 폴더와 PDF가 필요합니다.')
    if Path(file_date).name != file_date or file_date in {'', '.', '..'}:
        raise ValueError('안전한 배포 날짜가 필요합니다.')
    names = [p.name.casefold() for p in generated_pdfs]
    if len(names) != len(set(names)):
        raise ValueError('PDF 이름이 중복되었습니다.')
    expected = {p.name: inspect_pdf(p) for p in generated_pdfs}
    knowledge_folder.mkdir(parents=True, exist_ok=True)
    # tempfile.mkdtemp(mode=0700)의 Windows ACL을 최종 파일로 옮기지 않는다.
    stage = knowledge_folder.parent / ('.srs-publish-' + uuid.uuid4().hex)
    stage.mkdir()
    incoming, backup = stage / 'incoming', stage / 'originals'
    incoming.mkdir()
    backup.mkdir()
    originals = sorted(set(knowledge_folder.glob('(사양서)*.pdf')) |
                       {knowledge_folder / p.name for p in generated_pdfs if (knowledge_folder / p.name).exists()})
    if any(p.is_symlink() for p in originals):
        shutil.rmtree(stage)
        raise ValueError('배포 폴더의 사양서 링크는 교체할 수 없습니다.')
    changed: list[Path] = []
    preserve = False
    try:
        for p in generated_pdfs:
            shutil.copy2(p, incoming / p.name)
            if inspect_pdf(incoming / p.name) != expected[p.name]:
                raise RuntimeError('준비한 PDF가 원본과 다릅니다.')
        for p in originals:
            shutil.copy2(p, backup / p.name)
        for p in generated_pdfs:
            dest = knowledge_folder / p.name
            (incoming / p.name).replace(dest)
            changed.append(dest)
            if inspect_pdf(dest) != expected[p.name]:
                raise RuntimeError(f'최종 PDF가 원본과 다릅니다: {p.name}')
        archive_root = org_folder or knowledge_folder.parent / 'ORG'
        archive = archive_root / (file_date + '-' + uuid.uuid4().hex[:12])
        archive.mkdir(parents=True)
        for p in originals:
            shutil.copy2(backup / p.name, archive / p.name)
        for p in originals:
            if p.name not in expected:
                p.unlink()
                changed.append(p)
        result = PublishResult(published=list(expected), retained=[p.name for p in originals], org_folder=str(archive))
        if finalize:
            finalize(result)
        return result
    except BaseException:
        try:
            for dest in reversed(changed):
                saved = backup / dest.name
                if saved.exists():
                    restore = stage / 'restore.pdf'
                    shutil.copy2(saved, restore)
                    restore.replace(dest)
                    if dest.read_bytes() != saved.read_bytes():
                        raise RuntimeError('복구한 파일이 이전 파일과 다릅니다.')
                else:
                    dest.unlink(missing_ok=True)
        except BaseException as exc:
            preserve = True
            raise RuntimeError(f'배포 복구 실패. 복구 사본을 보존했습니다: {stage}') from exc
        raise
    finally:
        if not preserve:
            try:
                shutil.rmtree(stage)
            except OSError:
                logger.warning('배포 준비 폴더를 정리하지 못해 보존합니다: %s', stage)
