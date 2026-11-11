from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.config.loader import ConfigTomlError, load_toml
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    ResolutionTerminationReason,
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.workspace.context import WorkspaceContextError, workspace_context
from ralph.workspace.scope import WorkspaceScope


def test_load_toml_directory_raises_config_toml_error(tmp_path: Path) -> None:
    dir_path = tmp_path / "ralph-workflow.toml"
    dir_path.mkdir(parents=True)

    with pytest.raises(ConfigTomlError) as exc_info:
        load_toml(dir_path)

    msg = str(exc_info.value)
    assert "What failed: Ralph could not read" in msg
    assert str(dir_path) in msg
    assert "Why it matters: settings in a malformed file are not safe to use." in msg
    assert f"Fix: correct the TOML syntax in {dir_path}, then run `ralph --check-config`." in msg


def test_load_toml_permission_denied_raises_config_toml_error(tmp_path: Path) -> None:
    file_path = tmp_path / "ralph-workflow.toml"
    file_path.write_text("[general]\n", encoding="utf-8")
    file_path.chmod(0o000)

    try:
        try:
            file_path.read_text(encoding="utf-8")
            is_root = True
        except PermissionError:
            is_root = False

        if is_root:
            with pytest.MonkeyPatch.context() as mp:

                def _raise_permission_error(*_args: object, **_kwargs: object) -> str:
                    raise PermissionError(f"[Errno 13] Permission denied: '{file_path}'")

                mp.setattr(Path, "read_text", _raise_permission_error)
                with pytest.raises(ConfigTomlError) as exc_info:
                    load_toml(file_path)
        else:
            with pytest.raises(ConfigTomlError) as exc_info:
                load_toml(file_path)

        msg = str(exc_info.value)
        assert "What failed: Ralph could not read" in msg
        assert str(file_path) in msg
        assert "Why it matters: settings in a malformed file are not safe to use." in msg
        assert (
            f"Fix: correct the TOML syntax in {file_path}, then run `ralph --check-config`." in msg
        )
    finally:
        file_path.chmod(0o644)


def test_workspace_context_wraps_directory_in_place_of_config(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_dir = agent_dir / "ralph-workflow.toml"
    cfg_dir.mkdir()

    with pytest.raises(WorkspaceContextError) as exc_info, workspace_context(tmp_path):
        pass

    exc = exc_info.value
    assert exc.file == cfg_dir
    assert f"fix {cfg_dir} then run ralph --check-config" in exc.fix


def test_auto_integrate_resolver_directory_in_place_of_config(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_dir = agent_dir / "ralph-workflow.toml"
    cfg_dir.mkdir()

    session = ResolutionSession()
    mock_display = MagicMock()
    mock_policy = MagicMock()
    mock_registry = MagicMock()
    mock_config = MagicMock()
    mock_deps = MagicMock()
    mock_scope = WorkspaceScope(root=tmp_path)

    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=mock_policy,
        registry=mock_registry,
        display=mock_display,
        config=mock_config,
        pipeline_deps=mock_deps,
        workspace_scope=mock_scope,
    )

    stop = RebaseStop(
        sha="def5678",
        subject="test directory config conflict",
        conflicted_files=("file.txt",),
        stop_index=1,
        stop_cap=10,
    )

    with bind_active_rebase_resolution_session(session):
        outcome = resolver(tmp_path, "main", stop)

    assert isinstance(outcome, ResolutionOutcome)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert f"fix {cfg_dir} then run ralph --check-config" in str(outcome.reason)
    assert session.charge_conflict_budget is False
    assert session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE
    assert session.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE


def test_auto_integrate_resolver_unreadable_permission_denied_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True)
    cfg_file = agent_dir / "ralph-workflow.toml"
    cfg_file.write_text("[general]\n", encoding="utf-8")
    cfg_file.chmod(0o000)

    try:
        try:
            cfg_file.read_text(encoding="utf-8")
            is_root = True
        except PermissionError:
            is_root = False

        if is_root:
            orig_read_text = Path.read_text

            def _read_text_permission(
                path_obj: Path, encoding: str | None = None, errors: str | None = None
            ) -> str:
                if path_obj == cfg_file:
                    raise PermissionError(f"[Errno 13] Permission denied: '{cfg_file}'")
                return orig_read_text(path_obj, encoding=encoding, errors=errors)

            monkeypatch.setattr(Path, "read_text", _read_text_permission)

        session = ResolutionSession()
        mock_display = MagicMock()
        mock_policy = MagicMock()
        mock_registry = MagicMock()
        mock_config = MagicMock()
        mock_deps = MagicMock()
        mock_scope = WorkspaceScope(root=tmp_path)

        resolver = build_agent_rebase_stop_resolver(
            policy_bundle=mock_policy,
            registry=mock_registry,
            display=mock_display,
            config=mock_config,
            pipeline_deps=mock_deps,
            workspace_scope=mock_scope,
        )

        stop = RebaseStop(
            sha="abc9876",
            subject="test permission denied config conflict",
            conflicted_files=("file.txt",),
            stop_index=1,
            stop_cap=10,
        )

        with bind_active_rebase_resolution_session(session):
            outcome = resolver(tmp_path, "main", stop)

        assert isinstance(outcome, ResolutionOutcome)
        assert outcome.succeeded is False
        assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
        assert f"fix {cfg_file} then run ralph --check-config" in str(outcome.reason)
        assert session.charge_conflict_budget is False
        assert session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE
        assert session.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    finally:
        cfg_file.chmod(0o644)
