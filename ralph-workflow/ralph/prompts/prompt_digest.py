from __future__ import annotations

import hashlib
import json
import os
from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pro_support.prompt import resolve_effective_prompt_path

if TYPE_CHECKING:
    from pathlib import Path

PROMPT_DIGEST_FILENAME = "prompt_digest.json"
_DIGEST_KEY = "prompt_sha256"
_READ_ERRORS = (OSError, UnicodeDecodeError, ValueError)


class PromptCopyReconciliation(StrEnum):
    UNCHANGED = "unchanged"
    PROMPT_CHANGED = "prompt_changed"
    COPY_RESTORED = "copy_restored"
    DIGEST_RECORDED = "digest_recorded"


def prompt_digest_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / PROMPT_DIGEST_FILENAME


def materialized_prompt_copy_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "PRODUCT_CRITERIA.md"


def prompt_text_digest(prompt_text: str) -> str:
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def _serialized_digest(digest: str) -> str:
    payload: dict[str, str] = {_DIGEST_KEY: digest}
    return json.dumps(payload, sort_keys=True) + "\n"


def record_prompt_digest(
    workspace_root: Path,
    prompt_text: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    path = prompt_digest_path(workspace_root)
    write_text_if_changed(
        backend,
        path,
        _serialized_digest(prompt_text_digest(prompt_text)),
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(path.parent, parents=True, exist_ok=True),
    )


def recorded_prompt_digest(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    path = prompt_digest_path(workspace_root)
    try:
        if not backend.exists(path):
            return None
        payload: object = json.loads(backend.read_text(path, encoding="utf-8"))
    except _READ_ERRORS:
        return None
    if not isinstance(payload, dict):
        return None
    digest: object = payload.get(_DIGEST_KEY)
    return digest if isinstance(digest, str) and digest else None


def _current_prompt_text(workspace_root: Path, backend: FileBackend) -> str | None:
    prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    try:
        if not backend.exists(prompt_path):
            return None
        return backend.read_text(prompt_path, encoding="utf-8")
    except _READ_ERRORS:
        return None


def _prompt_differs_from_record(recorded: str | None, prompt_text: str) -> bool:
    return recorded is not None and recorded != prompt_text_digest(prompt_text)


def prompt_changed_since_last_materialization(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> bool:
    prompt_text = _current_prompt_text(workspace_root, backend)
    if prompt_text is None:
        return False
    return _prompt_differs_from_record(
        recorded_prompt_digest(workspace_root, backend=backend), prompt_text
    )


def _copy_text(copy_path: Path, backend: FileBackend) -> str | None:
    try:
        return backend.read_text(copy_path, encoding="utf-8")
    except _READ_ERRORS:
        return None


def reconcile_materialized_prompt(
    workspace_root: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> PromptCopyReconciliation:
    prompt_text = _current_prompt_text(workspace_root, backend)
    copy_path = materialized_prompt_copy_path(workspace_root)
    if prompt_text is None or not backend.exists(copy_path):
        return PromptCopyReconciliation.UNCHANGED
    recorded = recorded_prompt_digest(workspace_root, backend=backend)
    if _prompt_differs_from_record(recorded, prompt_text):
        return PromptCopyReconciliation.PROMPT_CHANGED
    if _copy_text(copy_path, backend) != prompt_text:
        write_text_if_changed(backend, copy_path, prompt_text, encoding="utf-8")
        record_prompt_digest(workspace_root, prompt_text, backend=backend)
        return PromptCopyReconciliation.COPY_RESTORED
    if recorded is None:
        record_prompt_digest(workspace_root, prompt_text, backend=backend)
        return PromptCopyReconciliation.DIGEST_RECORDED
    return PromptCopyReconciliation.UNCHANGED


__all__ = [
    "PROMPT_DIGEST_FILENAME",
    "PromptCopyReconciliation",
    "materialized_prompt_copy_path",
    "prompt_changed_since_last_materialization",
    "prompt_digest_path",
    "prompt_text_digest",
    "reconcile_materialized_prompt",
    "record_prompt_digest",
    "recorded_prompt_digest",
]
