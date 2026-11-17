from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from tests._scenario_git import commit_file, git
from tests._scenario_operator_line import OperatorLine
from tests.scenarios._fake_agent_runner import FakeAgentRunner

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.config.models import UnifiedConfig
    from tests._scenario_display import ScenarioDisplay

pytestmark = pytest.mark.subprocess_e2e

__all__ = [
    "FakeAgentRunner",
    "StatusCaptureDisplay",
    "_capture_log_records",
    "capture_operator_lines",
    "corrupted_config",
    "empty_resolution_stop_snapshot",
    "log_status_outcome",
    "paused_partial_out_of_reach_rebase",
    "paused_three_commit_first_stop_rebase",
    "scenario_config",
    "shared_executor_seam",
    "supervisor_restart",
]


class StatusCaptureDisplay:
    def __init__(self) -> None:
        self.lines: list[OperatorLine] = []

    def emit_warn_line(self, _unit_id: str, _tag: str, message: str) -> None:
        self.lines.append(OperatorLine("WARNING", message))

    def emit(self, _unit_id: str | None, message: str) -> None:
        self.lines.append(OperatorLine("INFO", str(message)))


def log_status_outcome(display: StatusCaptureDisplay, outcome: object, *, root: Path) -> None:
    from ralph.pipeline.runner import log_auto_integrate_outcome

    log_auto_integrate_outcome(display, outcome, root=root)


@contextmanager
def shared_executor_seam(runner: FakeAgentRunner | None) -> Iterator[None]:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline.effect_executor import (
        install_agent_effect_dependencies,
        reset_agent_effect_dependencies,
    )
    from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

    clock = getattr(runner, "clock", None) if runner is not None else None
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock or FakeClock()))
    agent_runner = getattr(runner, "handle_agent_effect", None) if runner is not None else None
    token = install_agent_effect_dependencies(
        agent_runner=agent_runner,
        recovery_controller=controller,
    )

    try:
        yield
    finally:
        reset_agent_effect_dependencies(token)


@contextmanager
def capture_operator_lines(display: ScenarioDisplay | None = None) -> Iterator[list[OperatorLine]]:
    lines: list[OperatorLine] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        level = record.get("level")
        level_name = getattr(level, "name", "INFO")
        content = str(record.get("message", ""))
        if level_name in {"WARNING", "ERROR", "CRITICAL"} or ("waiting" in content.lower()):
            lines.append(OperatorLine(level_name, content))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        yield lines
    finally:
        logger.remove(sink_id)
        if display is not None:
            lines.extend(display.lines)


@contextmanager
def _capture_log_records() -> Iterator[list[tuple[str, str]]]:
    records: list[tuple[str, str]] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", {})
        records.append((str(record.get("name", "")), str(record.get("message", ""))))

    sink_id = logger.add(sink, level="DEBUG", format="{message}")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def scenario_config() -> UnifiedConfig:
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            },
            "conflict_resolution": {
                "max_rounds_per_stop": 2,
                "max_rebase_conflict_stops": 4,
            },
        }
    )


def corrupted_config(root: Path) -> Path:
    config = root / ".agent" / "ralph-workflow.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[general\nauto_integrate_enabled = true\n", encoding="utf-8")
    return config


