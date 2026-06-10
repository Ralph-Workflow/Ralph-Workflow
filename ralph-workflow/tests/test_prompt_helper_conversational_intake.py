"""Regression tests: the prompt-helper agent runs non-interactively.

The agent must never converse with the user. The only conversation is between
the user and the host orchestrator. When the agent's single turn produces no
artifact, the host must NOT fall into a chat loop — it surfaces the failure and
leaves no PROMPT.md behind.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.cli.commands.prompt_helper import run_prompt_helper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


class TestPromptHelperNonInteractive:
    """The agent gets one non-interactive turn; the host owns all conversation."""

    def test_no_artifact_does_not_prompt_for_chat_and_writes_no_prompt_md(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A turn that submits no artifact must not re-prompt the user to chat."""
        invoke_count = {"count": 0}

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
                invoke_count["count"] += 1

                def _turn() -> Iterator[str]:
                    yield "Working on it..."
                    # Resumable exit without ever submitting an artifact.
                    raise OpenCodeResumableExitError("claude", session_id="resume-123")

                return _turn()

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        ask_calls: list[tuple[str, ...] | None] = []

        def fake_prompt_ask(*args: object, **kwargs: object) -> str:
            raw_choices = kwargs.get("choices")
            ask_calls.append(tuple(raw_choices) if isinstance(raw_choices, list) else None)
            return "A notes app with tags and search."

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            fake_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # Exactly one agent turn; no second, conversational re-invoke.
        assert invoke_count["count"] == 1
        # Only the single up-front idea prompt was shown — no chat follow-up.
        assert ask_calls == [None]
        assert not (workspace_root / "PROMPT.md").exists()
