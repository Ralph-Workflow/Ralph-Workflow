"""Regression tests for prompt-helper conversational intake turns."""

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


class TestPromptHelperConversationalIntake:
    """Prompt-helper must stay interactive before any artifact exists."""

    def test_resumable_turn_without_artifact_prompts_for_user_input_and_resumes(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A pre-artifact Claude turn must return control to the user, not fail."""
        prompt_payloads: list[str] = []
        session_ids: list[str | None] = []
        artifact_reads = {"count": 0}
        spec = {
            "title": "Notes App",
            "scope": "A small personal notes app with tags.",
            "goals": ["Capture quick notes"],
            "users": ["Solo users"],
            "success_criteria": ["A user can create and organize notes"],
        }

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
                prompt_payloads.append(prompt_file.read_text(encoding="utf-8"))
                session_ids.append(session_id)
                if len(prompt_payloads) == 1:

                    def _first_turn() -> Iterator[str]:
                        yield "What do you want to build?"
                        raise OpenCodeResumableExitError("claude", session_id="resume-123")

                    return _first_turn()

                def _second_turn() -> Iterator[str]:
                    yield "Thanks — I turned that into a draft specification."
                    raise OpenCodeResumableExitError("claude", session_id="resume-123")

                return _second_turn()

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )

        def fake_read_product_spec_artifact(
            *args: object,
            **kwargs: object,
        ) -> dict[str, object] | None:
            del args, kwargs
            artifact_reads["count"] += 1
            if artifact_reads["count"] == 1:
                return None
            return spec

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            fake_read_product_spec_artifact,
        )

        prompt_calls: list[tuple[str, tuple[str, ...] | None]] = []

        def fake_prompt_ask(message: str, *args: object, **kwargs: object) -> str:
            del args
            raw_choices = kwargs.get("choices")
            choice_values = tuple(raw_choices) if isinstance(raw_choices, list) else None
            prompt_calls.append((message, choice_values))
            if raw_choices is None:
                return "A notes app with tags and search."
            return "Finish"

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            fake_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert len(prompt_payloads) == 2
        assert "What do you want to build or define?" in prompt_payloads[0]
        assert "A notes app with tags and search." in prompt_payloads[1]
        assert session_ids[1] == "resume-123"
        assert prompt_calls[0][1] is None
        assert prompt_calls[1][1] is not None
        assert (workspace_root / "PROMPT.md").exists()
