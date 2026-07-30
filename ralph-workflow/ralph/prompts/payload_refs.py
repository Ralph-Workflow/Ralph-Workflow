"""Helpers for replacing oversized prompt payloads with file references."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

MAX_INLINE_PROMPT_BYTES = 100 * 1024

type PromptPayloadWriter = Callable[[str, str], str]


def sanitize_surrogates(text: str) -> str:
    """Replace lone surrogate code points so the result is strictly UTF-8 encodable."""
    pep383_safe = re.sub(r"[\ud800-\udc7f\udd00-\udfff]", "\ufffd", text)
    return pep383_safe.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def build_prompt_payload_variables(
    values: Mapping[str, str],
    *,
    prompt_name_prefix: str,
    write_payload: PromptPayloadWriter,
) -> dict[str, str]:
    """Return template variables with oversized values replaced by file references."""

    variables: dict[str, str] = {}
    for name, content in values.items():
        safe_content = sanitize_surrogates(content)
        if len(safe_content.encode("utf-8")) > MAX_INLINE_PROMPT_BYTES:
            relative_path = prompt_payload_relative_path(prompt_name_prefix, name)
            variables[name] = ""
            variables[f"{name}_PATH"] = write_payload(relative_path, safe_content)
            continue

        variables[name] = safe_content
        variables[f"{name}_PATH"] = ""
    return variables


def prompt_payload_relative_path(prompt_name_prefix: str, variable_name: str) -> str:
    """Return the relative path for a prompt payload file given its prefix and variable name."""
    normalized_prefix = _normalize_segment(prompt_name_prefix)
    normalized_name = _normalize_segment(variable_name)
    return f".agent/tmp/prompt_payloads/{normalized_prefix}_{normalized_name}.txt"


def write_payload_to_directory(
    output_dir: Path,
    relative_path: str,
    content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str:
    """Write payload content to a directory and return the absolute path.

    Routes the physical write through :func:`write_text_if_changed` so
    a byte-identical re-emit of an oversized prompt payload does not
    advance the destination's mtime or generate an additional
    fseventsd notification. The post-condition "file contains
    ``sanitize_surrogates(content)``" always holds: any read
    uncertainty or content mismatch falls through to a real write.

    Args:
        output_dir: Directory under which the payload file is created.
        relative_path: Path whose final segment names the payload file.
        content: Payload text to persist (sanitized via
            :func:`sanitize_surrogates` before being written).
        backend: Filesystem backend used for ``mkdir`` and the
            idempotent text write. Defaults to the real-Path backend;
            tests inject an in-memory counting backend to verify the
            byte-identical re-emit skip.
    """
    destination = output_dir / Path(relative_path).name
    backend.mkdir(destination.parent, parents=True, exist_ok=True)
    write_text_if_changed(backend, destination, sanitize_surrogates(content), encoding="utf-8")
    return str(destination)


def _normalize_segment(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "prompt"
