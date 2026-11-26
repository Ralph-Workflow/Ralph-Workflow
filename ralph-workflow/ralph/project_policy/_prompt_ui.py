from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Final, Protocol, cast

from loguru import logger


@dataclass(frozen=True)
class PromptChoice:
    key: str
    title: str
    description: str


SelectFn = Callable[[str, Sequence[PromptChoice], str], str]


class _Future(Protocol):
    def done(self) -> bool: ...


class _Loop(Protocol):
    def call_later(self, delay: float, callback: Callable[[], None]) -> object: ...


class _PromptApplication(Protocol):
    pre_run_callables: list[Callable[[], None]]
    loop: _Loop | None
    future: _Future | None

    def exit(self, *, result: object = None) -> None: ...


class _Question:
    application: _PromptApplication

    def ask(self) -> object: ...


class _ChoiceFactory(Protocol):
    def __call__(self, *, title: str, value: str, description: str) -> object: ...


class _Questionary(Protocol):
    Choice: _ChoiceFactory

    def select(
        self,
        question: str,
        *,
        choices: Sequence[object],
        default: str,
        **kwargs: object,
    ) -> _Question: ...


def _load_questionary() -> _Questionary:
    return cast("_Questionary", import_module("questionary"))


NO_ANSWER: Final[str] = ""
UNANSWERED_PROMPT_SECONDS: Final[float] = 600.0


def _expire_unanswered(prompt: _Question, timed_out: list[bool]) -> None:
    application: _PromptApplication | None = getattr(prompt, "application", None)
    if application is None:
        return

    def expire() -> None:
        future = application.future
        if future is not None and future.done():
            return
        timed_out.append(True)
        application.exit(result=None)

    def schedule() -> None:
        loop = application.loop
        if loop is not None:
            loop.call_later(UNANSWERED_PROMPT_SECONDS, expire)

    application.pre_run_callables.append(schedule)


def select(
    question: str,
    choices: Sequence[PromptChoice],
    default: str,
    *,
    questionary_module: Callable[[], object] | None = None,
) -> str:
    try:
        questionary = (
            _load_questionary()
            if questionary_module is None
            else cast("_Questionary", questionary_module())
        )
        items = [
            questionary.Choice(
                title=choice.title,
                value=choice.key,
                description=choice.description,
            )
            for choice in choices
        ]
        prompt = questionary.select(
            question,
            choices=items,
            default=default,
            show_description=True,
            instruction="(use the arrow keys, then Enter)",
        )
        timed_out: list[bool] = []
        _expire_unanswered(prompt, timed_out)
        answer: object = prompt.ask()
        if timed_out:
            return NO_ANSWER
    except (AttributeError, EOFError, OSError, RuntimeError, ValueError) as exc:
        logger.debug("policy selection prompt failed (non-fatal): {}", exc)
        return default
    if isinstance(answer, str):
        return answer
    return default


def default_is_tty() -> bool:
    try:
        stdin_tty: bool = sys.stdin.isatty()
        stdout_tty: bool = sys.stdout.isatty()
    except (OSError, ValueError, AttributeError):
        return False
    return stdin_tty and stdout_tty


__all__ = ["PromptChoice", "SelectFn", "default_is_tty", "select"]
