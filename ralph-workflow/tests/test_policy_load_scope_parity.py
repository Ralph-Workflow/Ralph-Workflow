"""``--check-config --config <file>`` must validate the same policy the run loads.

``ralph --check-config --config <file>`` loaded policy from the config file's
directory via the raw :func:`ralph.policy.loader.load_policy`, which REPLACES the bundled pipeline with a workspace ``pipeline.toml`` (instead of
merging it) and ignores the user-global pipeline/artifacts files, so a partial
``.agent/pipeline.toml`` the real run accepts was rejected, and a user-global
override the run applied was never checked.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.cli import main as cli_main
from ralph.policy.loader import load_policy_for_config_dir, load_policy_for_workspace_scope
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

_PARTIAL_PIPELINE = "[development_timebox]\nduration_seconds = 7200\nwarning_seconds = 6000\n"


def _workspace(tmp_path: Path) -> Path:
    agent_dir = tmp_path / "repo" / ".agent"
    agent_dir.mkdir(parents=True)
    return agent_dir


def test_partial_workspace_pipeline_merges_onto_the_bundled_pipeline(tmp_path: Path) -> None:
    """A pipeline.toml that only tunes the timebox must load like it does for the run."""
    agent_dir = _workspace(tmp_path)
    (agent_dir / "pipeline.toml").write_text(_PARTIAL_PIPELINE, encoding="utf-8")

    bundle = load_policy_for_config_dir(agent_dir)
    run_bundle = load_policy_for_workspace_scope(WorkspaceScope(agent_dir.parent))

    timebox = bundle.pipeline.development_timebox
    assert timebox is not None
    assert timebox.duration_seconds == 7200
    assert bundle.pipeline == run_bundle.pipeline


def test_user_global_pipeline_override_is_applied(tmp_path: Path) -> None:
    """The user-global pipeline file the run layers in must also be checked."""
    agent_dir = _workspace(tmp_path)
    global_pipeline = Path(os.environ["XDG_CONFIG_HOME"]) / "ralph-workflow-pipeline.toml"
    global_pipeline.parent.mkdir(parents=True, exist_ok=True)
    global_pipeline.write_text(_PARTIAL_PIPELINE, encoding="utf-8")

    bundle = load_policy_for_config_dir(agent_dir)

    timebox = bundle.pipeline.development_timebox
    assert timebox is not None
    assert timebox.duration_seconds == 7200


def test_check_config_with_explicit_config_accepts_a_partial_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--check-config --config`` must accept what the run accepts."""
    agent_dir = _workspace(tmp_path)
    (agent_dir / "pipeline.toml").write_text(_PARTIAL_PIPELINE, encoding="utf-8")
    config_file = agent_dir / "ralph-workflow.toml"
    config_file.write_text("[general]\n", encoding="utf-8")
    # Agent binaries on the host PATH are not what this test is about.
    monkeypatch.setattr(cli_main, "validate_chain_agents_on_path", lambda _agents: None)

    exit_code = cli_main.handle_check_config(
        str(config_file), {}, True, console=Console(file=None, force_terminal=False)
    )

    assert exit_code == 0
