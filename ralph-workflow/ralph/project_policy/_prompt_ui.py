from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeGuard

import questionary
from loguru import logger


@dataclass(frozen=True)
class PromptChoice:
    key: str
    title: str
    description: str


SelectFn = Callable[[str, Sequence[PromptChoice], str], str]


class _Question(Protocol):
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
        show_description: bool = False,
        instruction: str = "",
    ) -> _Question: ...


def _is_questionary(value: object) -> TypeGuard[_Questionary]:
    choice = getattr(value, "Choice", None)
    select_fn = getattr(value, "select", None)
    return callable(choice) and callable(select_fn)


def _load_questionary() -> _Questionary:
    if not _is_questionary(questionary):
        msg = "questionary module must expose Choice and select"
        raise RuntimeError(msg)
    return questionary


def select(question: str, choices: Sequence[PromptChoice], default: str) -> str:
    try:
        questionary_api = _load_questionary()
        items = [
            questionary_api.Choice(
                title=choice.title,
                value=choice.key,
                description=choice.description,
            )
            for choice in choices
        ]
        answer: object = questionary_api.select(
            question,
            choices=items,
            default=default,
            show_description=True,
            instruction="(use the arrow keys, then Enter)",
        ).ask()
    except (AttributeError, EOFError, OSError, RuntimeError, ValueError) as exc:
        logger.debug("policy selection prompt failed (non-fatal): {}", exc)
        return default
    if isinstance(answer, str):
        return answer
    return default


__all__ = ["PromptChoice", "SelectFn", "select"]
