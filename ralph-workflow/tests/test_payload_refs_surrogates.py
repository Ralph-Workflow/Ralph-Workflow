"""Tests for surrogate-safe payload reference handling."""

from __future__ import annotations

from pathlib import Path

from ralph.prompts.payload_refs import (
    MAX_INLINE_PROMPT_BYTES,
    build_prompt_payload_variables,
    sanitize_surrogates,
    write_payload_to_directory,
)

SURROGATE = "\udca4"  # PEP 383 representation of raw byte 0xA4


def test_sanitize_surrogates_removes_lone_surrogate() -> None:
    sanitized = sanitize_surrogates(f"prefix {SURROGATE} suffix")
    assert SURROGATE not in sanitized
    sanitized.encode("utf-8")  # must not raise


def test_sanitize_surrogates_preserves_normal_text() -> None:
    text = "diff --git a/x.py b/x.py\n+hello\n"
    assert sanitize_surrogates(text) == text


def test_build_prompt_payload_variables_inlines_surrogate_text(tmp_path: Path) -> None:
    diff = f"line1\n{SURROGATE}\nline3"

    variables = build_prompt_payload_variables(
        {"DIFF": diff},
        prompt_name_prefix="development_commit",
        write_payload=lambda relative_path, content: str(tmp_path / Path(relative_path).name),
    )

    assert SURROGATE not in variables["DIFF"]
    assert variables["DIFF_PATH"] == ""
    variables["DIFF"].encode("utf-8")  # must not raise


def test_build_prompt_payload_variables_writes_file_for_oversized_surrogate_text(
    tmp_path: Path,
) -> None:
    diff = SURROGATE + ("x" * (MAX_INLINE_PROMPT_BYTES + 1))
    captured: dict[str, str] = {}

    def writer(relative_path: str, content: str) -> str:
        captured["path"] = relative_path
        captured["content"] = content
        return relative_path

    variables = build_prompt_payload_variables(
        {"DIFF": diff},
        prompt_name_prefix="development_commit",
        write_payload=writer,
    )

    assert variables["DIFF"] == ""
    assert variables["DIFF_PATH"] == captured["path"]
    assert SURROGATE not in captured["content"]
    captured["content"].encode("utf-8")  # must not raise


def test_write_payload_to_directory_handles_surrogates(tmp_path: Path) -> None:
    relative = ".agent/tmp/prompt_payloads/dev_commit_diff.txt"
    content = f"prefix\n{SURROGATE}\nsuffix"

    written_path = write_payload_to_directory(tmp_path, relative, content)

    written = Path(written_path).read_text(encoding="utf-8")
    assert SURROGATE not in written
    assert "prefix" in written
    assert "suffix" in written
