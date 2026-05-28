#!/usr/bin/env python3
"""Sync selected research findings to the Research-Findings git repo.

Designed to be testable:
- file selection is declarative
- git operations are isolated behind a runner
- dry-run mode reports what would happen without mutating the repo
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

SCRIPT_NAME = Path(__file__).name
WORKSPACE = Path("/home/mistlight/.openclaw/workspace")
SYNC_PATH_SPECS: tuple[str, ...] = (
    ".gitignore",
    "AGENTS.md",
    "TOOLS.md",
    "outreach-log.md",
    "marketing",
    "seo-reports",
    "content",
    "drafts",
    "memory/*.md",
    "agents/marketing/CLEANUP_PLAN.md",
    "agents/marketing/STRATEGY.md",
    "agents/marketing/SKILLS_RESEARCH.md",
    "agents/marketing/generate_content.py",
    "agents/marketing/run.py",
    "agents/marketing/run_posting.py",
    "agents/marketing/sync_research.py",
    "agents/marketing/tests",
    "agents/unblocker/BLOCKED_CHANNELS.json",
    "agents/unblocker/run.py",
    "agents/unblocker/tests",
    "agents/*/logs",
)


@dataclass
class SyncPlan:
    specs: list[str]
    resolved_paths: list[str]
    has_changes: bool
    commit_message: str


class GitRunner:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run(self, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.workspace), *args],
            check=check,
            text=True,
            capture_output=True,
        )


def git_pathspecs(specs: Iterable[str]) -> list[str]:
    pathspecs: list[str] = []
    for spec in specs:
        if any(ch in spec for ch in "*?[]"):
            pathspecs.append(f":(glob){spec}")
        else:
            pathspecs.append(spec)
    return pathspecs


def parse_status_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for raw_line in stdout.splitlines():
        if len(raw_line) < 4:
            continue
        payload = raw_line[3:].strip()
        candidates = payload.split(" -> ") if " -> " in payload else [payload]
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
    return paths


def changed_paths(runner: GitRunner, pathspecs: Sequence[str]) -> list[str]:
    if not pathspecs:
        return []
    result = runner.run(["status", "--short", "--", *git_pathspecs(pathspecs)], check=False)
    return parse_status_paths(result.stdout)


def resolve_sync_paths(workspace: Path, specs: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        if any(ch in spec for ch in "*?[]"):
            matches = sorted(workspace.glob(spec))
            for match in matches:
                rel = match.relative_to(workspace).as_posix()
                if rel not in seen:
                    seen.add(rel)
                    resolved.append(rel)
        else:
            path = workspace / spec
            if path.exists():
                rel = path.relative_to(workspace).as_posix()
                if rel not in seen:
                    seen.add(rel)
                    resolved.append(rel)
    return resolved


def has_staged_changes(runner: GitRunner) -> bool:
    result = runner.run(["diff", "--cached", "--quiet"], check=False)
    return result.returncode != 0


def has_uncommitted_changes(runner: GitRunner, pathspecs: Sequence[str]) -> bool:
    return bool(changed_paths(runner, pathspecs))


def build_commit_message() -> str:
    result = subprocess.run(["date", "+%Y-%m-%d"], text=True, capture_output=True, check=True)
    return f"Automated research sync - {result.stdout.strip()}"


def build_sync_plan(workspace: Path, specs: Iterable[str], commit_message: str, runner: GitRunner | None = None) -> SyncPlan:
    spec_list = list(specs)
    resolved = resolve_sync_paths(workspace, spec_list)
    if runner is None:
        has_changes = False
    else:
        stage_paths = list(dict.fromkeys([*resolved, *changed_paths(runner, spec_list)]))
        if stage_paths:
            runner.run(["add", "-A", "--", *stage_paths])
        has_changes = has_staged_changes(runner)
    return SyncPlan(specs=spec_list, resolved_paths=resolved, has_changes=has_changes, commit_message=commit_message)


def execute_sync(workspace: Path, specs: Iterable[str], dry_run: bool = False) -> dict:
    commit_message = build_commit_message()

    if dry_run:
        runner = GitRunner(workspace)
        resolved = resolve_sync_paths(workspace, specs)
        return {
            "ok": True,
            "dry_run": True,
            "resolved_paths": resolved,
            "has_changes": has_uncommitted_changes(runner, list(specs)),
            "commit_message": commit_message,
        }

    runner = GitRunner(workspace)
    plan = build_sync_plan(workspace, specs, commit_message, runner=runner)

    if not plan.has_changes:
        return {
            "ok": True,
            "changed": False,
            "message": "No changes to sync.",
            "resolved_paths": plan.resolved_paths,
        }

    commit = runner.run(["commit", "-m", plan.commit_message])
    push = runner.run(["push", "origin", "master"])
    head = runner.run(["rev-parse", "HEAD"])
    remote = runner.run(["rev-parse", "origin/master"])
    return {
        "ok": True,
        "changed": True,
        "commit_message": plan.commit_message,
        "commit_stdout": commit.stdout.strip(),
        "push_stdout": push.stdout.strip(),
        "head": head.stdout.strip(),
        "origin_master": remote.stdout.strip(),
        "resolved_paths": plan.resolved_paths,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync research findings to git")
    parser.add_argument("--workspace", default=str(WORKSPACE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = execute_sync(Path(args.workspace), SYNC_PATH_SPECS, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


# ── Self-improvement ──────────────────────────────────────────────────────────

SELF_IMPROVEMENT_MANDATE = {
    "owner": "marketing_system",
    "goal": "improve real marketing outcomes, not just technical health",
    "default_decision_rule": "it is up to the system to decide and proceed",
    "required_when_outcomes_flat": [
        "create_new_agents",
        "repair_existing_agents",
        "rewrite_prompts",
        "change_cron_jobs",
        "patch_marketing_scripts",
        "add_or_tighten_tests",
        "retire_stale_paths",
        "generate_new_distribution_assets",
    ],
    "technical_repairs_alone_are_insufficient": True,
}

# Note: sync_research.py is a git sync reporter, not a direct loop driver.
# Self-improvement for reporters means: if outcomes are flat, the system should
# expand what research findings are synced or improve the sync process.

def flat_outcome_count(script_name: str, max_runs: int = 3) -> int:
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    """Return True if this loop has had flat outcomes for 3+ consecutive runs."""
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == "__main__":
    raise SystemExit(main())
