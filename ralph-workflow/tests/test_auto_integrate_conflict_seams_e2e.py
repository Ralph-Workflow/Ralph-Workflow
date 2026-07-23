"""The conflict chain, driven from the seams that had no proof of it.

``tests/test_auto_integrate_conflict_e2e.py`` proves rebase -> abort ->
endpoint merge -> resolution pipeline -> merge commit -> fast-forward
for the AFTER-COMMIT seam. The user's report is that conflicts break
integration generally, and the other seams reach the same chain through
different callers: the phase-transition hook adds a clean-worktree
precondition and its own short-circuit table, and the startup seam
builds the production resolver itself from the loop context before the
first phase runs. Neither had an end-to-end proof, so a conflict could
be fatal there while the after-commit proof stayed green.

The agent is substituted, never launched: the process launch alone is
replaced with a deterministic editor, exactly as the after-commit proof
does. The production factory
:func:`ralph.pipeline.auto_integrate_agent.build_agent_conflict_resolver`
is still what the seam receives, so the drain lookup, the registry
lookup, the prompt render, the status-bar lifecycle, the deterministic
marker gate, the staging and the merge commit all stay real.

The negative case is the load-bearing one: an agent that CLAIMS success
while leaving a marker behind must produce no merge commit at all.
Ralph's own textual re-scan, not the agent's self-report, is the
verdict.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step): every test drives real git.
``timeout_seconds(20)`` sizes the budget for a conflicted integration
plus its merge commit. This does not weaken any cap: the file stays out
of the 60 s combined budget, and it runs under the bounded ``make
test-auto-integrate-e2e`` target that ``make verify`` invokes.

The ``_run`` / ``_base_branch`` / ``_commit`` / ``_build_config`` /
``_diverged_conflicting_repo`` helpers are duplicated here to keep this
file standalone, matching the convention documented at
tests/test_auto_integrate_race.py:11-15.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig
from ralph.git.merge import branch_sha
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline.auto_integrate import auto_integrate_on_phase_transition
from ralph.pipeline.auto_integrate_agent import build_agent_conflict_resolver
from ralph.pipeline.auto_integrate_record import record_path
from ralph.pipeline.conflict_resolution import driver as resolution_driver
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

#: Stand-in for the pipeline dependency bundle. The resolver refuses to
#: run without one (an MCP-less invocation is the defect the resolution
#: pipeline exists to remove); the real bundle is only consumed by the
#: agent launch, which these tests replace.
_PIPELINE_DEPS_SENTINEL = "pipeline-deps-sentinel"

_MERGED_CONTENT = "feature version\nbase version 1\n"
_UNRESOLVED_CONTENT = (
    "<<<<<<< HEAD\nfeature version\n=======\nbase version 1\n>>>>>>> main\n"
)


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    """Return the seed template's default branch name."""
    out = _run(tmp_git_repo, "symbolic-ref", "--quiet", "HEAD")
    return out.stdout.strip().removeprefix("refs/heads/")


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _run(repo_root, "add", filename)
    _run(repo_root, "commit", "-m", message)
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _build_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    """Set up feature/base divergence with a guaranteed shared.txt conflict."""
    base = _base_branch(tmp_git_repo)
    _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    base_seed_sha = _run(
        tmp_git_repo, "rev-parse", f"refs/heads/{base}"
    ).stdout.strip()
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _install_editing_agent(
    monkeypatch: pytest.MonkeyPatch, *, content: str = _MERGED_CONTENT
) -> list[Path]:
    """Replace the agent LAUNCH with a file-editing, git-free resolver.

    Only the process launch is replaced. The stub honours the contract
    the prompt states: it reads the prompt Ralph wrote, rewrites each
    conflicted file listed there, and runs NO git command. ``content``
    is what it writes -- pass unresolved content to model an agent that
    claims success without doing the work.
    """
    prompts: list[Path] = []

    def _fake_invoke(
        *,
        agent_name: str,
        prompt_path: Path,
        max_session_seconds: float,
        **_rest: object,
    ) -> bool:
        prompts.append(prompt_path)
        # .agent/tmp/<phase>_prompt.md -> repository root
        root = prompt_path.parents[2]
        for line in prompt_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- `") or not stripped.endswith("`"):
                continue
            relative = stripped[3:-1]
            (root / relative).write_text(content, encoding="utf-8")
        return True

    monkeypatch.setattr(resolution_driver, "invoke_resolution_agent", _fake_invoke)
    return prompts


def _head_parents(repo_root: Path) -> list[str]:
    """Parent SHAs of HEAD, in order."""
    out = _run(repo_root, "rev-list", "--parents", "-n", "1", "HEAD")
    return out.stdout.split()[1:]


