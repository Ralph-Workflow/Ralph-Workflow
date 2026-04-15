"""Helpers for replacing oversized prompt payloads with file references."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

MAX_INLINE_PROMPT_BYTES = 100 * 1024

type PromptPayloadWriter = Callable[[str, str], str]


def build_prompt_payload_variables(
    values: Mapping[str, str],
    *,
    prompt_name_prefix: str,
    write_payload: PromptPayloadWriter,
) -> dict[str, str]:
    """Return template variables with oversized values replaced by file references."""

    variables: dict[str, str] = {}
    for name, content in values.items():
        if len(content.encode("utf-8")) > MAX_INLINE_PROMPT_BYTES:
            relative_path = prompt_payload_relative_path(prompt_name_prefix, name)
            variables[name] = ""
            variables[f"{name}_PATH"] = write_payload(relative_path, content)
            continue

        variables[name] = content
        variables[f"{name}_PATH"] = ""
    return variables


def prompt_payload_relative_path(prompt_name_prefix: str, variable_name: str) -> str:
    normalized_prefix = _normalize_segment(prompt_name_prefix)
    normalized_name = _normalize_segment(variable_name)
    return f".agent/tmp/prompt_payloads/{normalized_prefix}_{normalized_name}.txt"


def write_payload_to_directory(output_dir: Path, relative_path: str, content: str) -> str:
    destination = output_dir / Path(relative_path).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return str(destination)


def _normalize_segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "prompt"
