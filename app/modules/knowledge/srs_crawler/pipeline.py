"""수집·PDF·최종 배포를 모두 확인한 뒤 완료본을 공개한다."""
from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .collector import CollectStats, collect_project
from .logging_setup import setup_logging
from .partition import assign_file_groups
from .polarion_client import PolarionClient
from .problem_state import ProblemState
from .publish import archive_and_publish, inspect_pdf
from .render_recovery import render_group_pdf_with_recovery
from .run_lock import RunLock
from .snapshot_store import load_snapshot, save_manifest, save_snapshot, find_previous_snapshot_date
from .util import build_pdf_filename
from .validate import validate_run


def _json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def run(config, snapshot_date: str | None = None, force: bool = False, publish: bool = True) -> dict:
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    run_date = snapshot_date or today.isoformat()
    stamp = datetime.fromisoformat(run_date).strftime(config.filename_date_format)
    with RunLock(config.logs_dir / 'collection.lock'):
        logger = setup_logging(config.logs_dir, today.strftime('%Y%m%d'))
        complete = config.base_dir / run_date / 'manifest.json'
        if publish and snapshot_date is None and not force and complete.is_file():
            prior = json.loads(complete.read_text(encoding='utf-8'))
            if prior.get('status') == 'SUCCESS' and prior.get('published') is True:
                # 완료 기록뿐 아니라 최종 파일도 다시 검사한다.
                for record in prior['files']:
                    if inspect_pdf(config.base_dir / run_date / 'pdf' / record['name']) != record:
                        raise RuntimeError('완료본 파일이 실행 기록과 다릅니다.')
                    if inspect_pdf(config.knowledge_folder / record['name']) != record:
                        raise RuntimeError('지식 폴더의 완료본이 실행 기록과 다릅니다.')
                logger.info('오늘 수집·배포 완료본을 다시 확인했습니다.')
                return dict(prior, reused=True)
        run_id = today.strftime('%Y%m%d') + '-' + uuid.uuid4().hex[:12]
        work = config.base_dir / '.runs' / run_id
        html_dir, pdf_dir = work / 'html', work / 'pdf'
        html_dir.mkdir(parents=True)
        pdf_dir.mkdir()
        records, stats = [], []
        result = {'run_id': run_id, 'date': run_date, 'status': 'FAILED', 'published': False,
                  'reused': False, 'files': [], 'warnings': []}
        snapshot_stage = config.snapshots_dir / '.runs' / run_id
        try:
            if snapshot_date:
                for project in config.projects:
                    saved = list(load_snapshot(config.snapshots_dir, run_date, project.id).values())
                    if not saved:
                        raise RuntimeError(f'수집 저장본이 없습니다: {project.id} / {run_date}')
                    records.extend(saved)
                    stats.append(CollectStats(project_id=project.id, total_items=len(saved), expected_total=len(saved)))
            else:
                with PolarionClient(config.host, config.token, config.verify_ssl, config.timeout_seconds,
                                        config.request_interval_seconds, config.page_size) as client:
                    for project in config.projects:
                        client.ping(project.id)
                        count = CollectStats(project_id=project.id)
                        saved = collect_project(client, project, config, snapshot_stage / '_images', count)
                        records.extend(saved)
                        stats.append(count)
                        result['stats'] = [asdict(s) for s in stats]
                        save_snapshot(snapshot_stage, run_date, project.id, saved)
                # 예상 건수와 빈 응답을 검사한 뒤에만 스냅샷을 확정한다.
                if any(s.errors or s.total_items == 0 or s.total_items != s.expected_total for s in stats):
                    raise RuntimeError('SRS 수집 건수 또는 상세 수집 검사에 실패했습니다.')
                save_manifest(snapshot_stage, run_date, {'stats': [asdict(s) for s in stats]})
                for project in config.projects:
                    destination = config.snapshots_dir / run_date / project.id
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        previous = config.archive_dir / run_id / project.id
                        previous.parent.mkdir(parents=True, exist_ok=True)
                        destination.replace(previous)
                    (snapshot_stage / run_date / project.id).replace(destination)
            groups, warnings = assign_file_groups(records, config)
            result['warnings'].extend(warnings)
            state = ProblemState.load(config.snapshots_dir)
            pdfs, isolated = [], []
            for group in groups:
                if not group.records:
                    continue
                rendered, fallback = render_group_pdf_with_recovery(
                    group, project_label=group.records[0]['project_id'], html_dir=html_dir,
                    pdf_path=pdf_dir / build_pdf_filename(config.filename_prefix, group.display_name, stamp),
                    timeout_seconds=config.pdf_timeout_seconds, known_problem_srs=set(config.known_problem_srs),
                    problem_state=state)
                pdfs.append(rendered)
                isolated.extend(r['uid'] for r in fallback)
            state.save()
            previous_date = find_previous_snapshot_date(config.snapshots_dir, run_date)
            previous_count = sum(len(load_snapshot(config.snapshots_dir, previous_date, p.id)) for p in config.projects) if previous_date else None
            validation = validate_run(collect_stats=stats, all_records=records, pdf_results=pdfs,
                expected_pdf_count=len([g for g in groups if g.records]), partition_warnings=warnings,
                assigned_records=[r for g in groups for r in g.records],
                required_pdf_names=[build_pdf_filename(config.filename_prefix, g.display_name, stamp) for g in groups if g.records],
                previous_srs_count=previous_count, min_expected_srs_ratio=config.min_expected_srs_ratio,
                require_all_pdfs=config.require_all_pdfs)
            result['checks'] = validation.checks
            result['stats'] = [asdict(s) for s in stats]
            result['warnings'].extend(f'서식 단순화: {uid}' for uid in isolated)
            result['warnings'].extend(f'이미지 실패: {r["uid"]}' for r in records if any(not i.get('ok') for i in r.get('image_results', [])))
            if not validation.ok:
                raise RuntimeError('SRS·PDF 검사가 실패해 배포하지 않습니다.')
            result['files'] = [inspect_pdf(p.path) for p in pdfs]
            if not publish:
                result['status'] = 'PREVIEW'
                return result
            # 공개 PDF 폴더 자체도 별도 준비하고 최종 파일을 검사한다.
            public = config.base_dir / run_date
            candidate = work / 'public'
            incoming = candidate / 'pdf'
            incoming.mkdir(parents=True)
            for p in pdfs:
                shutil.copy2(p.path, incoming / p.path.name)
                if inspect_pdf(incoming / p.path.name) != next(f for f in result['files'] if f['name'] == p.path.name):
                    raise RuntimeError('공개 준비본 PDF 검사가 실패했습니다.')
            def finalize(deployment):
                result.update(deployment=asdict(deployment), published=True, status='SUCCESS')
                _json(candidate / 'manifest.json', result)
                previous = work / 'previous-public'
                replaced = False
                try:
                    if public.exists():
                        public.replace(previous)
                    candidate.replace(public)
                    replaced = True
                    for record in result['files']:
                        if inspect_pdf(public / 'pdf' / record['name']) != record:
                            raise RuntimeError('최종 공개 PDF가 원본과 다릅니다.')
                    if json.loads(complete.read_text(encoding='utf-8')) != result:
                        raise RuntimeError('최종 완료 기록을 읽을 수 없습니다.')
                except BaseException:
                    result.update(published=False, status='FAILED')
                    if replaced:
                        public.replace(work / 'failed-public')
                    if previous.exists():
                        previous.replace(public)
                    raise
            archive_and_publish(knowledge_folder=config.knowledge_folder, generated_pdfs=[p.path for p in pdfs],
                                file_date=stamp, finalize=finalize)
            logger.info('SRS 수집·PDF 배포 완료: %d건 / PDF %d개', len(records), len(pdfs))
            return result
        except Exception as exc:
            result.update(status='FAILED', published=False)
            result['error'] = type(exc).__name__
            logger.error('SRS 수집·배포 실패: %s', type(exc).__name__)
            raise
        finally:
            _json(work / 'manifest.json', result)
