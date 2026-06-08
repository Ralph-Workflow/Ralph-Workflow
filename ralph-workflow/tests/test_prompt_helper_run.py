"""Tests for ralph/cli/commands/prompt_helper.py — run_prompt_helper."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

import pytest

from pathlib import Path

from ralph.cli.commands.prompt_helper import ReviewAction, _run_single_invoke, run_prompt_helper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.models import UnifiedConfig


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


class TestRunPromptHelper:
    """Tests for run_prompt_helper."""

    def _stub_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeRuntime:
            def __enter__(self) -> _FakeRuntime:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                del exc_type, exc, tb

            def invoke_prompt_file(
                self,
                prompt_file: Path,
                *,
                session_id: str | None = None,
                session_id_sink: object | None = None,
                required_artifact: object | None = None,
            ) -> Iterator[str]:
                del prompt_file, session_id, session_id_sink, required_artifact
                return iter(())

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )

    def test_creates_prompt_file(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_prompt_helper creates prompt file at .agent/tmp/prompt_helper_prompt.md."""
        self._stub_runtime(monkeypatch)

        # No artifact - session ends silently
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        assert prompt_file.exists()

    def test_prompt_file_contains_tool_name(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt file contains the submit_artifact_tool_name."""
        self._stub_runtime(monkeypatch)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        content = prompt_file.read_text(encoding="utf-8")
        assert "ralph_submit_artifact" in content

    def test_does_not_write_prompt_md_when_no_artifact(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no product_spec artifact, PROMPT.md is NOT written."""
        self._stub_runtime(monkeypatch)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_md_file = workspace_root / "PROMPT.md"
        assert not prompt_md_file.exists()

    def test_writes_prompt_md_when_artifact_exists(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When artifact exists, _handle_artifact_exists is invoked."""
        self._stub_runtime(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: spec,
        )

        # Mock _handle_artifact_exists to capture the call
        handle_called = {"called": False, "spec": None}

        def mock_handle(
            workspace_root: Path,
            runtime: object,
            existing_prompt_context: str | None,
            submit_artifact_tool_name: str,
            spec: dict[str, object],
            session_id: str | None,
        ) -> None:
            del (
                workspace_root,
                runtime,
                existing_prompt_context,
                submit_artifact_tool_name,
                session_id,
            )
            handle_called["called"] = True
            handle_called["spec"] = spec

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._handle_artifact_exists",
            mock_handle,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert handle_called["called"], "_handle_artifact_exists was not called"
        assert handle_called["spec"] == spec

    def test_no_artifact_means_no_review_loop(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no artifact exists, _handle_artifact_exists is NOT invoked."""
        self._stub_runtime(monkeypatch)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        # Mock _handle_artifact_exists to track if it's called
        handle_called = {"called": False}

        def mock_handle(*args: object, **kwargs: object) -> None:
            handle_called["called"] = True

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._handle_artifact_exists",
            mock_handle,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert not handle_called["called"], (
            "_handle_artifact_exists was called but should not have been"
        )

    def test_prompt_helper_uses_managed_agent_session_runtime(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt-helper runs through the public managed-session runtime."""
        runtime_calls: list[tuple[str, str | None]] = []

        class _FakeRuntime:
            def __enter__(self) -> _FakeRuntime:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                del exc_type, exc, tb

            def invoke_prompt_file(
                self,
                prompt_file: Path,
                *,
                session_id: str | None = None,
                session_id_sink: object | None = None,
                required_artifact: object | None = None,
            ) -> Iterator[str]:
                del required_artifact, session_id_sink
                runtime_calls.append((prompt_file.read_text(encoding="utf-8"), session_id))
                return iter(())

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert len(runtime_calls) == 1
        assert "What do you want to build or define?" in runtime_calls[0][0]
        assert runtime_calls[0][1] is None

    def test_run_single_invoke_returns_stream_observed_session_id(
        self,
    ) -> None:
        runtime_calls: list[str | None] = []

        class _FakeRuntime:
            def invoke_prompt_file(
                self,
                prompt_file: Path,
                *,
                session_id: str | None = None,
                session_id_sink: object | None = None,
                required_artifact: object | None = None,
            ) -> Iterator[str]:
                del prompt_file, required_artifact
                runtime_calls.append(session_id)
                if callable(session_id_sink):
                    session_id_sink("sess-helper")
                return iter(["ok"])

        prompt_file = Path("PROMPT.md")
        assert _run_single_invoke(_FakeRuntime(), prompt_file) == "sess-helper"
        assert runtime_calls == [None]

    def test_review_action_enum_values(self) -> None:
        """ReviewAction enum has expected values."""
        assert ReviewAction.CONTINUE.value == "continue"
        assert ReviewAction.UPDATE_SECTION.value == "update"
        assert ReviewAction.START_OVER.value == "start_over"
        assert ReviewAction.FINISH.value == "finish"

    def test_refine_rejects_prompt_md_symlink_outside_workspace(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refine flow must not read a PROMPT.md symlink that escapes the workspace."""
        self._stub_runtime(monkeypatch)
        outside_prompt = workspace_root.parent / "outside-prompt.md"
        outside_prompt.write_text("secret", encoding="utf-8")
        (workspace_root / "PROMPT.md").symlink_to(outside_prompt)
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "Refine it",
        )

        with pytest.raises(ValueError, match="outside workspace root"):
            run_prompt_helper(config_with_helper_agent, workspace_root)
