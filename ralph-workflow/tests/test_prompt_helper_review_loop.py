"""Tests for ralph/cli/commands/prompt_helper.py — post-artifact refine/accept loop."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from ralph.cli.commands.prompt_helper import run_prompt_helper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig

_SPEC: dict[str, object] = {
    "title": "Test Title",
    "scope": "Test scope",
    "goals": ["Goal 1"],
    "users": ["User 1"],
    "success_criteria": ["Criterion 1"],
}


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


class TestRefineAcceptLoop:
    """Tests for the post-artifact refine/accept loop state machine."""

    def _setup_base_runtime(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        outputs: list[str] | None = None,
    ) -> MagicMock:
        """Set up a fake managed-session runtime; returns a per-call MagicMock."""
        mock_invoke_runtime = MagicMock(return_value=iter(outputs or []))

        class _FakeRuntime:
            def __enter__(self) -> _FakeRuntime:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                del exc_type, exc, tb

            def invoke_prompt_file(
                self,
                prompt_file: object,
                *,
                session_id: str | None = None,
                session_id_sink: object | None = None,
                required_artifact: object | None = None,
            ) -> Iterator[str]:
                del prompt_file, session_id, session_id_sink, required_artifact
                return mock_invoke_runtime()

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: _SPEC,
        )
        return mock_invoke_runtime

    def test_accept_writes_prompt_md(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Accept writes PROMPT.md from the artifact."""
        mock_invoke = self._setup_base_runtime(monkeypatch)

        # Idea prompt (no choices) returns the seed; review prompt returns Accept.
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "An idea" if kwargs.get("choices") is None else "Accept",
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert (workspace_root / "PROMPT.md").exists(), "PROMPT.md should be written on Accept"
        # Only the initial artifact-producing turn ran; Accept does not re-invoke.
        assert mock_invoke.call_count == 1

    def test_run_does_not_delete_retired_json_artifact(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt-helper no longer owns cleanup of the retired JSON artifact path."""
        self._setup_base_runtime(monkeypatch)
        legacy_artifact = workspace_root / ".agent" / "artifacts" / "product_spec.json"
        legacy_artifact.parent.mkdir(parents=True)
        legacy_artifact.write_text('{"title": "legacy"}', encoding="utf-8")
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "An idea" if kwargs.get("choices") is None else "Accept",
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert legacy_artifact.read_text(encoding="utf-8") == '{"title": "legacy"}'

    def test_refine_reinvokes_agent_then_accept_writes_prompt_md(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refine re-invokes the agent with the draft, then Accept writes PROMPT.md."""
        mock_invoke = self._setup_base_runtime(monkeypatch)

        # Review choices in order: Refine (with feedback), then Accept.
        review_calls = [0]

        def mock_prompt_ask(*args: object, **kwargs: object) -> str:
            raw_choices = kwargs.get("choices")
            if raw_choices is None:
                # Idea prompt and refine-feedback prompt are freeform.
                return "Please add tagging and search."
            if review_calls[0] == 0:
                review_calls[0] += 1
                return "Refine"
            return "Accept"

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            mock_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # Agent invoked twice: initial artifact + one refine.
        assert mock_invoke.call_count == 2
        assert (workspace_root / "PROMPT.md").exists(), "PROMPT.md should be written on Accept"

    def test_refine_does_not_write_prompt_md_until_accept(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PROMPT.md is only written when the user accepts, never on Refine alone."""
        self._setup_base_runtime(monkeypatch)

        write_called = [False]

        def mock_write_prompt_md(
            workspace_root: Path,
            spec: dict[str, object],
            *,
            display_context: object = None,
        ) -> None:
            del workspace_root, spec, display_context
            write_called[0] = True

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._write_prompt_md",
            mock_write_prompt_md,
        )

        # Refine twice, then Accept — assert no write happened before Accept.
        review_sequence = ["Refine", "Refine", "Accept"]

        def mock_prompt_ask(*args: object, **kwargs: object) -> str:
            if kwargs.get("choices") is None:
                return "Some feedback."
            # The first review choice must be Refine and must not have written yet.
            choice = review_sequence.pop(0)
            if choice == "Refine":
                assert not write_called[0], "PROMPT.md written before Accept"
            return choice

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            mock_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert write_called[0], "PROMPT.md should be written once the user accepts"

    def test_accept_with_realistic_agent_output(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Accept works even when the agent streams realistic output.

        The host, not the agent, owns the post-artifact state machine.
        """
        self._setup_base_runtime(
            monkeypatch,
            outputs=[
                "Structuring the specification...",
                "SUBMITTING ARTIFACT: product_spec",
            ],
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "An idea" if kwargs.get("choices") is None else "Accept",
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert (workspace_root / "PROMPT.md").exists()
