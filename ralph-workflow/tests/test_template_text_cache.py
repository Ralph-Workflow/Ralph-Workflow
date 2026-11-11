from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.prompts import commit, master_prompt
from ralph.prompts.template_registry import (
    TemplateRegistry,
    _packaged_template_cache,
    _PackagedTemplateCache,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _counting_reader(text: str) -> tuple[dict[str, int], Callable[[Path], str]]:

    state: dict[str, int] = {"calls": 0}

    def reader(_path: Path) -> str:
        state["calls"] += 1
        return text

    return state, reader


def test_packaged_template_cache_reads_each_file_once() -> None:

    state, reader = _counting_reader("template-body\n")

    cache = _PackagedTemplateCache(reader=reader)

    first = cache.get("shared/_unattended_mode.jinja", root=Path("/unused"))
    second = cache.get("shared/_unattended_mode.jinja", root=Path("/unused"))
    third = cache.get("shared/_unattended_mode.jinja", root=Path("/unused"))

    assert first == "template-body\n"
    assert second == "template-body\n"
    assert third == "template-body\n"
    assert state["calls"] == 1, (
        f"reader must be invoked exactly once across 3 calls, got {state['calls']}"
    )


def test_packaged_template_cache_clear_resets_state() -> None:

    state, reader = _counting_reader("body")

    cache = _PackagedTemplateCache(reader=reader)
    cache.get("commit_message.jinja", root=Path("/unused"))
    assert state["calls"] == 1

    cache.clear()
    cache.get("commit_message.jinja", root=Path("/unused"))
    assert state["calls"] == 2, "after clear(), the next get must re-invoke the reader"


def test_packaged_template_cache_is_key_scoped() -> None:

    state, reader = _counting_reader("body")

    cache = _PackagedTemplateCache(reader=reader)

    cache.get("a.jinja", root=Path("/unused"))
    cache.get("a.jinja", root=Path("/unused"))
    cache.get("b.jinja", root=Path("/unused"))
    cache.get("b.jinja", root=Path("/unused"))
    cache.get("b.jinja", root=Path("/unused"))

    assert state["calls"] == 2, f"reader must run once per distinct key, got {state['calls']}"


def test_master_prompt_unattended_mode_uses_cache() -> None:
    state: dict[str, int] = {"calls": 0}

    def reader(_path: Path) -> str:
        state["calls"] += 1
        return "UNATTENDED-MODE-BODY"

    original = _packaged_template_cache._reader
    _packaged_template_cache._reader = reader
    _packaged_template_cache.clear()
    try:
        first = master_prompt._unattended_mode_text()
        second = master_prompt._unattended_mode_text()
        third = master_prompt._unattended_mode_text()
    finally:
        _packaged_template_cache._reader = original
        _packaged_template_cache.clear()

    assert first == "UNATTENDED-MODE-BODY"
    assert second == "UNATTENDED-MODE-BODY"
    assert third == "UNATTENDED-MODE-BODY"
    assert state["calls"] == 1, (
        f"_unattended_mode_text must read packaged template once,"
        f" got {state['calls']} reader invocations"
    )


def test_commit_select_template_uses_cache() -> None:
    state: dict[str, int] = {"calls": 0}

    def reader(_path: Path) -> str:
        state["calls"] += 1
        return "COMMIT-MESSAGE-BODY"

    original_reader = _packaged_template_cache._reader
    _packaged_template_cache._reader = reader
    _packaged_template_cache.clear()
    try:
        first = commit._select_template(None)
        second = commit._select_template(None)
        third = commit._select_template(None)
    finally:
        _packaged_template_cache._reader = original_reader
        _packaged_template_cache.clear()

    assert first == "COMMIT-MESSAGE-BODY"
    assert second == "COMMIT-MESSAGE-BODY"
    assert third == "COMMIT-MESSAGE-BODY"
    assert state["calls"] == 1, (
        f"_select_template must read packaged commit_message.jinja once, got {state['calls']}"
    )


def test_commit_opencode_simplified_uses_cache() -> None:

    state: dict[str, int] = {"calls": 0}

    def reader(_path: Path) -> str:
        state["calls"] += 1
        return "COMMIT-SIMPLIFIED-BODY"

    original_reader = _packaged_template_cache._reader
    _packaged_template_cache._reader = reader
    _packaged_template_cache.clear()
    try:
        first = commit.prompt_commit_message_for_opencode(
            "diff --git a/x b/x\n+x",
            submit_artifact_tool_name="ralph_submit_md_artifact",
        )
        second = commit.prompt_commit_message_for_opencode(
            "diff --git a/x b/x\n+y",
            submit_artifact_tool_name="ralph_submit_md_artifact",
        )
    finally:
        _packaged_template_cache._reader = original_reader
        _packaged_template_cache.clear()

    assert "COMMIT-SIMPLIFIED-BODY" in first
    assert "COMMIT-SIMPLIFIED-BODY" in second
    assert state["calls"] == 1, (
        f"prompt_commit_message_for_opencode must read"
        f" commit_simplified.jinja once, got {state['calls']}"
    )


def test_template_registry_backfills_discovered_template(tmp_path: Path) -> None:
    state: dict[str, int] = {"calls": 0}
    text = "DISCOVERED-BODY\n"

    template_root = tmp_path / "templates"
    template_root.mkdir()
    target = template_root / "review.jinja"
    target.write_text(text, encoding="utf-8")

    def reader(path: Path) -> str:
        state["calls"] += 1
        return path.read_text(encoding="utf-8")

    registry = TemplateRegistry(
        template_dirs=(template_root,),
        _read_text=reader,
    )

    first = registry.get_template("review.jinja")
    assert first == text
    assert state["calls"] == 1, (
        f"first get_template must trigger exactly one read, got {state['calls']}"
    )

    second = registry.get_template("review.jinja")
    assert second == text
    assert state["calls"] == 1, (
        f"second get_template must NOT re-read, got {state['calls']} total reader invocations"
    )

    third = registry.get_template("review.jinja")
    assert third == text
    assert state["calls"] == 1, (
        f"third get_template must NOT re-read, got {state['calls']} total reader invocations"
    )
