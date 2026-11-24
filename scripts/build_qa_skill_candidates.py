"""Build and hash four QA Skill release candidates without installing or publishing."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts import build_alm_skill, build_plm_skill, build_vxvue_desktop


def build(server, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    packages = {}
    for name, filename, builder in (
        ("alm", "alm-skill.zip", build_alm_skill.build_skill),
        ("plm", "plm-skill.zip", build_plm_skill.build_skill),
    ):
        path = destination / filename
        info = builder(server, path)
        packages[name] = {"file": filename, **info}
    for name, data in (
        ("vxvue-ai", build_vxvue_desktop.skill_bytes()),
        ("bellalun-server-ai", build_vxvue_desktop.bellalun_skill_bytes()),
    ):
        filename = name + "-skill.zip"
        with (destination / filename).open("xb") as handle:
            handle.write(data)
        packages[name] = {"file": filename, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}
    manifest = {"status": "candidate_not_published", "packages": packages}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.server, args.output_dir), ensure_ascii=True))
