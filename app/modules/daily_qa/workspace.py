"""Claude 가 읽고 쓰는 격리 작업 폴더 (SPEC NFR-SEC-001).

구조 (`daily_qa.workspace_dir`):

    CLAUDE.md                 무인 실행 규칙 (이 모듈이 매번 새로 쓴다)
    .claude/settings.json     도구 허용 범위 (이 모듈이 매번 새로 쓴다)
    .claude/skills/<skill>/   저장소의 app/modules/daily_qa/skills 를 매번 그대로 복사
    rules/qa-guide.md         수집된 QA 규칙 사본 (저장소에 커밋하지 않는 사내 자료)
    rules/instruction-prompt.txt
    runs/<실행ID>/in/         작업 입력 (마스킹 후)
    runs/<실행ID>/context/    SRS·TC 전체 색인, 매뉴얼 텍스트 (마스킹 후)
    runs/<실행ID>/out/        Skill 이 쓰는 결과 JSON — Claude 가 쓸 수 있는 유일한 곳

작업 폴더는 저장소 밖이어야 한다. Claude Code 는 실행 폴더와 그 **모든 상위 폴더**의
CLAUDE.md 를 읽으므로, 저장소 안에 두면 개발용 지침(커밋·푸시 규칙 포함)이 무인 실행에
섞여 들어간다. 그래서 상위 어디에든 CLAUDE.md/AGENTS.md 가 있으면 실행을 거부한다.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.security_filter import mask_payload, mask_text

SKILLS_SOURCE = Path(__file__).parent / "skills"
GUIDE_NAME = "qa-guide.md"
PROMPT_NAME = "instruction-prompt.txt"
AGENT_DOC_NAMES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md")

#: Claude 에게 허용하는 도구. 명령 실행·웹·MCP 는 없다.
ALLOWED_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Skill",
    "Write(runs/**/out/**)",
    "Edit(runs/**/out/**)",
)
DENIED_TOOLS = (
    "Bash",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "Task",
    "Agent",
)

CLAUDE_MD = """# 무인 실행 규칙 (VXvue 일일 QA 점검)

이 폴더는 서버의 일일 점검이 만든 격리 작업 폴더다. 사람이 대화로 답할 수 없다.

- 요청받은 Skill 하나만 수행하고, 결과는 지정된 `runs/<실행ID>/out/<작업ID>.json` 한 파일에만 쓴다.
- 입력 파일, `context/`, `rules/` 밖의 자료를 찾지 않는다. 인터넷·명령 실행은 쓰지 않는다.
- 사람에게 물어야 할 것은 결과 JSON 의 `open_questions` 에 적고, 판정은 `사양 확인 필요` 로 둔다.
- 이슈 종료, TC 원본 수정, TC 결과·이력 삭제를 제안하지 않는다 (QA 규칙 §55).
"""


class WorkspaceError(RuntimeError):
    pass


@dataclass
class RunWorkspace:
    root: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / self.run_id

    @property
    def in_dir(self) -> Path:
        return self.run_dir / "in"

    @property
    def out_dir(self) -> Path:
        return self.run_dir / "out"

    @property
    def context_dir(self) -> Path:
        return self.run_dir / "context"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def validate_location(workspace: Path, repo_root: Path) -> Path:
    """작업 폴더 위치 검사. 통과하면 절대 경로를 돌려준다."""
    resolved = workspace.expanduser().resolve()
    repo = repo_root.resolve()
    if resolved == repo or repo in resolved.parents:
        raise WorkspaceError(f"작업 폴더가 저장소 안에 있습니다: {resolved}. daily_qa.workspace_dir 를 저장소 밖으로 지정하세요.")
    if resolved in repo.parents:
        raise WorkspaceError(f"작업 폴더가 저장소를 포함합니다: {resolved}")
    for parent in resolved.parents:
        for name in AGENT_DOC_NAMES:
            if (parent / name).is_file():
                raise WorkspaceError(f"작업 폴더의 상위 폴더에 {parent / name} 가 있어 무인 실행에 섞여 들어갑니다.")
    return resolved


def _write_json(path: Path, payload: object) -> dict:
    """마스킹한 뒤 쓴다. 마스킹 통계를 돌려준다 (감사 기록용)."""
    masked, report = mask_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(masked, ensure_ascii=False, indent=1), encoding="utf-8")
    return report.as_dict()


def _write_jsonl(path: Path, items: list[dict]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            masked, report = mask_payload(item)
            total += report.total
            handle.write(json.dumps(masked, ensure_ascii=False) + "\n")
    return {"masked": total}


def prepare(workspace_dir: Path, repo_root: Path, run_id: str, guide: Path | None, prompt: Path | None) -> RunWorkspace:
    root = validate_location(workspace_dir, repo_root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    settings = {
        "permissions": {"allow": list(ALLOWED_TOOLS), "deny": list(DENIED_TOOLS), "defaultMode": "dontAsk"},
        "env": {
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        "enableAllProjectMcpServers": False,
    }
    claude_dir = root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    skills_target = claude_dir / "skills"
    if skills_target.exists():
        shutil.rmtree(skills_target)
    shutil.copytree(SKILLS_SOURCE, skills_target)
    rules_dir = root / "rules"
    rules_dir.mkdir(exist_ok=True)
    for source, name in ((guide, GUIDE_NAME), (prompt, PROMPT_NAME)):
        target = rules_dir / name
        if source and source.is_file():
            text, _ = mask_text(source.read_text(encoding="utf-8", errors="replace"))
            target.write_text(text, encoding="utf-8")
        elif target.exists():
            target.unlink()
    run = RunWorkspace(root=root, run_id=run_id)
    for directory in (run.in_dir, run.out_dir, run.context_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return run


def write_task_input(run: RunWorkspace, task_id: str, payload: dict) -> tuple[Path, dict]:
    path = run.in_dir / f"{task_id}.json"
    return path, _write_json(path, payload)


def write_context(run: RunWorkspace, srs_items: list[dict], tc_rows: list[dict], manuals: dict[str, str]) -> dict:
    stats = {
        "srs": _write_jsonl(run.context_dir / "srs_current.jsonl", srs_items),
        "tc": _write_jsonl(run.context_dir / "tc_index.jsonl", tc_rows),
        "manuals": [],
    }
    manual_dir = run.context_dir / "manuals"
    manual_dir.mkdir(exist_ok=True)
    for name, text in manuals.items():
        masked, report = mask_text(text)
        (manual_dir / name).write_text(masked, encoding="utf-8")
        stats["manuals"].append({"file": name, "masked": report.total})
    return stats
