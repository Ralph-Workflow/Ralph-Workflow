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


def test_an_unanswered_prompt_expires_to_no_answer_instead_of_waiting_forever() -> None:
    delays: list[float] = []

    class _Loop:
        def call_later(self, delay: float, callback: object) -> None:
            delays.append(delay)
            assert callable(callback)
            callback()

    class _Application:
        def __init__(self) -> None:
            self.pre_run_callables: list[object] = []
            self.loop = _Loop()
            self.future = None
            self.exited = False

        def exit(self, *, result: object = None) -> None:
            assert result is None
            self.exited = True

    class _Unanswered:
        def __init__(self) -> None:
            self.application = _Application()

        def ask(self) -> None:
            for run in self.application.pre_run_callables:
                assert callable(run)
                run()
            assert self.application.exited

    class _Questionary:
        Choice = staticmethod(lambda **kwargs: kwargs)

        def select(self, *args: object, **kwargs: object) -> _Unanswered:
            return _Unanswered()

    answer = _prompt_ui.select("Q?", _CHOICES, "adopt", questionary_module=_Questionary)

    assert answer == _prompt_ui.NO_ANSWER
    assert delays == [_prompt_ui.UNANSWERED_PROMPT_SECONDS]
