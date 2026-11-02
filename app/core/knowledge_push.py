"""지식 폴더를 담당자 PC 에서 운영 서버로 밀어 올리는 쪽 (보내는 편).

받는 쪽은 `app/core/knowledge_upload.py` 다. 왜 마운트가 아니라 업로드인지도 거기에 적혀 있다.

**순서가 중요하다.** 먼저 로컬에 수집하고(`sync_product`), 그 결과 manifest 를 기준으로 올린다.
스캔 결과를 바로 올리지 않는 이유는 분류·중복 해소·리비전 판정이 전부 수집 단계에서 확정되기
때문이다 — 서버가 받는 것은 "폴더에 뭐가 있더라"가 아니라 "무엇을 쓰기로 했다"여야 한다.

**서버가 이미 가진 파일은 올리지 않는다.** 초기 1회는 약 192MB 지만 이후 주간 실행은 바뀐
문서 몇 개로 줄어든다. 판단 기준은 sha256 이다 — 수정 시각은 복사·동기화로 쉽게 바뀐다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.product_knowledge import (
    collected_path,
    document_logical_key,
    is_strictly_older_name,
    load_manifest,
    product_dir,
    product_slug,
    resolve_config,
    scan_source,
    source_available,
    sync_product,
)

KST = timezone(timedelta(hours=9))

#: 큰 PDF 한 개를 사내망으로 올리는 데 걸리는 시간을 넉넉히 잡는다.
UPLOAD_TIMEOUT_SECONDS = 300


def _client(timeout: int):
    import httpx

    return httpx.Client(timeout=timeout)


def fetch_server_state(client, target_url: str, product: str) -> dict[tuple[str, str], str]:
    """서버가 가진 `(kind, file_name) → sha256`.

    조회에 실패하면 예외를 올린다 — 조용히 빈 값으로 넘어가면 전부 다시 올리게 되고,
    그것이 "왜 매주 3분씩 걸리지"의 원인이 되어도 아무 데도 남지 않는다.
    """
    response = client.get(f"{target_url.rstrip('/')}/knowledge/product-knowledge/state", params={"product": product})
    response.raise_for_status()
    payload = response.json()
    return {(asset["kind"], asset["file_name"]): asset.get("sha256", "") for asset in payload.get("assets", [])}


def fetch_collect_request(product: str, target_url: str, timeout: int = 10) -> str:
    """화면 `지금 수집` 이 남긴, 아직 처리되지 않은 요청 시각 (REQ-KNOW-021). 없으면 빈 문자열.

    실패하면 예외를 올린다. 요청이 없는 것으로 볼지는 부르는 쪽(`knowledge_retry.run_due`)이 정한다.
    """
    with _client(timeout) as client:
        response = client.get(f"{target_url.rstrip('/')}/knowledge/product-knowledge/request", params={"product": product})
        response.raise_for_status()
        return str(response.json().get("requested_at") or "")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_time(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def pull_person_documents(config, client, target_url: str, *, backup_root: Path, stamp: str) -> dict:
    """서버 화면 등록본 가운데 지식 폴더보다 최신인 것을 지식 폴더로 내려받는다 (REQ-SYNC-004).

    목록 조회 실패는 예외로 올린다 — 업로드를 계속할지는 부르는 쪽(`push_product`)이 정한다.
    파일 하나의 실패는 그 파일만 건너뛰고 `failed` 에 적는다.
    """
    from app.core.knowledge_upload import UploadRejected, safe_file_name

    response = client.get(f"{target_url.rstrip('/')}/knowledge/product-knowledge/registered", params={"product": config.product})
    response.raise_for_status()
    documents = response.json().get("documents", [])
    folder = Path(config.knowledge_source.dir)
    result = {"downloaded": [], "moved": [], "local_newer": [], "failed": [], "status": "SUCCESS"}
    for document in documents:
        local = [asset for asset in scan_source(config).assets if asset.sha256]
        name, kind, digest = str(document.get("name", "")), str(document.get("kind", "")), str(document.get("sha256", ""))
        if any(asset.sha256 == digest for asset in local):
            continue
        try:
            name = safe_file_name(name)
        except UploadRejected as exc:
            result["failed"].append(f"{name} ({exc})")
            continue
        key = document_logical_key(kind, name)
        same = [asset for asset in local if asset.kind == kind and document_logical_key(kind, asset.file_name) == key]
        registered = _parse_time(document.get("registered_at", ""))
        # 폴더 쪽이 리비전으로 확실히 최신이거나, 비교할 수 없는데 화면 등록 뒤에 바뀌었으면 그대로 둔다.
        newer = [
            asset for asset in same
            if is_strictly_older_name(name, asset.file_name)
            or (not is_strictly_older_name(asset.file_name, name) and registered is not None
                and (_parse_time(asset.modified) or registered) > registered)
        ]
        if newer:
            result["local_newer"].append(name)
            continue
        temporary = folder / f".~{name}.download.tmp"
        try:
            download = client.get(f"{target_url.rstrip('/')}/knowledge/download/{document['id']}")
            download.raise_for_status()
            temporary.write_bytes(download.content)
            if _file_sha256(temporary) != digest:
                raise ValueError("sha256 불일치")
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            result["failed"].append(f"{name} ({type(exc).__name__}: {exc})"[:200])
            continue
        # 새 파일을 다 받고 확인한 뒤에 기존 파일을 옮긴다. 받다가 끊겨도 폴더에서 문서가 사라지지 않는다.
        for asset in same:
            target_dir = backup_root / stamp
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(asset.source_path, target_dir / Path(asset.source_path).name)
            result["moved"].append(Path(asset.source_path).name)
        temporary.replace(folder / name)
        result["downloaded"].append(name)
    if result["failed"]:
        result["status"] = "PARTIAL"
    return result


def _pull_summary(pulled: dict | None, error: str = "") -> str:
    if error:
        return f"서버 화면 등록본 확인 실패({error})"
    return (f"서버 화면 등록본 내려받음 {len(pulled['downloaded'])}건, 지식 폴더가 더 최신 {len(pulled['local_newer'])}건, "
            f"내려받기 실패 {len(pulled['failed'])}건")


def push_product(product: str, target_url: str, *, dry_run: bool = False, timeout: int = UPLOAD_TIMEOUT_SECONDS) -> dict:
    """제품 하나의 지식 폴더를 수집해 서버로 올린다.

    반환값은 `sync_and_register` 와 같은 모양(`status`/`detail`)이라 CLI 가 두 경로를 같게
    다룰 수 있다.
    """
    config = resolve_config(product)
    if config is None:
        return {"status": "NEEDS_CONFIG", "detail": f"config/products/ 에 '{product}' 설정이 없습니다."}
    if not source_available(config):
        return {
            "status": "NEEDS_CONFIG",
            "detail": f"이 PC 에서 지식 폴더에 접근할 수 없습니다: {config.knowledge_source.dir or '(미설정)'}",
        }

    profile = getattr(config.knowledge_source, "skill_sync", None)
    if profile and profile.enabled:
        try:
            if (Path(__file__).parent / "skill_knowledge.py").is_file() and __package__ in (None, ""):
                from skill_knowledge import export_skill, read_bundle
            else:
                from app.core.skill_knowledge import export_skill, read_bundle
            if dry_run:
                read_bundle(profile.plugin, profile.skill)
            else:
                export_skill(config)
        except (OSError, ValueError) as exc:
            return {"status": "FAILED", "detail": f"Skill 지침 내보내기 실패: {type(exc).__name__}. 로컬 설치 상태와 자동 생성 문서를 확인해주세요."}

    # 0단계: 서버 화면 등록본을 먼저 지식 폴더로 내려받는다 (REQ-SYNC-004). 미리보기는 서버를 부르지 않는다.
    pulled: dict | None = None
    pull_note = ""
    if not dry_run:
        try:
            with _client(timeout) as client:
                pulled = pull_person_documents(config, client, target_url, backup_root=product_dir(config.product) / "replaced",
                                               stamp=datetime.now(KST).strftime("%Y%m%d-%H%M%S"))
            pull_note = _pull_summary(pulled)
        except Exception as exc:
            # 목록을 못 읽으면(옛 서버, 연결 끊김) 내려받기만 건너뛰고 업로드는 한다.
            pull_note = _pull_summary(None, type(exc).__name__)

    collected = sync_product(config, dry_run=dry_run)
    if collected.status in ("NEEDS_CONFIG", "FAILED"):
        return {"status": collected.status, "detail": collected.detail}

    manifest = load_manifest(config.product)
    # PC 가 읽지 못한(error) 파일도 보낸다. 빼고 보내면 서버는 그 문서의 새 판도 옛 판도 목록에 없다고 보고
    # 옛 판까지 지운다(SPEC_CODE_MISMATCH 5절 3번). 서버는 받은 파일을 스스로 다시 읽고, 못 읽으면 옛 판을 지킨다.
    assets = list(manifest.get("assets", []))
    manifest_payload = {
        "allow_prune": collected.status == "SUCCESS",
        "source_dir": manifest.get("source_dir", ""),
        "counts": manifest.get("counts", {}),
        "uploaded_from": socket.gethostname(),
        # 서버는 자기 파일 경로를 스스로 만든다. PC 경로(`source_path`)를 보내면 서버에서
        # 열 수 없는 경로가 `documents` 에 등록될 수 있어 아예 뺀다.
        "assets": [{key: value for key, value in asset.items() if key not in ("source_path", "normalized_path", "normalized_chars", "error")} for asset in assets],
        "excluded": manifest.get("excluded", []),
    }

    if dry_run:
        return {
            "status": "DRY_RUN",
            "detail": f"{collected.detail} / 업로드 대상 {len(assets)}건 (실제 전송하지 않음)",
            "uploaded": [],
            "skipped": [],
        }

    uploaded: dict[str, dict] = {}
    skipped: list[str] = []
    failed: list[str] = []
    sent_bytes = 0

    with _client(timeout) as client:
        have = fetch_server_state(client, target_url, config.product)
        for asset in assets:
            kind, file_name, digest = asset.get("kind", ""), asset.get("file_name", ""), asset.get("sha256", "")
            if have.get((kind, file_name)) == digest and digest:
                skipped.append(file_name)
                continue
            path = collected_path(config.product, asset)
            if not path.is_file():
                failed.append(f"{file_name} (수집본 없음)")
                continue
            try:
                with path.open("rb") as handle:
                    response = client.post(
                        f"{target_url.rstrip('/')}/knowledge/product-knowledge/asset",
                        data={"product": config.product, "kind": kind, "sha256": digest},
                        files={"file": (file_name, handle, "application/octet-stream")},
                    )
                response.raise_for_status()
            except Exception as exc:
                # 한 파일이 실패해도 나머지는 계속 올린다. 큰 PDF 하나 때문에 그 주 수집이
                # 통째로 비는 것이 가장 나쁘다.
                failed.append(f"{file_name} ({type(exc).__name__})")
                continue
            uploaded[file_name] = response.json()
            sent_bytes += path.stat().st_size

        manifest_payload["allow_prune"] = manifest_payload["allow_prune"] and not failed
        commit = client.post(
            f"{target_url.rstrip('/')}/knowledge/product-knowledge/commit",
            data={
                "product": config.product,
                "manifest": json.dumps(manifest_payload, ensure_ascii=False),
                "uploaded": json.dumps(uploaded, ensure_ascii=False),
            },
        )
        commit.raise_for_status()
        result = commit.json()

    detail = (
        (f"{pull_note} / " if pull_note else "")
        + f"{collected.detail} / 업로드 {len(uploaded)}건({sent_bytes / 1_048_576:.1f}MB), "
        f"서버 보유분 건너뜀 {len(skipped)}건 / {result.get('detail', '')}"
    )
    if failed:
        detail += f" / 업로드 실패 {len(failed)}건: {', '.join(failed[:5])}"
    status = result.get("status", "SUCCESS")
    # PC 수집이 PARTIAL(읽지 못한 파일 있음)이거나 업로드가 하나라도 실패하면 PARTIAL 이다(종료 코드 1).
    if (failed or collected.status == "PARTIAL" or (pulled and pulled["status"] == "PARTIAL")) and status == "SUCCESS":
        status = "PARTIAL"
    return {
        "status": status,
        "detail": detail,
        "uploaded": sorted(uploaded),
        "skipped": skipped,
        "failed": failed,
        "duplicates": result.get("duplicates", []),
        "removed": result.get("removed", []),
        # CLI 가 "읽지 못해 이전 판 유지" 줄과 읽지 못한 파일을 한 줄씩 찍는다(SPEC_CODE_MISMATCH 5절 12번).
        "kept_previous": result.get("kept_previous", []),
        "unreadable": result.get("failures", []),
        "pulled": pulled or {},
    }


def target_slug(product: str) -> str:
    """서버에서 이 제품이 쓰는 폴더 이름. 문제 확인 시 경로를 안내하는 데 쓴다."""
    return product_slug(product)


def collected_root(product: str) -> Path:
    from app.core.product_knowledge import product_dir

    return product_dir(product)
