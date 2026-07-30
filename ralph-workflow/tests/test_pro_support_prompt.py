"""Black-box unit tests for :mod:`ralph.pro_support.prompt`."""

from __future__ import annotations

import os
import stat as stat_mod
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support import prompt as prompt_module
from ralph.pro_support.prompt import (
    DEFAULT_SOURCE_PROMPT_NAME,
    resolve_effective_prompt_path,
)
from ralph.prompts.master_prompt import _sync_product_criteria_file

if TYPE_CHECKING:
    import pytest


def test_default_is_workspace_prompt_md(tmp_path: Path) -> None:
    result = resolve_effective_prompt_path(tmp_path, env={})
    assert result == (tmp_path / "PROMPT.md").resolve()
    assert DEFAULT_SOURCE_PROMPT_NAME == "PROMPT.md"


def test_absolute_prompt_path_is_returned_as_is(tmp_path: Path) -> None:
    absolute = "/tmp/some_prompt.md"
    result = resolve_effective_prompt_path(tmp_path, env={"PROMPT_PATH": absolute})
    assert result == Path(absolute).resolve()


def test_relative_prompt_path_is_resolved_against_workspace(tmp_path: Path) -> None:
    rel = "subdir/prompt.md"
    result = resolve_effective_prompt_path(tmp_path, env={"PROMPT_PATH": rel})
    assert result == (tmp_path / "subdir" / "prompt.md").resolve()


def test_empty_prompt_path_falls_back_to_default(tmp_path: Path) -> None:
    result = resolve_effective_prompt_path(tmp_path, env={"PROMPT_PATH": ""})
    assert result == (tmp_path / "PROMPT.md").resolve()


def test_resolver_does_not_read_prompt_path_at_import() -> None:
    """Importing the module must not consume the env var (no side effects)."""
    assert "PROMPT_PATH" not in prompt_module.__dict__ or prompt_module.PROMPT_PATH == "PROMPT_PATH"


def test_resolver_handles_dotdot(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    env = {"PROMPT_PATH": "../escape.md"}
    result = resolve_effective_prompt_path(tmp_path, env=env)
    assert result == (tmp_path.parent / "escape.md").resolve()


def test_engine_does_not_modify_prompt_md_under_pro_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator-visible PROMPT.md MUST be untouched by ``_sync_product_criteria_file``.

    Sets up a workspace with a known PROMPT.md, freezes its stat and
    contents, runs the engine's prompt-sync helper, and asserts the
    PROMPT.md bytes are byte-for-byte unchanged while the engine-owned
    ``.agent/PRODUCT_CRITERIA.md`` IS updated to reflect the prompt.
    """
    workspace_root = tmp_path
    prompt_text = "# Operator prompt\nDo not touch this.\n"
    prompt_path = workspace_root / "PROMPT.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    # Freeze the file: chmod to read-only so the engine cannot rewrite
    # it. (Read-only bit also reminds the reader of the contract: the
    # engine must never write to PROMPT.md.)
    prompt_path.chmod(stat_mod.S_IRUSR | stat_mod.S_IRGRP | stat_mod.S_IROTH)
    before_mtime_ns = prompt_path.stat().st_mtime_ns
    before_text = prompt_path.read_text(encoding="utf-8")

    try:
        result_path = _sync_product_criteria_file(
            workspace_root=workspace_root,
            default_product_criteria=None,
        )
        assert result_path == workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
        assert result_path.read_text(encoding="utf-8") == prompt_text, (
            "engine must mirror PROMPT.md into PRODUCT_CRITERIA.md"
        )

        after_mtime_ns = prompt_path.stat().st_mtime_ns
        after_text = prompt_path.read_text(encoding="utf-8")
        assert before_mtime_ns == after_mtime_ns, (
            "PROMPT.md mtime changed; engine must not touch the operator-visible file"
        )
        assert before_text == after_text, (
            "PROMPT.md content changed; engine must not touch the operator-visible file"
        )
        # Also assert no engine-owned materialised file was written into the workspace root
        assert not (workspace_root / "PROMPT.md.bak").exists()
        assert not (workspace_root / "PROMPT.md.tmp").exists()
    finally:
        # Restore writability so the test runner can clean up tmp_path
        prompt_path.chmod(stat_mod.S_IRUSR | stat_mod.S_IWUSR)


def test_resolver_uses_os_environ_when_env_arg_omitted(tmp_path: Path) -> None:
    """When no env mapping is passed, the resolver consults ``os.environ``."""
    os.environ["PROMPT_PATH"] = "/tmp/from_os_environ.md"
    try:
        result = resolve_effective_prompt_path(tmp_path)
        assert result == Path("/tmp/from_os_environ.md").resolve()
    finally:
        del os.environ["PROMPT_PATH"]


def test_engine_sync_uses_resolver_under_pro_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With ``PROMPT_PATH`` set, ``_sync_product_criteria_file`` reads the resolved path.

    The test creates a prompt at a non-default location, sets
    ``PROMPT_PATH`` to that location, runs the engine's sync helper, and
    asserts the engine-owned ``.agent/PRODUCT_CRITERIA.md`` ends up
    matching the contents of the resolved prompt.
    """
    workspace_root = tmp_path
    target = workspace_root / "custom" / "goal.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Custom prompt\n", encoding="utf-8")

    monkeypatch.setenv("PROMPT_PATH", str(target))

    result_path = _sync_product_criteria_file(
        workspace_root=workspace_root,
        default_product_criteria=None,
    )
    assert result_path == workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    assert result_path.read_text(encoding="utf-8") == "# Custom prompt\n", (
        "engine must read the PROMPT_PATH-resolved file, not <workspace>/PROMPT.md"
    )
