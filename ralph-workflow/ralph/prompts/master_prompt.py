
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.prompts._mcp_dispatcher_hint import agy_dispatcher_hint_text
from ralph.prompts.template_registry import _packaged_template_cache, packaged_template_root

if TYPE_CHECKING:
    from collections.abc import Callable

    from pathlib import Path


def _write_master_prompt_file(
    master_prompt_path: Path,
    content: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    write_text_if_changed(
        backend,
        master_prompt_path,
        content,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(master_prompt_path.parent, parents=True, exist_ok=True),
    )


def materialize_master_prompt(
    *,
    workspace_root: Path,
    name: str,
    default_product_criteria: str | None = None,
    worker_namespace: Path | None = None,
    planning_style: bool = False,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    transport: AgentTransport | None = None,
) -> str:
    product_criteria_path = _sync_product_criteria_file(
        workspace_root=workspace_root,
        default_product_criteria=default_product_criteria,
        worker_namespace=worker_namespace,
        backend=backend,
    )
    master_prompt_path = (
        worker_master_prompt_path(worker_namespace, name)
        if worker_namespace is not None
        else workspace_root / ".agent" / "tmp" / f"{name}_master_prompt.md"
    )
    current_plan_path = _current_plan_handoff_path(
        workspace_root,
        planning_style=planning_style,
    )
    _write_master_prompt_file(
        master_prompt_path,
        build_master_prompt(
            planning_style=planning_style,
            master_prompt_path=str(master_prompt_path),
            product_criteria_path=str(product_criteria_path),
            current_plan_path=str(current_plan_path) if current_plan_path is not None else None,
            transport=transport,
        ),
        backend=backend,
    )
    return str(master_prompt_path)


def _sync_product_criteria_file(
    *,
    workspace_root: Path,
    default_product_criteria: str | None,
    worker_namespace: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path:
    product_criteria_path = (
        worker_product_criteria_path(worker_namespace)
        if worker_namespace is not None
        else workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    )
    source_prompt_path = resolve_effective_prompt_path(workspace_root, os.environ)
    if source_prompt_path.exists():
        def _stat(path: Path) -> tuple[int, int] | None:
            if not path.exists():
                return None
            stat = path.stat()
            return (stat.st_size, stat.st_mtime_ns)

        changed, prompt_text = _prompt_files_differ(
            source_prompt_path,
            product_criteria_path,
            stat_fn=_stat,
            read_source=lambda p: p.read_text(encoding="utf-8"),
            read_current=lambda p: p.read_text(encoding="utf-8"),
            trust_equal_metadata=False,
        )
        if changed and prompt_text is not None:
            write_text_if_changed(
                backend,
                product_criteria_path,
                prompt_text,
                encoding="utf-8",
                prepare_write=lambda: backend.mkdir(
                    product_criteria_path.parent, parents=True, exist_ok=True
                ),
            )
            if worker_namespace is None:
                _write_prompt_history_snapshot(
                    workspace_root=workspace_root,
                    prompt_text=prompt_text,
                    backend=backend,
                )
        return product_criteria_path
    if not backend.exists(product_criteria_path) and default_product_criteria is not None:
        write_text_if_changed(
            backend,
            product_criteria_path,
            default_product_criteria,
            encoding="utf-8",
            prepare_write=lambda: backend.mkdir(
                product_criteria_path.parent, parents=True, exist_ok=True
            ),
        )
        if worker_namespace is None:
            _write_prompt_history_snapshot(
                workspace_root=workspace_root,
                prompt_text=default_product_criteria,
                backend=backend,
            )
    return product_criteria_path


def worker_product_criteria_path(worker_namespace: Path) -> Path:
    return worker_namespace / "tmp" / "PRODUCT_CRITERIA.md"


def worker_master_prompt_path(worker_namespace: Path, phase: str) -> Path:
    normalized = phase.replace("/", "_").replace(" ", "_")
    return worker_namespace / "tmp" / f"{normalized}_master_prompt.md"


def _write_prompt_history_snapshot(
    *,
    workspace_root: Path,
    prompt_text: str,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    history_dir = workspace_root / ".agent" / "prompt_history"
    history_path = history_dir / f"PROMPT_{_history_timestamp()}.md"
    backend.mkdir(history_dir, parents=True, exist_ok=True)
    write_text_if_changed(backend, history_path, prompt_text, encoding="utf-8")


def _history_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _current_plan_handoff_path(
    workspace_root: Path,
    *,
    planning_style: bool,
) -> Path | None:
    if planning_style:
        return None
    plan_path = workspace_root / ".agent" / "PLAN.md"
    return plan_path if plan_path.exists() else None


def build_master_prompt(
    *,
    planning_style: bool,
    master_prompt_path: str,
    product_criteria_path: str,
    current_plan_path: str | None = None,
    transport: AgentTransport | None = None,
) -> str:
    unattended = _unattended_mode_text().strip()
    preamble = (
        "This is the session's master prompt. Its instructions remain binding for the "
        "entire session.\n\n"
        "The durable copy of this master prompt is at:\n"
        f"`{master_prompt_path}`\n\n"
        "Read it now; after any context compaction, resume, or continuation, re-read "
        f"`{master_prompt_path}` and the files it references before doing anything else.\n"
    )
    exec_guidance = f"\n\n{exec_job_guidance_text()}"
    dispatcher_hint = (
        f"\n\n{agy_dispatcher_hint_text('ralph_submit_md_artifact')}"
        if transport is AgentTransport.AGY
        else ""
    )
    if planning_style:
        return (
            f"{preamble}\n"
            f"{unattended}\n\n"
            "The task request (product criteria) is a DIFFERENT document, held in this file:\n"
            f"`{product_criteria_path}`\n\n"
            "Treat that file as the source of truth for the current goal.\n"
            "Do not ask the user to restate it.\n"
            f"{exec_guidance}"
            f"{dispatcher_hint}"
        )

    plan_guidance = ""
    override_scope = "this master prompt"
    if current_plan_path is not None:
        plan_guidance = (
            "The canonical plan is the source of truth for the current goal and "
            "execution steps:\n"
            f"`{current_plan_path}`\n\n"
        )
        override_scope = "the plan or this master prompt"
    return (
        f"{preamble}\n"
        f"{unattended}\n\n"
        f"{plan_guidance}"
        "The product criteria / task request is a DIFFERENT document, held in this file:\n"
        f"`{product_criteria_path}`\n\n"
        "Treat that file as background product criteria only — do not let it override "
        f"{override_scope}.\n"
        "Do not ask the user to restate it.\n"
        f"{exec_guidance}"
        f"{dispatcher_hint}"
    )


def exec_job_guidance_text() -> str:
    return (
        "EXEC JOB FLOW:\n"
        "Commands in `exec` / `unsafe_exec` running past the 45s soft reply budget detach and return "
        "a `job_id`. When a command returns a `job_id`, do not re-run it: call `exec_wait` to monitor "
        "or `exec_cancel` to abort. `timeout_ms` sets the inactivity stall window, not total duration; "
        "use `max_runtime_ms` for overall execution duration caps."
    )


def _unattended_mode_text() -> str:
    return _packaged_template_cache.get(
        "shared/_unattended_mode.jinja", root=packaged_template_root()
    )


def _prompt_files_differ(
    source: Path,
    current: Path,
    *,
    stat_fn: Callable[[Path], tuple[int, int] | None],
    read_source: Callable[[Path], str] | None = None,
    read_current: Callable[[Path], str] | None = None,
    trust_equal_metadata: bool = True,
) -> tuple[bool, str | None]:

    def _default_read(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    read_source_fn = read_source if read_source is not None else _default_read
    read_current_fn = read_current if read_current is not None else _default_read

    source_stat = stat_fn(source)
    current_stat = stat_fn(current)
    if source_stat is None:
        return False, None
    if current_stat is None:
        return True, read_source_fn(source)
    src_size, src_mtime = source_stat
    cur_size, cur_mtime = current_stat
    if src_size == cur_size and src_mtime == cur_mtime and trust_equal_metadata:
        return False, None
    if src_size != cur_size:
        return True, (read_source_fn(source) if read_source is not None else None)
    src_text = read_source_fn(source)
    cur_text = read_current_fn(current)
    return src_text != cur_text, src_text
