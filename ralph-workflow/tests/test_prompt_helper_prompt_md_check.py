"""Tests for host-owned existing-PROMPT handling in run_prompt_helper."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from ralph.cli.commands.prompt_helper import run_prompt_helper

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


class TestPromptMdCheck:
    """Tests for host-owned existing-PROMPT handling in run_prompt_helper."""

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
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

    def test_existing_prompt_md_is_seeded_without_a_replace_refine_menu(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Detecting PROMPT.md seeds it as agent context with no up-front menu.

        The host does not ask "Replace it / Refine it" — detection alone means
        "refine the existing prompt", and the agent produces a fresh draft from
        it. The only user interaction is the post-artifact review choice.
        """
        self._stub_runtime(monkeypatch)
        # An artifact is produced from the seeded PROMPT.md.
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: {
                "title": "Notes App",
                "scope": "scope",
                "goals": ["g"],
                "users": ["u"],
                "success_criteria": ["c"],
            },
        )
        prompt_md = workspace_root / "PROMPT.md"
        prompt_md.write_text("# Existing prompt\nBuild a notes app.", encoding="utf-8")

        prompt_calls: list[tuple[str, tuple[str, ...] | None]] = []

        def fake_prompt_ask(message: str, *args: object, **kwargs: object) -> str:
            del args
            raw_choices = kwargs.get("choices")
            choice_values = tuple(raw_choices) if isinstance(raw_choices, list) else None
            prompt_calls.append((message, choice_values))
            return "Accept"

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            fake_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        content = prompt_file.read_text(encoding="utf-8")
        assert "Build a notes app." in content
        assert "Replace it" not in content
        assert "Refine it" not in content
        # No up-front idea prompt and no replace/refine menu — only the review
        # choice (Refine / Accept) was shown.
        assert prompt_calls == [(prompt_calls[0][0], ("Refine", "Accept"))]
