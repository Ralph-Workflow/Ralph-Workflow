from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideEffectClassification = Literal["read", "mutate", "unknown"]


@dataclass(frozen=True)
class SideEffectContract:
    tool_name: str
    classification: SideEffectClassification
    idempotent: bool


def _contract(name: str, classification: SideEffectClassification) -> SideEffectContract:
    return SideEffectContract(
        tool_name=name,
        classification=classification,
        idempotent=(classification == "read"),
    )


REGISTRY: dict[str, SideEffectContract] = {
    "exec": _contract("exec", "mutate"),
    "unsafe_exec": _contract("unsafe_exec", "mutate"),
    "raw_exec": _contract("raw_exec", "mutate"),
    "exec_cancel": _contract("exec_cancel", "mutate"),
    "web_search": _contract("web_search", "mutate"),
    "visit_url": _contract("visit_url", "mutate"),
    "download_url": _contract("download_url", "mutate"),
    "write_file": _contract("write_file", "mutate"),
    "edit_file": _contract("edit_file", "mutate"),
    "append_file": _contract("append_file", "mutate"),
    "create_directory": _contract("create_directory", "mutate"),
    "move_file": _contract("move_file", "mutate"),
    "copy_file": _contract("copy_file", "mutate"),
    "delete_path": _contract("delete_path", "mutate"),
    "ralph_submit_md_artifact": _contract("ralph_submit_md_artifact", "mutate"),
    "ralph_stage_md_artifact": _contract("ralph_stage_md_artifact", "mutate"),
    "ralph_edit_md_artifact": _contract("ralph_edit_md_artifact", "mutate"),
    "ralph_finalize_md_artifact": _contract("ralph_finalize_md_artifact", "mutate"),
    "ralph_discard_md_draft": _contract("ralph_discard_md_draft", "mutate"),
    "coordinate": _contract("coordinate", "mutate"),
    "declare_complete": _contract("declare_complete", "mutate"),
    "report_progress": _contract("report_progress", "mutate"),
    "read_env": _contract("read_env", "read"),
    "exec_wait": _contract("exec_wait", "read"),
    "read_file": _contract("read_file", "read"),
    "read_multiple_files": _contract("read_multiple_files", "read"),
    "list_directory": _contract("list_directory", "read"),
    "list_directory_recursive": _contract("list_directory_recursive", "read"),
    "directory_tree": _contract("directory_tree", "read"),
    "search_files": _contract("search_files", "read"),
    "grep_files": _contract("grep_files", "read"),
    "stat_path": _contract("stat_path", "read"),
    "list_allowed_roots": _contract("list_allowed_roots", "read"),
    "git_status": _contract("git_status", "read"),
    "git_diff": _contract("git_diff", "read"),
    "git_log": _contract("git_log", "read"),
    "git_show": _contract("git_show", "read"),
    "ralph_verify_md_artifact": _contract("ralph_verify_md_artifact", "read"),
    "ralph_get_md_draft": _contract("ralph_get_md_draft", "read"),
    "read_image": _contract("read_image", "read"),
    "read_media": _contract("read_media", "read"),
    "media_capture": _contract("media_capture", "mutate"),
    "ralph_index_status": _contract("ralph_index_status", "read"),
    "ralph_reindex": _contract("ralph_reindex", "read"),
    "ralph_graph": _contract("ralph_graph", "read"),
}


def get_contract(tool_name: str) -> SideEffectContract:
    return REGISTRY.get(tool_name, SideEffectContract(tool_name, "unknown", False))


def register(tool_name: str, classification: SideEffectClassification) -> SideEffectContract:
    REGISTRY[tool_name] = _contract(tool_name, classification)
    return REGISTRY[tool_name]


__all__ = [
    "REGISTRY",
    "SideEffectClassification",
    "SideEffectContract",
    "get_contract",
    "register",
]
