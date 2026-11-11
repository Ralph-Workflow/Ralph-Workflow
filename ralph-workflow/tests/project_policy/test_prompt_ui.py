from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.project_policy import _prompt_ui

if TYPE_CHECKING:
    import pytest

_CHOICES = (
    _prompt_ui.PromptChoice(key="adopt", title="Adopt", description="one-time setup"),
    _prompt_ui.PromptChoice(key="keep", title="Keep", description="no enforcement"),
)


def test_select_returns_default_when_the_prompt_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class _Questionary:
        def select(self, *args: object, **kwargs: object) -> object:
            raise EOFError("stdin closed")

    monkeypatch.setattr(_prompt_ui, "_load_questionary", _Questionary)

    assert _prompt_ui.select("Q?", _CHOICES, "adopt") == "adopt"


def test_select_returns_default_when_the_user_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class _Aborted:
        def ask(self) -> None:
            return None

    class _Questionary:
        def select(self, *args: object, **kwargs: object) -> _Aborted:
            return _Aborted()

    monkeypatch.setattr(_prompt_ui, "_load_questionary", _Questionary)

    assert _prompt_ui.select("Q?", _CHOICES, "keep") == "keep"


def test_select_returns_the_chosen_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Answered:
        def ask(self) -> str:
            return "keep"

    class _Questionary:
        Choice = staticmethod(lambda **kwargs: kwargs)

        def select(self, *args: object, **kwargs: object) -> _Answered:
            return _Answered()

    monkeypatch.setattr(_prompt_ui, "_load_questionary", _Questionary)

    assert _prompt_ui.select("Q?", _CHOICES, "adopt") == "keep"
