from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config import (
    emit_config_failure_warning,
    load_optional_toml,
    reset_fallback_warning_registry,
)
from ralph.config.unreadable_config_finding import collect_unreadable_config_files
from ralph.pipeline.resolution_outcome_producer import config_failure_from_exception
from ralph.policy.loader import PolicyValidationError
from ralph.policy.loader import load_toml as load_policy_toml
from ralph.workspace.context import workspace_context
from ralph.workspace.errors import WorkspaceContextError

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _clean_fallback_registry() -> Generator[None, None, None]:
    reset_fallback_warning_registry()
    try:
        yield
    finally:
        reset_fallback_warning_registry()


def test_config_parse_error_carries_the_file_and_the_parse_error(tmp_path: Path) -> None:
    parse_error = ValueError("boom")
    emit_config_failure_warning(
        tmp_path / "agents.toml",
        kind="agents",
        error_detail="boom",
        behaviour="using built-in default",
        fix_hint="Fix: run 'ralph --check-config'.",
        exc=parse_error,
    )

    payload = config_failure_from_exception(parse_error)
    assert payload is not None
    assert payload.file == tmp_path / "agents.toml"
    assert payload.parse_error is parse_error


def test_strict_optional_toml_exposes_the_typed_parse_error_payload(tmp_path: Path) -> None:
    broken = tmp_path / "pipeline.toml"
    broken.write_text("broken = [[[\n", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError) as exc_info:
        load_optional_toml(broken, kind="pipeline", strict=True)

    payload = config_failure_from_exception(exc_info.value)
    assert payload is not None
    assert payload.file == broken
    assert payload.parse_error is exc_info.value


def test_wrapped_parse_error_exposes_the_typed_parse_error_payload(tmp_path: Path) -> None:
    broken = tmp_path / "artifacts.toml"
    broken.write_text("broken = [[[\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError) as exc_info:
        load_policy_toml(broken, kind="artifacts", strict=True)

    payload = config_failure_from_exception(exc_info.value)
    assert payload is not None
    assert payload.file == broken
    assert isinstance(payload.parse_error, tomllib.TOMLDecodeError)


def test_workspace_context_error_exposes_the_typed_parse_error_payload(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True)
    broken = agent_dir / "ralph-workflow.toml"
    broken.write_text("[general\nbroken syntax = = =\n", encoding="utf-8")
    (workspace / "PROMPT.md").write_text("# prompt\n", encoding="utf-8")

    with pytest.raises(WorkspaceContextError) as exc_info, workspace_context(workspace):
        pass

    payload = config_failure_from_exception(exc_info.value)
    assert payload is not None
    assert payload.file == broken
    assert isinstance(payload.parse_error, tomllib.TOMLDecodeError)


def test_unreadable_config_finding_exposes_the_typed_parse_error_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "isolated-config-home"
    config_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    broken = config_home / "ralph-workflow-pipeline.toml"
    broken.write_text("broken = [[[\n", encoding="utf-8")

    findings = collect_unreadable_config_files(global_dir=config_home)

    matching = [finding for finding in findings if finding.path == broken]
    assert len(matching) == 1
    payload = config_failure_from_exception(matching[0].error)
    assert payload is not None
    assert payload.file == broken
    assert isinstance(payload.parse_error, tomllib.TOMLDecodeError)


def test_unrelated_exception_has_no_parse_error_payload() -> None:
    assert config_failure_from_exception(RuntimeError("unrelated")) is None
