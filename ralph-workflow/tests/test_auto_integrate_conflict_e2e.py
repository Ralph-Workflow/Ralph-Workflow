"""End-to-end proof of the real conflict-resolution chain (AC-06, AC-07).

Two links in the chain were never exercised together against a real
repository:

* The PRODUCTION resolver factory
  :func:`ralph.pipeline.auto_integrate_agent.build_agent_conflict_resolver`
  was only ever tested against ``MagicMock`` objects with no git, while
  every real-conflict test used a hand-written in-test closure. The
  factory's own plumbing -- policy drain lookup, registry lookup, prompt
  file, invocation bounds -- was therefore unproven end to end.
* Both runner-seam test files fabricate ``RebaseState(last_action=
  'conflict')`` by hand and contain zero real repositories, so the seam
  had never driven a genuine rebase-conflict to endpoint-merge to
  resolution to fast-forward sequence.

File-level markers. ``subprocess_e2e`` excludes this file from ``make
test`` (the budget-tracked 60 s step): every test here drives real git
through :func:`auto_integrate_after_commit`.
``timeout_seconds(20)`` sizes the budget for a full conflicted
integration plus its merge commit. This does not weaken any cap: the
file stays out of the 60 s combined budget and inside the 60 s
per-suite cap on ``make test-subprocess-e2e``.

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
from ralph.pipeline import auto_integrate_agent as resolver_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_agent import build_agent_conflict_resolver
from ralph.pipeline.auto_integrate_record import record_path
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.policy.models import PhaseDefinition, PolicyBundle

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]

_PROMPT_RELATIVE_PATH = Path(".agent") / "auto_integrate_conflict_prompt.md"


def _run(
    repo_root: Path, *args: str, timeout: float = 20.0
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root`` with a configurable timeout."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
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
    """Build a real ``UnifiedConfig`` with the auto-integrate knobs set."""
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


def _commit_phase_def() -> PhaseDefinition:
    """Return the first commit-role phase of the default policy."""
    for phase_def in _default_policy_bundle().pipeline.phases.values():
        if phase_def.role == "commit":
            return phase_def
    message = "default policy has no commit-role phase"
    raise AssertionError(message)


def _install_editing_agent(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Stub ``invoke_agent`` with a file-editing, git-free resolver agent.

    The stub honours the production contract stated in
    :mod:`ralph.pipeline.auto_integrate_agent`: it reads the prompt Ralph
    wrote, rewrites each conflicted file listed there with marker-free
    content, and runs NO git command.
    """
    prompts: list[Path] = []

    def _fake_invoke(
        agent_config: object, prompt_file: str, *, options: object = None
    ) -> Iterator[str]:
        prompt_path = Path(prompt_file)
        prompts.append(prompt_path)
        root = prompt_path.parent.parent
        for line in prompt_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- `") or not stripped.endswith("`"):
                continue
            relative = stripped[3:-1]
            (root / relative).write_text(
                "feature version\nbase version 1\n", encoding="utf-8"
            )
        return iter(())

    monkeypatch.setattr(resolver_module, "invoke_agent", _fake_invoke)
    return prompts


def _head_parents(repo_root: Path) -> list[str]:
    """Parent SHAs of HEAD, in order."""
    out = _run(repo_root, "rev-list", "--parents", "-n", "1", "HEAD")
    return out.stdout.split()[1:]


def test_production_resolver_factory_resolves_a_real_conflict_end_to_end(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-06: the real factory, not a test closure, lands a conflicted merge."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    prompts = _install_editing_agent(monkeypatch)
    resolver = build_agent_conflict_resolver(
        policy_bundle=_default_policy_bundle(),
        registry=AgentRegistry.from_config(config),
        display=MagicMock(),
        config=config,
    )

    outcome = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=resolver,
    )

    assert prompts == [tmp_git_repo / _PROMPT_RELATIVE_PATH]
    assert outcome is not None
    assert outcome.last_action == "merged"
    assert outcome.fast_forwarded is True
    # Ralph -- not the agent -- created a real two-parent merge commit.
    assert len(_head_parents(tmp_git_repo)) == 2
    assert not (tmp_git_repo / ".git" / "MERGE_HEAD").exists()
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(tmp_git_repo, base) == head
    # The transient prompt and the durable crash record are both gone.
    assert not (tmp_git_repo / _PROMPT_RELATIVE_PATH).exists()
    assert not record_path(tmp_git_repo).exists()


def test_runner_commit_seam_drives_the_full_conflict_chain(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-07: the seam plumbing, not a MagicMock, lands a conflicted merge."""
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    _install_editing_agent(monkeypatch)

    outcome = runner_module._maybe_auto_integrate(
        effect=CommitEffect(message_file="unused"),
        event=PipelineEvent.COMMIT_SUCCESS,
        commit_phase_def=_commit_phase_def(),
        config=config,
        workspace_scope=WorkspaceScope(tmp_git_repo),
        state=PipelineState.from_policy(_default_policy_bundle().pipeline),
        display=MagicMock(),
        policy_bundle=_default_policy_bundle(),
        registry=AgentRegistry.from_config(config),
    )

    assert outcome is not None
    assert outcome.fast_forwarded is True
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    assert branch_sha(tmp_git_repo, base) == head
    assert len(_head_parents(tmp_git_repo)) == 2
    assert not (tmp_git_repo / ".git" / "MERGE_HEAD").exists()