def _git_dir(repo_root: Path) -> Path:
    raw = _run(repo_root, "rev-parse", "--git-dir").stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _assert_repository_is_quiescent(repo_root: Path) -> None:
    """No half-finished merge or rebase may survive an integration."""
    git_dir = _git_dir(repo_root)
    assert not (git_dir / "MERGE_HEAD").exists(), "an in-progress merge survived"
    assert not (git_dir / "rebase-merge").exists(), "a rebase-merge dir survived"
    assert not (git_dir / "rebase-apply").exists(), "a rebase-apply dir survived"
    assert not record_path(repo_root).exists(), (
        "the durable integration record was not cleared"
    )


def _assert_no_surviving_markers(repo_root: Path) -> None:
    text = (repo_root / "shared.txt").read_text(encoding="utf-8")
    for marker in _CONFLICT_MARKERS:
        assert marker not in text, f"conflict marker {marker!r} survived"


def _production_resolver(
    repo_root: Path, config: UnifiedConfig
) -> object:
    """The real factory, wired exactly as the runner seams wire it."""
    return build_agent_conflict_resolver(
        policy_bundle=_default_policy_bundle(),
        registry=AgentRegistry.from_config(config),
        display=MagicMock(),
        config=config,
        pipeline_deps=_PIPELINE_DEPS_SENTINEL,
        workspace_scope=WorkspaceScope(repo_root),
    )


def _startup_context(repo_root: Path, config: UnifiedConfig) -> MagicMock:
    """A loop context carrying the REAL policy, registry, config and scope.

    ``_run_startup_integration`` builds the resolver itself from these,
    so the production factory is exercised rather than substituted.
    """
    ctx = MagicMock()
    ctx.config = config
    ctx.workspace_scope = WorkspaceScope(repo_root)
    ctx.policy_bundle = _default_policy_bundle()
    ctx.registry = AgentRegistry.from_config(config)
    ctx.pipeline_deps = _PIPELINE_DEPS_SENTINEL
    return ctx


def _assert_conflict_chain_landed(
    tmp_git_repo: Path,
    base: str,
    outcome: RebaseState | None,
    *,
    action: str,
    parent_count: int,
) -> None:
    """The whole chain resolved and landed with the seam's expected topology."""
    assert outcome is not None
    assert outcome.last_action == action
    assert outcome.fast_forwarded is True
    assert len(_head_parents(tmp_git_repo)) == parent_count
    assert (tmp_git_repo / "shared.txt").read_text(encoding="utf-8") == _MERGED_CONTENT
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(tmp_git_repo, base) == head, "the mainline did not land"
    _assert_no_surviving_markers(tmp_git_repo)
    _assert_repository_is_quiescent(tmp_git_repo)


def test_phase_transition_seam_drives_the_full_conflict_chain(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-03: a genuine conflict is resolved and landed at a phase boundary."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    prompts = _install_editing_agent(monkeypatch)

    outcome = auto_integrate_on_phase_transition(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_production_resolver(tmp_git_repo, config),
    )

    assert prompts, "the resolution pipeline never rendered a prompt"
    _assert_conflict_chain_landed(tmp_git_repo, base, outcome, action="merged", parent_count=2)


def test_startup_seam_drives_the_full_conflict_chain(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-03: a run resumed on a conflicted branch resolves before phase one.

    The startup seam builds the production resolver from the loop
    context itself, so this also proves that wiring -- not just the
    integration chain it feeds.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    prompts = _install_editing_agent(monkeypatch)

    outcome = run_loop_module._run_startup_integration(
        _startup_context(tmp_git_repo, config)
    )

    assert prompts, "the resolution pipeline never rendered a prompt"
    _assert_conflict_chain_landed(tmp_git_repo, base, outcome, action="rebased", parent_count=1)


def test_an_agent_claiming_success_over_a_surviving_marker_lands_nothing(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verdict is Ralph's marker re-scan, never the agent's claim.

    The injected agent returns ``True`` for every round while leaving a
    conflict marker in place. No merge commit may be created, the merge
    must be aborted, and the feature branch must be bit-identical to its
    pre-integration value.
    """
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    _install_editing_agent(monkeypatch, content=_UNRESOLVED_CONTENT)
    pre_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    pre_target_sha = branch_sha(tmp_git_repo, base)

    outcome = auto_integrate_on_phase_transition(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_production_resolver(tmp_git_repo, config),
    )

    assert outcome is not None
    assert outcome.last_action == "conflict"
    assert outcome.fast_forwarded is False
    post_feature_sha = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert post_feature_sha == pre_feature_sha, "the feature branch moved"
    assert len(_head_parents(tmp_git_repo)) == 1, "a merge commit was created"
    assert branch_sha(tmp_git_repo, base) == pre_target_sha, "the mainline moved"
    _assert_repository_is_quiescent(tmp_git_repo)
