from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.invoke._choice_menu_option import _ChoiceMenuOption


@dataclass(frozen=True)
class _ChoiceMenuState:
    prompt: str
    options: tuple[_ChoiceMenuOption, ...]
    selected_index: int | None
    confirm_footer: str
