from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from rich.markup import escape

prompt_reader = importlib.import_module("ralph.display.prompt_reader")

if TYPE_CHECKING:
    from pathlib import Path


def test_read_prompt_preview_missing_file_returns_placeholder(tmp_path: Path) -> None:
    result = prompt_reader.read_prompt_preview(tmp_path / "PROMPT.md")

    assert result == ("[dim]PROMPT.md not found[/dim]",)


def test_read_prompt_preview_returns_escaped_preview_lines(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("one\ntwo\n[red]three[/red]\nfour\nfive", encoding="utf-8")

    result = prompt_reader.read_prompt_preview(prompt_path)

    assert result == tuple(
        escape(line) for line in ["one", "two", "[red]three[/red]", "four", "five"]
    )


def test_read_prompt_preview_caps_read_at_eight_kib(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_bytes(b"a" * 20_000)

    result = prompt_reader.read_prompt_preview(prompt_path)

    assert len("\n".join(result)) <= prompt_reader.MAX_PROMPT_BYTES


def test_read_prompt_preview_escapes_markup(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("[red]inject[/red]", encoding="utf-8")

    result = prompt_reader.read_prompt_preview(prompt_path)

    assert result == (escape("[red]inject[/red]"),)


def test_read_prompt_preview_tolerates_non_utf8_bytes(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_bytes(b"\xff\xfe")

    result = prompt_reader.read_prompt_preview(prompt_path)

    assert result


def test_read_prompt_preview_limits_to_ten_lines(tmp_path: Path) -> None:
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("\n".join(f"line {i}" for i in range(12)), encoding="utf-8")

    result = prompt_reader.read_prompt_preview(prompt_path)

    assert len(result) == prompt_reader.PREVIEW_LINES
    assert result[0] == "line 0"
    assert result[-1] == "line 9"


def test_find_prompt_path_returns_none_for_missing_workspace(tmp_path: Path) -> None:
    assert prompt_reader.find_prompt_path(tmp_path / "missing") is None
