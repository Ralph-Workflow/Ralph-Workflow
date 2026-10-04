"""Schema-upgrade resolution for the project policy preflight.

The project policy preflight may encounter older copies of the
canonical policy files whose schema marker pre-dates the current
schema. The user is asked exactly ONCE -- not once per file -- to
either upgrade or freeze every such file. ``_maybe_resolve_schema_upgrade``
exposes that consent gate; ``_freeze_policy_files`` performs the
freeze pinning.

Lives in its own module so :mod:`ralph.project_policy.cli_integration`
stays under the 1000-line repository cap.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from ralph.project_policy import _prompt_ui
from ralph.project_policy import markers as policy_markers

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.workspace.protocol import Workspace

    from .cli_integration import EmitFn


#: Stable choice keys. Control flow branches on these, never on the copy.
_CHOICE_UPGRADE: str = "upgrade"
_CHOICE_FREEZE: str = "freeze"
_CHOICE_EXPLAIN: str = "explain"


#: Explanation shown (via the info panel) before the upgrade-or-freeze menu.
_SCHEMA_EXPLAIN: str = (
    "The schema is the structure Ralph Workflow's deterministic validator "
    "reads: the markers, the declared gate commands, and the placeholders "
    "that must be resolved before a policy counts as complete. When the "
    "schema moves, files written against the old one can no longer be fully "
    "validated.\n\n"
    "Upgrading hands each file to an agent, which rewrites it into the "
    "current structure and carries your project-specific rules across. You "
    "review the result like any other change -- it lands in your working "
    "tree.\n\n"
    "Freezing leaves the files exactly as they are. They keep working, but "
    "Ralph Workflow stops offering to bring them forward, and later versions "
    "will validate less of them."
)


def _schema_choices(count: int) -> tuple[_prompt_ui.PromptChoice, ...]:
    """Build the upgrade-or-freeze menu for ``count`` outdated policy files."""
    files = "file" if count == 1 else "files"
    return (
        _prompt_ui.PromptChoice(
            key=_CHOICE_UPGRADE,
            title=f"Upgrade all {count} {files} to {policy_markers.SCHEMA_VERSION}",
            description="An agent rewrites them; your rules carry across.",
        ),
        _prompt_ui.PromptChoice(
            key=_CHOICE_FREEZE,
            title=f"Keep all {count} {files} on their current schema",
            description="Left as they are. Ralph Workflow will not re-ask.",
        ),
        _prompt_ui.PromptChoice(
            key=_CHOICE_EXPLAIN,
            title="What does upgrading involve?",
            description="Explains both choices, then asks again.",
        ),
    )


def _maybe_resolve_schema_upgrade(
    workspace: Workspace,
    emit: EmitFn,
    *,
    select: _prompt_ui.SelectFn | None,
    is_tty: Callable[[], bool] | None,
) -> bool:
    """Offer a single all-or-nothing upgrade-or-freeze choice for older copies."""
    paths = [
        f"{policy_markers.CANONICAL_DIR}{name}"
        for name in (
            *policy_markers.CORE_POLICY_FILES,
            *policy_markers.CONDITIONAL_POLICY_FILES.values(),
        )
        if workspace.exists(f"{policy_markers.CANONICAL_DIR}{name}")
    ]
    outdated: list[tuple[str, str, int]] = []
    invalid_schema = False
    current_version = int(policy_markers.SCHEMA_VERSION.removeprefix("v"))
    for path in paths:
        lines = workspace.read(path).splitlines()
        first_line = next((line for line in lines if line.strip()), "")
        if first_line == policy_markers.POLICY_SCHEMA_MARKER:
            continue
        freeze_match = re.fullmatch(r"<!-- ralph-policy-schema: freeze v([0-9]+) -->", first_line)
        if freeze_match is not None:
            frozen_version = int(freeze_match.group(1))
            if frozen_version < current_version:
                continue
            emit(
                f"Policy {path} has invalid freeze schema v{frozen_version}; "
                f"a freeze must be older than {policy_markers.SCHEMA_VERSION}."
            )
            invalid_schema = True
            break
        match = re.fullmatch(r"<!-- ralph-policy-schema: v([0-9]+) -->", first_line)
        if match is None:
            emit(f"Policy schema marker is missing or malformed in {path}.")
            invalid_schema = True
            break
        installed_version = int(match.group(1))
        if installed_version > current_version:
            emit(
                f"Policy {path} uses future schema v{installed_version}; "
                f"this Ralph version supports {policy_markers.SCHEMA_VERSION}."
            )
            invalid_schema = True
            break
        outdated.append((path, first_line, installed_version))
    if invalid_schema:
        return False
    if not outdated:
        return True
    tty_check = is_tty if is_tty is not None else lambda: False
    if not tty_check():
        emit(
            "Policy schema choice required; rerun interactively to upgrade "
            "or freeze the customized policy file(s)."
        )
        return False
    file_list = "\n".join(
        f"  \u2022 {path}  (currently v{version})" for path, _marker, version in outdated
    )
    emit(
        f"Ralph Workflow's policy schema {policy_markers.SCHEMA_VERSION} is "
        f"available. {len(outdated)} policy file(s) you have customized are "
        f"still on an older schema:\n{file_list}\n\n"
        "Your choices:\n\n"
        "  \u2022 Upgrade them. An agent rewrites each file into the current "
        "schema, carrying your project-specific rules across, and the result "
        "lands in your working tree for you to review. This adds agent work "
        "to the start of this run.\n"
        "  \u2022 Keep them on their current schema. The files are frozen exactly "
        "as they are and Ralph Workflow stops offering to bring them forward. "
        "Reversible later by deleting the `freeze` line at the top of a file."
    )
    select_fn = select if select is not None else _prompt_ui.select
    choices = _schema_choices(len(outdated))
    for _round in range(8):
        choice = _ask(
            select_fn,
            emit,
            "What should Ralph Workflow do with these policy files?",
            choices,
            _CHOICE_UPGRADE,
            fallback_notice=(
                "Policy schema choice could not be completed; no implicit upgrade was applied."
            ),
        )
        if choice == _CHOICE_EXPLAIN:
            emit(_SCHEMA_EXPLAIN)
            continue
        if choice == _CHOICE_UPGRADE:
            return True
        _freeze_policy_files(workspace, emit, outdated)
        return True
    return True


def _ask(
    select: _prompt_ui.SelectFn,
    emit: EmitFn,
    question: str,
    choices: Sequence[_prompt_ui.PromptChoice],
    default: str,
    *,
    fallback_notice: str,
) -> str:
    """Ask one menu, returning ``default`` if the seam itself blows up."""
    try:
        return select(question, choices, default)
    except Exception as exc:
        logger.debug("policy prompt failed (non-fatal): {}", exc)
        emit(fallback_notice)
        return default


def _freeze_policy_files(
    workspace: Workspace,
    emit: EmitFn,
    outdated: Sequence[tuple[str, str, int]],
) -> None:
    """Pin every outdated policy file at its installed schema version."""
    frozen: list[str] = []
    for path, marker, installed_version in outdated:
        content = workspace.read(path)
        workspace.write(
            path,
            content.replace(
                marker,
                f"<!-- ralph-policy-schema: freeze v{installed_version} -->",
                1,
            ),
        )
        frozen.append(path)
    frozen_list = "\n".join(f"  \u2022 {path}" for path in frozen)
    emit(
        f"Froze {len(frozen)} policy file(s) at their current schema \u2014 Ralph "
        f"Workflow will not upgrade them:\n{frozen_list}\n\n"
        "Changed your mind? Remove the skip: delete the "
        "`<!-- ralph-policy-schema: freeze vN -->` line at the top of the file "
        "(or change `freeze vN` back to `vN`) and rerun \u2014 Ralph Workflow will "
        "offer the upgrade again."
    )


__all__ = [
    "_freeze_policy_files",
    "_maybe_resolve_schema_upgrade",
]
