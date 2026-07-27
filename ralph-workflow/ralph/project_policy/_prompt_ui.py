"""Arrow-key selection prompt used by the project-policy readiness prompts.

The policy prompts ask questions whose answers are expensive to get wrong
(adopting the managed policy commits the user to a one-time remediation
run; declining disables policy enforcement for the repository). A yes/no
confirm cannot carry that much meaning, so the prompts render a menu of
named choices instead.

:func:`select` is the only entry point. It is a thin, defensive wrapper
around :mod:`questionary`:

* Every failure mode — a terminal that cannot host a full-screen prompt,
  EOF on stdin despite ``isatty``, Ctrl-C — returns ``default`` instead of
  raising. Interactivity must never block or crash a run; that contract
  predates this module and is preserved here.
* The caller passes and receives stable choice *keys*, never display
  strings, so prompt copy can be reworded without touching control flow.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from loguru import logger


@dataclass(frozen=True)
class PromptChoice:
    """One selectable menu item.

    Attributes:
        key: Stable identifier returned by :func:`select`. Control flow
            branches on this, never on the displayed text.
        title: The single-line label shown in the menu.
        description: Longer guidance shown under the menu while the item
            is highlighted. Explains the consequence of choosing it.
    """

    key: str
    title: str
    description: str


#: Signature of the injectable selection seam. Takes the question, the
#: choices, and the key to fall back on; returns the chosen key.
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
    )  # cast-policy: seam: lazy third-party UI module boundary


def select(question: str, choices: Sequence[PromptChoice], default: str) -> str:
    """Ask ``question`` as an arrow-key menu and return the chosen key.

    Returns ``default`` — never raises — when the prompt cannot run or the
    user aborts it. ``default`` must be one of the choice keys; the caller
    is responsible for that invariant.
    """
    try:
        # Keep policy-module imports cheap in non-interactive pipeline runs.
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
        # A broken pipe, an EOF despite isatty, or a terminal questionary
        # cannot drive. The run continues on the default.
        logger.debug("policy selection prompt failed (non-fatal): {}", exc)
        return default
    if isinstance(answer, str):
        return answer
    # ask() returns None when the user interrupts with Ctrl-C.
    return default


__all__ = ["PromptChoice", "SelectFn", "select"]
