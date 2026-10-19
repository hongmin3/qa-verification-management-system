"""Immutable server document snapshots and full latest QA instructions."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import tempfile
from threading import Lock

from .skill.scripts.corpus import Corpus
from app.core.product_knowledge import load_manifest, collected_path, manifest_path, product_dir
from app.core.skill_knowledge import parse_bundle

KINDS = ("specification", "manual", "testcase", "release_note", "qa_rules", "instruction_prompt", "skill_instruction")
_snapshot_lock = Lock()


def revision(name):
    match = re.search(r"Rev\.?\s*(\d+(?:\.\d+)*)", name, re.I)
    return tuple(int(n) for n in match[1].split(".")) if match else (0,)


def skill_status(project_root, reviewed_hash=""):
    assets = [a for a in load_manifest("VXvue", project_root).get("assets", []) if a.get("kind") == "skill_instruction"]
    if not assets:
        return {"version": None, "status": "waiting", "code_review_needed": False}
    try:
        if len(assets) != 1 or assets[0].get("error"):
            raise ValueError("source-read-failed")
        path = collected_path("VXvue", assets[0], project_root)
        if not path.resolve().is_relative_to(product_dir("VXvue", project_root).resolve()):
            raise ValueError("invalid-path")
        data = path.read_bytes()
        if assets[0].get("sha256") and hashlib.sha256(data).hexdigest() != assets[0]["sha256"]:
            raise ValueError("source-hash-mismatch")
        metadata = parse_bundle(data.decode("utf-8-sig"))
        return {"version": metadata["version"], "plugin": metadata["plugin"], "status": "ready",
                "code_review_needed": metadata["code_hash"] != reviewed_hash}
    except (OSError, ValueError):
        return {"version": None, "status": "error", "code_review_needed": False}


def prepare_knowledge(storage, destination, project_root=None):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with _snapshot_lock, tempfile.TemporaryDirectory(prefix="building-", dir=destination) as temp:
        building = Path(temp)
        root = building / "original"
        root.mkdir()
        docs = [dict(d) for kind in KINDS for d in storage.active_documents(kind, "VXvue")]
        # Rules are synced assets, not registered analysis documents. Canonical
        # manifest wins over legacy manually registered rule copies.
        project_root = Path(project_root) if project_root else storage.db_path.parent.parent
        manifest = load_manifest("VXvue", project_root)
        if manifest_path("VXvue", project_root).is_file() and not manifest:
            raise ValueError("지식 동기화 자산 목록을 읽지 못했습니다. 동기화 상태를 확인해주세요.")
        if manifest:
            docs = [d for d in docs if d["kind"] not in ("qa_rules", "instruction_prompt", "skill_instruction")]
            for index, asset in enumerate(manifest.get("assets", [])):
                if asset.get("kind") not in ("qa_rules", "instruction_prompt", "skill_instruction"):
                    continue
                path = collected_path("VXvue", asset, project_root)
                if not path.resolve().is_relative_to(product_dir("VXvue", project_root).resolve()):
                    raise ValueError("지식 동기화 목록의 경로를 확인해주세요.")
                docs.append({"id": f"asset-{index}", "kind": asset["kind"], "name": asset["file_name"],
                             "path": str(path), "error": asset.get("error"), "expected_sha256": asset.get("sha256")})
        from app.core.qa_rule_source import select_rule_documents
        selected = select_rule_documents(docs, require_prompt=True)
        required = {d["kind"]: d for d in selected}
        docs = [d for d in docs if d["kind"] not in ("qa_rules", "instruction_prompt") or d in selected]
        skill_docs = [d for d in docs if d["kind"] == "skill_instruction"]
        if len(skill_docs) > 1:
            raise ValueError("Skill 지침 동기화본이 여러 개입니다. 적용할 원본을 확인해주세요.")
        skill_metadata, skill_text = None, ""
        records, failures = [], []
        for doc in docs:
            name = Path(doc["name"].replace("\\", "/")).name
            if not name or name in (".", ".."):
                failures.append({"name": doc["name"], "error": "invalid-name"})
                continue
            try:
                if doc.get("error"):
                    raise ValueError("source-read-failed")
                data = Path(doc["path"]).read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if doc.get("expected_sha256") and digest != doc["expected_sha256"]:
                    raise ValueError("source-hash-mismatch")
                target = root / name
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                        continue
                    target = root / f"{doc['id']}-{name}"
                target.write_bytes(data)
                records.append({"document_id": target.name, "registration_id": doc["id"], "name": doc["name"], "kind": doc["kind"], "sha256": digest})
            except (OSError, ValueError) as exc:
                failures.append({"name": doc["name"], "error": type(exc).__name__})
        rules = []
        for kind, doc in required.items():
            try:
                text = Path(doc["path"]).read_text(encoding="utf-8-sig")
                if not text.strip():
                    raise ValueError("empty-rule")
                expected = next((r for r in records if r["registration_id"] == doc["id"]), None)
                if expected is None or hashlib.sha256(text.encode("utf-8")).hexdigest() != hashlib.sha256((root / expected["document_id"]).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest():
                    raise ValueError("rule-changed-during-copy")
                rules.append(f"\n\n## {doc['name']}\n{text}")
            except (OSError, ValueError) as exc:
                raise ValueError("최신 지침을 읽지 못했습니다. 동기화 상태를 확인해주세요.") from exc
        if skill_docs:
            doc = skill_docs[0]
            expected = next((r for r in records if r["registration_id"] == doc["id"]), None)
            if expected is None:
                raise ValueError("Skill 지침을 읽지 못했습니다. 동기화 상태를 확인해주세요.")
            skill_text = (root / expected["document_id"]).read_text(encoding="utf-8-sig")
            skill_metadata = parse_bundle(skill_text)
        identity = hashlib.sha256(json.dumps({"documents": records, "failures": failures}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        final = destination / identity
        if not final.exists():
            status = Corpus(root, building / "index.sqlite3").get_status()
            failures.extend({"name": entry["document_id"], "error": entry.get("type", "source-read-failed")} for entry in status["scope"].get("errors", []))
            building.rename(final)
            # TemporaryDirectory may clean a missing path; its moved contents are retained.
        elif (final / "manifest.json").is_file():
            failures = json.loads((final / "manifest.json").read_text(encoding="utf-8")).get("failures", failures)
        snapshot = {"id": identity, "root": str(final / "original"), "index": str(final / "index.sqlite3"), "documents": records, "failures": failures, "rules": "".join(rules), "skill_metadata": skill_metadata, "skill_text": skill_text}
        (final / "manifest.json").write_text(json.dumps({k: v for k, v in snapshot.items() if k not in ("rules", "skill_text")}, ensure_ascii=False), encoding="utf-8")
        return snapshot
