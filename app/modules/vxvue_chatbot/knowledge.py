"""Immutable server document snapshots and full latest QA instructions."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import tempfile
from threading import Lock

from .skill.scripts.corpus import Corpus

KINDS = ("specification", "manual", "testcase", "qa_rules", "instruction_prompt")
_snapshot_lock = Lock()


def revision(name):
    match = re.search(r"Rev\.?\s*(\d+(?:\.\d+)*)", name, re.I)
    return tuple(int(n) for n in match[1].split(".")) if match else (0,)


def prepare_knowledge(storage, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with _snapshot_lock, tempfile.TemporaryDirectory(prefix="building-", dir=destination) as temp:
        building = Path(temp)
        root = building / "original"
        root.mkdir()
        docs = [dict(d) for kind in KINDS for d in storage.active_documents(kind, "VXvue")]
        required = {}
        for kind in ("instruction_prompt", "qa_rules"):
            candidates = [d for d in docs if d["kind"] == kind]
            if not candidates:
                raise ValueError("지침 프롬프트와 QA 규칙 MD를 Knowledge에 동기화한 뒤 질문해주세요.")
            latest = max(revision(d["name"]) for d in candidates)
            selected = [d for d in candidates if revision(d["name"]) == latest]
            if len({Path(d["path"]).read_bytes() for d in selected if Path(d["path"]).is_file()}) > 1:
                raise ValueError("같은 개정 번호의 지침이 서로 다릅니다. 적용할 문서를 확인해주세요.")
            required[kind] = selected[0]
        records, failures = [], []
        for doc in docs:
            name = Path(doc["name"].replace("\\", "/")).name
            if not name or name in (".", ".."):
                failures.append({"name": doc["name"], "error": "invalid-name"})
                continue
            try:
                data = Path(doc["path"]).read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                target = root / name
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                        continue
                    target = root / f"{doc['id']}-{name}"
                target.write_bytes(data)
                records.append({"document_id": target.name, "registration_id": doc["id"], "name": doc["name"], "kind": doc["kind"], "sha256": digest})
            except OSError as exc:
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
        identity = hashlib.sha256(json.dumps({"documents": records, "failures": failures}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        final = destination / identity
        if not final.exists():
            status = Corpus(root, building / "index.sqlite3").get_status()
            failures.extend({"name": entry["document_id"], "error": entry.get("type", "source-read-failed")} for entry in status["scope"].get("errors", []))
            building.rename(final)
            # TemporaryDirectory may clean a missing path; its moved contents are retained.
        elif (final / "manifest.json").is_file():
            failures = json.loads((final / "manifest.json").read_text(encoding="utf-8")).get("failures", failures)
        snapshot = {"id": identity, "root": str(final / "original"), "index": str(final / "index.sqlite3"), "documents": records, "failures": failures, "rules": "".join(rules)}
        (final / "manifest.json").write_text(json.dumps({k: v for k, v in snapshot.items() if k != "rules"}, ensure_ascii=False), encoding="utf-8")
        return snapshot
