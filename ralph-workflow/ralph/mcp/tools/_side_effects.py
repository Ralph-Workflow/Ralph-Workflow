"""Side-effect contract for Ralph MCP tools.

A tool call that is retried after a committed-then-failed response has
explicitly defined side-effect semantics. A command that may have partially
executed before a stream failed is surfaced as ``partial: True`` and the
recovery controller refuses to re-execute when ``outcome == 'partial'``.

The classification here is the single source of truth for the retry safety
contract. It is a closed registry — every tool in the built registry must
have a classification, and a new tool without a classification fails the
default-deny test (tests/test_property_f_retry_side_effects.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SideEffectClassification = Literal["read", "mutate", "unknown"]


@dataclass(frozen=True)
class SideEffectContract:
    """Side-effect contract for a tool.

    Attributes:
        tool_name: The tool's public name (e.g. ``"exec"``).
        classification: ``"read"`` for idempotent reads, ``"mutate"`` for
            commands that may change system state, ``"unknown"`` if the
            classification is not pinned.
        idempotent: True when the tool is safe to re-execute after a
            partial failure. Reads are idempotent. Mutates are not.
    """

    tool_name: str
    classification: SideEffectClassification
    idempotent: bool


def _contract(name: str, classification: SideEffectClassification) -> SideEffectContract:
    return SideEffectContract(
        tool_name=name,
        classification=classification,
        idempotent=(classification == "read"),
    )


#: Static side-effect registry. Every tool listed in
#: ``RalphToolName`` MUST appear here — the default-deny test walks the
#: live enum and asserts no tool is missing a contract.
#: This is the closed set; adding a new tool without a contract is a
#: test failure (a property the recovery controller relies on).
REGISTRY: dict[str, SideEffectContract] = {
    # Mutating tools — exec family and any web/visit tool that may set cookies,
    # file writes, git writes, plan edits, and artifact submission.
    "exec": _contract("exec", "mutate"),
    "unsafe_exec": _contract("unsafe_exec", "mutate"),
    "raw_exec": _contract("raw_exec", "mutate"),
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
    "ralph_submit_artifact": _contract("ralph_submit_artifact", "mutate"),
    "ralph_submit_plan_section": _contract("ralph_submit_plan_section", "mutate"),
    "ralph_insert_plan_step": _contract("ralph_insert_plan_step", "mutate"),
    "ralph_replace_plan_step": _contract("ralph_replace_plan_step", "mutate"),
    "ralph_remove_plan_step": _contract("ralph_remove_plan_step", "mutate"),
    "ralph_finalize_plan": _contract("ralph_finalize_plan", "mutate"),
    "ralph_discard_plan_draft": _contract("ralph_discard_plan_draft", "mutate"),
    "coordinate": _contract("coordinate", "mutate"),
    "declare_complete": _contract("declare_complete", "mutate"),
    "report_progress": _contract("report_progress", "mutate"),
    "read_env": _contract("read_env", "read"),
    # Read tools — safe to retry on partial failure.
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
    "ralph_get_plan_draft": _contract("ralph_get_plan_draft", "read"),
    "read_image": _contract("read_image", "read"),
    "read_media": _contract("read_media", "read"),
}


def get_contract(tool_name: str) -> SideEffectContract:
    """Return the side-effect contract for a tool, or an unknown contract.

    A new tool without a classification is treated as ``unknown`` and
    non-idempotent — the safe default. The default-deny test fails on any
    unclassified tool that actually appears in the registry, so this
    fallback is only reached when a caller passes a tool name not in the
    registry (i.e. a typo).
    """
    return REGISTRY.get(tool_name, SideEffectContract(tool_name, "unknown", False))


def register(tool_name: str, classification: SideEffectClassification) -> SideEffectContract:
    """Register a new contract (test-only convenience; production uses REGISTRY)."""
    REGISTRY[tool_name] = _contract(tool_name, classification)
    return REGISTRY[tool_name]


__all__ = [
    "REGISTRY",
    "SideEffectClassification",
    "SideEffectContract",
    "get_contract",
    "register",
]
