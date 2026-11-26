from __future__ import annotations

from pathlib import Path

import ralph.prompts

_UNATTENDED = (
    Path(ralph.prompts.__file__).parent / "templates" / "shared" / "_unattended_mode.jinja"
)


def test_every_unattended_prompt_steers_search_away_from_recursive_grep() -> None:
    text = " ".join(_UNATTENDED.read_text(encoding="utf-8").split())
    assert "Search with `git grep` or `rg`" in text
    assert "named pipe" in text
