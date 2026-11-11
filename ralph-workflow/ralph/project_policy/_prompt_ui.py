
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from loguru import logger


@dataclass(frozen=True)
class PromptChoice:

    key: str
    title: str
    description: str


SelectFn = Callable[[str, Sequence[PromptChoice], str], str]


class _Question:
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
    return cast(
        "_Questionary", import_module("questionary")
    )


def select(question: str, choices: Sequence[PromptChoice], default: str) -> str:
    try:
        questionary = _load_questionary()
        items = [
            questionary.Choice(
                title=choice.title,
                value=choice.key,
                description=choice.description,
            )
            for choice in choices
        ]
        answer: object = questionary.select(
            question,
            choices=items,
            default=default,
            show_description=True,
            instruction="(use the arrow keys, then Enter)",
        ).ask()
    except Exception as exc:
        logger.debug("policy selection prompt failed (non-fatal): {}", exc)
        return default
    if isinstance(answer, str):
        return answer
    return default


__all__ = ["PromptChoice", "SelectFn", "select"]