def paused_partial_out_of_reach_rebase(root: Path) -> tuple[str, str, str]:
    from ralph.git.rebase.rebase import rebase_in_progress
    from tests._scenario_git import commit_file

    commit_file(root, "init.txt", "init\n", "init")
    base_oid = git(root, "rev-parse", "HEAD")
    feature_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "feature link")
    target_oid = git(root, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "target link")
    commit_file(root, "deleted.txt", "base delete/modify\n", "declared base")
    commit_file(root, "notes.txt", "base notes\n", "notes base")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_oid},vendor/link")
    git(root, "commit", "-q", "-m", "declared gitlink base")

    git(root, "checkout", "-q", "-b", "feature")
    (root / "deleted.txt").write_text("feature keeps file\n", encoding="utf-8")
    (root / "notes.txt").write_text("feature notes\n", encoding="utf-8")
    git(root, "add", "deleted.txt", "notes.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature fourth symptom set")

    git(root, "checkout", "-q", "main")
    git(root, "rm", "-q", "deleted.txt")
    (root / "notes.txt").write_text("target notes\n", encoding="utf-8")
    git(root, "add", "notes.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target fourth symptom set")

    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    index_info = (
        f"160000 {base_oid} 1\tvendor/link\n"
        f"160000 {target_oid} 2\tvendor/link\n"
        f"160000 {feature_oid} 3\tvendor/link\n"
    )
    subprocess.run(
        ("git", "update-index", "--index-info"),
        input=index_info,
        cwd=root,
        text=True,
        check=True,
        capture_output=True,
    )
    if not rebase_in_progress(root):
        raise AssertionError("partial out of reach fixture did not pause")

    entries: dict[int, str] = {}
    for line in git(root, "ls-files", "-u", "--", "vendor/link").splitlines():
        metadata, _separator, _relative = line.partition("\t")
        fields = metadata.split()
        if len(fields) == 3:
            entries[int(fields[2])] = fields[1]
    s1, s2, s3 = entries.get(1, ""), entries.get(2, ""), entries.get(3, "")
    if not (s1 and s2 and s3):
        raise AssertionError(f"expected three gitlink stages for vendor/link, got {(s1, s2, s3)}")
    from ralph.pipeline.auto_integrate_record import adopt_active_rebase_record

    adopt_active_rebase_record(root, "main")
    return (s1, s2, s3)


def supervisor_restart(root: Path) -> dict[str, object]:
    script = (
        "import json,pathlib,subprocess,sys; "
        "p=pathlib.Path(sys.argv[1]);sys.path.insert(0,sys.argv[2]); "
        "from ralph.pipeline.auto_integrate_planning import integrate_before_planning; "
        "from ralph.pipeline.rebase_state import RebaseState; "
        "from ralph.workspace.scope import WorkspaceScope; "
        "from tests.scenarios._rebase_recovery_harness import scenario_config; "
        "s=integrate_before_planning(scenario_config(),WorkspaceScope(p),RebaseState()); "
        "g=lambda *a: subprocess.run(('git',*a),cwd=p,text=True,capture_output=True,check=True).stdout.strip(); "
        "print(json.dumps({'head':g('rev-parse','HEAD'),'status':g('status','--porcelain'),"
        "'resumed':s is not None}))"
    )
    package_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        (sys.executable, "-I", "-c", script, str(root), str(package_root)),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={"HOME": str(root / ".scenario-home"), "PATH": "/usr/bin:/bin"},
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise TypeError("restart probe returned a non-object payload")
    return payload


def paused_three_commit_first_stop_rebase(root: Path) -> None:
    from ralph.git.rebase.rebase import rebase_in_progress

    commit_file(root, "shared.txt", "base\n", "scenario base")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "shared.txt", "feature stop 1\n", "feature commit 1 (conflicts)")
    for i in range(2, 4):
        commit_file(root, f"feat_{i}.txt", f"feature {i}\n", f"feature commit {i}")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("three-commit fixture did not pause at the first stop")


def empty_resolution_stop_snapshot(root: Path) -> tuple[str, str, str]:
    from ralph.git.rebase.rebase import rebase_in_progress

    commit_file(root, "shared.txt", "base\n", "base")
    commit_file(root, "series.txt", "seed\n", "seed")
    git(root, "checkout", "-q", "-b", "feature")
    pre_rebase_head = commit_file(root, "shared.txt", "feature diff\n", "feature stop")
    git(root, "checkout", "-q", "main")
    commit_file(root, "shared.txt", "target value\n", "target change")
    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)
    if not rebase_in_progress(root):
        raise AssertionError("empty-resolution fixture did not pause")
    resolved_tree = git(root, "rev-parse", "HEAD^{tree}")
    return (resolved_tree, git(root, "rev-parse", "HEAD"), pre_rebase_head)
