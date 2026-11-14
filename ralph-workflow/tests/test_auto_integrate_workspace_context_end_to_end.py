from __future__ import annotations

from pathlib import Path

from ralph.pipeline import StopResolutionTrigger, attempt_stop_resolution
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.resolution_outcome import ConfigFailureOutcome, ResolvedOutcome
from ralph.workspace.context import WorkspaceContextError, workspace_context


def _seed_workspace(root: Path, *, prompt: str) -> None:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (root / "PROMPT.md").write_text(prompt, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")


def _record(root: Path) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
    )
    write_record(root, record)
    return record


def test_public_resolution_entry_uses_the_target_workspace_context(tmp_path: Path) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    _seed_workspace(caller, prompt="CALLER PROMPT")
    _seed_workspace(target, prompt="TARGET PROMPT")
    observed: dict[str, object] = {}

    def launch() -> ResolvedOutcome:
        with workspace_context(target) as context:
            observed["root"] = context.root
            observed["prompt"] = context.prompt_path.read_text(encoding="utf-8")
            observed["config"] = context.config.model_dump_json()
        return ResolvedOutcome()

    result = attempt_stop_resolution(
        "stop",
        StopResolutionTrigger(
            name="rebase_stop",
            root=caller,
            target="main",
            resolver=object(),
            record=_record(caller),
            eligible_agents=("resolver",),
            config_fingerprint="target-config",
            launcher=launch,
        ),
    )

    assert isinstance(result, ResolvedOutcome)
    assert observed["root"] == target.resolve()
    assert observed["prompt"] == "TARGET PROMPT"
    assert observed["prompt"] != (caller / "PROMPT.md").read_text(encoding="utf-8")
    assert read_record(caller) is not None


def test_target_configuration_failure_is_typed_and_caller_context_is_unchanged(
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    _seed_workspace(caller, prompt="CALLER PROMPT")
    _seed_workspace(target, prompt="TARGET PROMPT")
    target_config = target / ".agent" / "ralph-workflow.toml"
    target_config.write_text("[general\n", encoding="utf-8")
    caller_prompt = (caller / "PROMPT.md").read_bytes()

    def launch() -> ConfigFailureOutcome:
        try:
            with workspace_context(target):
                pass
        except WorkspaceContextError as exc:
            return ConfigFailureOutcome(exc.file, exc.reason)
        raise AssertionError("corrupt target configuration must fail closed")

    result = attempt_stop_resolution(
        "stop",
        StopResolutionTrigger(
            name="rebase_stop",
            root=caller,
            target="main",
            resolver=object(),
            record=_record(caller),
            eligible_agents=("resolver",),
            config_fingerprint="broken-target-config",
            launcher=launch,
        ),
    )

    assert isinstance(result, ConfigFailureOutcome)
    assert result.file == target_config
    assert (caller / "PROMPT.md").read_bytes() == caller_prompt
    assert read_record(caller) is not None
