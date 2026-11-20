"""Bellalun specification provenance, shared by the uploader and readers."""
import re

ONENOTE_ORIGINAL = '(사양서) Bellalun Viewer 사양서(261030)'
ONENOTE_ARCHIVE = '(사양서) Bellalun Viewer 기존 원노트 사양서(261030)'
PRODUCT_VERSIONS = ('1.0.12', '2.0.0')
RELEASE_VERSION = '1.0.12'
SPECIFICATION_VIEWS = ('onenote', *PRODUCT_VERSIONS)


def specification_view(name: str) -> str:
    if name in (ONENOTE_ORIGINAL + '.pdf', ONENOTE_ARCHIVE + '.pdf'):
        return 'onenote'
    match = re.fullmatch(
        r'\(사양서\) Bellalun Viewer ALM 사양서 (\d+\.\d+\.\d+)(?: \(목표 버전\))?\(\d{6}\)\.pdf',
        name)
    return match[1] if match else ''


def provenance(name: str) -> dict:
    view = specification_view(name)
    if view == 'onenote':
        return {'source': 'OneNote', 'scope': '기존 원노트 사양서', 'usage': '과거 자료'}
    if view == RELEASE_VERSION:
        return {'source': 'ALM', 'product_version': view, 'scope': '1.0.12 LiveDoc의 1.0.12 목표 SRS',
                'usage': '현재 배포 사양'}
    if view:
        return {'source': 'ALM', 'target_version': view, 'scope': f'목표 버전 {view}으로 지정된 SRS',
                'usage': '개발 진행 중', 'development': True}
    return {}


def excluded_from_current_specification(name: str) -> bool:
    view = specification_view(name)
    return bool(view and view != RELEASE_VERSION)
