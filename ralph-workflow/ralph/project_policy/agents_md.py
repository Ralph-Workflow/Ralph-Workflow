"""Opt-out detection and idempotent AGENTS.md/CLAUDE.md bootstrap.

All operations act on an injected :class:`~ralph.workspace.protocol.Workspace`.
There is no raw ``pathlib.Path`` I/O so the bootstrap is fully testable
with :class:`~ralph.workspace.memory.MemoryWorkspace`.

Behavior summary:

* :func:`is_opted_out` returns True ONLY when AGENTS.md exists AND its
  contents contain the byte-exact :data:`markers.OPT_OUT_MARKER` substring.
  Missing AGENTS.md returns False; near-miss prose, extra whitespace, or
  any case change returns False.

* :func:`bootstrap` is idempotent:

  * AGENTS.md missing -> create a deterministic minimal placeholder that
    contains the managed block, references :data:`markers.CANONICAL_DIR`,
    and instructs the first remediation agent to inspect the project,
    create and maintain the canonical files under ``docs/ralph-workflow-policy/``,
    migrate existing policies, configure executable gates, require agents
    to run those gates, and replace placeholder state. The placeholder
    MUST NOT claim any tool, command, or safeguard exists.

  * AGENTS.md present WITHOUT the managed block -> append exactly one
    managed block after existing content, preserving all prior bytes
    byte-for-byte.

  * AGENTS.md present WITH the managed block -> no-op.

  * CLAUDE.md missing -> create a minimal file pointing Claude-compatible
    agents to AGENTS.md.

  * CLAUDE.md present -> preserve unrelated content; append a one-line
    AGENTS.md pointer only when no AGENTS.md reference already exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.project_policy import markers

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

# Placeholder AGENTS.md content shipped on a fresh project. It instructs the
# first remediation agent to inspect the project, customize canonical files,
# and reconcile existing policies. It does NOT claim any tool, command, or
# safeguard exists.
_AGENTS_PLACEHOLDER_TEMPLATE = """{begin}
This project participates in Ralph Workflow's project-policy-readiness
preflight. Before ordinary planning or development begins, the canonical
quality-policy files under {canonical_dir} must exist, be customized for
this project's languages, frameworks, and commands, and pass the
deterministic validator (see marker contract below).

The remediation agent MUST, in order:

1. Inspect the project's actual languages, frameworks, package managers,
   test frameworks, and existing CONTRIBUTING/TESTING/DEVELOPMENT docs.
2. Create and maintain the canonical policy files under {canonical_dir} for
   every core policy type listed in the Ralph markers.
3. Customize each policy file with verified project facts (commands,
   owners, supported platforms, exceptions). Replace every starter
   placeholder with verified project evidence.
4. Migrate any existing project policy-like content into the matching
   canonical file, leaving a `ralph-workflow-policy:migrated ->` marker at
   the old location so the validator can clear the RWP-MIGRATE-UNRECONCILED
   finding.
5. Configure executable gates for testing, type checking, linting,
   dependency checks, and verification. Document each gate as a
   RALPH-COMMAND line (or RALPH-INAPPLICABLE with reason) so the validator
   can confirm the gate is real and non-placeholder.
6. Update CLAUDE.md (if present) to point Claude-compatible agents at this
   AGENTS.md.
7. Run every declared verification gate and report the outcome.

The remediation agent MUST NOT mark any policy complete while any RALPH-FACT
placeholder token, RALPH-COMMAND without a real value, or unresolved
RALPH-LANG coverage remains.

The readiness preflight is byte-exact deterministic; near-miss prose, extra
whitespace, or case changes do not satisfy any requirement.
{end}
"""

# Distinctive phrase present ONLY in the bootstrap placeholder body. Used to
# recognize an untouched placeholder so it can be condensed after READY; an
# agent- or user-rewritten block never contains it and is preserved.
_PLACEHOLDER_SENTINEL = "The remediation agent MUST, in order:"

# Concise managed block written once the project is READY. The bootstrap
# placeholder above is TEMPORARY scaffolding for the first remediation agent;
# leaving its long instruction list in AGENTS.md forever would bury the
# user's own content. The condensed block keeps only what every future AI
# agent needs: the pointer to the canonical policy dir and the obligation to
# follow it (which also keeps the validator's canonical-dir-in-block gate
# satisfied).
_AGENTS_READY_TEMPLATE = """{begin}
This project follows Ralph Workflow quality policies. Before making any
change, AI agents MUST read and follow the canonical policy files under
{canonical_dir} and run the RALPH-COMMAND verification gates declared there.
The policies are living documents: keep their facts current as the project
evolves, but never amend one against its stated intent.
{end}
"""

# CLAUDE.md minimal content. Point Claude-compatible agents at AGENTS.md
# without overriding any of its directives.
_CLAUDE_MINIMAL_CONTENT = (
    "# CLAUDE.md\n\n"
    "Claude-compatible agents working in this repository MUST follow the\n"
    "instructions in `AGENTS.md`. AGENTS.md is the single source of truth\n"
    "for project quality policy and project-specific agent behaviour.\n"
    "Do not duplicate or contradict its directives here.\n"
)


def is_opted_out(workspace: Workspace) -> bool:
    """Return True iff AGENTS.md exists and contains the byte-exact opt-out marker."""
    if not workspace.exists(markers.AGENTS_MD):
        return False
    content = workspace.read(markers.AGENTS_MD)
    return markers.OPT_OUT_MARKER in content


def has_significant_unmanaged_content(workspace: Workspace) -> bool:
    """Return True iff AGENTS.md is marker-free and holds significant content.

    Significant means at least one markdown heading line (starting with
    :data:`markers.SIGNIFICANT_HEADING_PREFIX`) or at least
    :data:`markers.SIGNIFICANT_NONEMPTY_LINE_THRESHOLD` non-empty lines.
    Any Ralph marker (managed block or opt-out) means the repository has
    already been bootstrapped or already decided, so the interactive
    skip-inline-policy offer must not fire.
    """
    if not workspace.exists(markers.AGENTS_MD):
        return False
    content = workspace.read(markers.AGENTS_MD)
    if _has_any_managed_marker(content) or markers.OPT_OUT_MARKER in content:
        return False
    nonempty_lines = [line for line in content.splitlines() if line.strip()]
    if any(
        line.lstrip().startswith(markers.SIGNIFICANT_HEADING_PREFIX)
        for line in nonempty_lines
    ):
        return True
    return len(nonempty_lines) >= markers.SIGNIFICANT_NONEMPTY_LINE_THRESHOLD


def write_opt_out(workspace: Workspace) -> list[str]:
    """Append the byte-exact opt-out marker to AGENTS.md.

    Persists the user's "keep my existing policy" choice from the run
    preflight prompt. Prior bytes are preserved; a file already carrying
    the marker is left untouched. Returns the changed-file list
    (``[markers.AGENTS_MD]`` or ``[]``), mirroring :func:`bootstrap`.
    """
    content = (
        workspace.read(markers.AGENTS_MD)
        if workspace.exists(markers.AGENTS_MD)
        else ""
    )
    if markers.OPT_OUT_MARKER in content:
        return []
    if content and not content.endswith("\n"):
        content += "\n"
    separator = "\n" if content else ""
    workspace.write(
        markers.AGENTS_MD,
        content + separator + markers.OPT_OUT_MARKER + "\n",
    )
    return [markers.AGENTS_MD]


def _managed_block() -> str:
    """Return the managed AGENTS.md instruction block content."""
    return _AGENTS_PLACEHOLDER_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )


def _has_well_formed_managed_block(content: str) -> bool:
    """Return True only when the managed block is structurally complete.

    A well-formed block has exactly one :data:`markers.AGENTS_BLOCK_BEGIN`
    AND exactly one :data:`markers.AGENTS_BLOCK_END`, with the begin
    preceding the end. Partial or duplicate blocks (the malformed states
    the validator rejects) leave the file for deterministic remediation:
    bootstrap MUST NOT append a second begin marker, which would otherwise
    create a duplicate state the validator flags but cannot auto-repair.
    """
    begin_count = content.count(markers.AGENTS_BLOCK_BEGIN)
    end_count = content.count(markers.AGENTS_BLOCK_END)
    if begin_count != 1 or end_count != 1:
        return False
    return content.find(markers.AGENTS_BLOCK_BEGIN) < content.find(
        markers.AGENTS_BLOCK_END
    )


def _bootstrap_agents_md(workspace: Workspace) -> list[str]:
    """Create or update AGENTS.md; return the changed-file list.

    Bootstrap is intentionally conservative on malformed AGENTS.md state:
    partial managed-block markers (begin without end, end without begin),
    duplicate markers, or begin-after-end pairing are left for the
    deterministic remediation agent rather than silently appended-to,
    because appending to those states would create a second begin marker
    the validator would then flag and a Ralph bootstrap would never be
    able to clear. A clean (marker-free) pre-existing AGENTS.md still
    receives an appended managed block, which is the original bootstrap
    contract for first-time projects.
    """
    if not workspace.exists(markers.AGENTS_MD):
        workspace.write(markers.AGENTS_MD, _managed_block().rstrip() + "\n")
        return [markers.AGENTS_MD]
    content = workspace.read(markers.AGENTS_MD)
    if _has_well_formed_managed_block(content):
        return []  # idempotent: block already present, no-op.
    if _has_any_managed_marker(content):
        # Partial/duplicate/misordered managed-block state: do NOT append
        # another block. The deterministic validator emits a stable
        # RWP-MARKER:agents-block-* finding and the remediation agent
        # repairs the file in one change. Appending here would create a
        # second begin marker the validator would also flag, leaving the
        # file in an unfixable state until the agent deletes the duplicate.
        return []
    # Clean AGENTS.md (no managed markers at all): append one managed block
    # so the project enters Ralph's policy-readiness contract.
    if not content.endswith("\n"):
        content = content + "\n"
    workspace.write(
        markers.AGENTS_MD,
        content + "\n" + _managed_block(),
    )
    return [markers.AGENTS_MD]


def _has_any_managed_marker(content: str) -> bool:
    """Return True if the content contains ANY managed block marker.

    A clean (marker-free) AGENTS.md returns False; a file with at least
    one begin or end marker (whether well-formed, partial, duplicate, or
    misordered) returns True. Bootstrap uses this to decide between
    "append one managed block" (clean state) and "leave for remediation"
    (any malformed state).
    """
    return (
        markers.AGENTS_BLOCK_BEGIN in content
        or markers.AGENTS_BLOCK_END in content
    )


def _bootstrap_claude_md(workspace: Workspace) -> list[str]:
    """Create or update CLAUDE.md; return the changed-file list."""
    if not workspace.exists(markers.CLAUDE_MD):
        workspace.write(markers.CLAUDE_MD, _CLAUDE_MINIMAL_CONTENT)
        return [markers.CLAUDE_MD]
    content = workspace.read(markers.CLAUDE_MD)
    # Idempotent: skip if AGENTS.md is already referenced.
    if "AGENTS.md" in content:
        return []
    separator = "" if content.endswith("\n") else "\n"
    workspace.write(markers.CLAUDE_MD, content + separator + "\n# AGENTS.md\n\nSee `AGENTS.md` for project policy.\n")
    return [markers.CLAUDE_MD]


def condense_placeholder_block(workspace: Workspace) -> list[str]:
    """Replace an untouched bootstrap placeholder block with the concise form.

    Called after the project reaches READY. The bootstrap placeholder is
    temporary scaffolding for the first remediation agent; once the
    policies exist and validate, only the short pointer block remains. A
    block whose body was rewritten (no placeholder sentinel), a missing
    AGENTS.md, or a malformed block is left untouched — user- and
    agent-authored content is never clobbered.

    Returns the changed-file list (``[markers.AGENTS_MD]`` or ``[]``).
    """
    if not workspace.exists(markers.AGENTS_MD):
        return []
    content = workspace.read(markers.AGENTS_MD)
    if not _has_well_formed_managed_block(content):
        return []
    begin_idx = content.find(markers.AGENTS_BLOCK_BEGIN)
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    body = content[begin_idx + len(markers.AGENTS_BLOCK_BEGIN) : end_idx]
    if _PLACEHOLDER_SENTINEL not in body:
        return []
    ready_block = _AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    ).strip()
    block_end = end_idx + len(markers.AGENTS_BLOCK_END)
    workspace.write(
        markers.AGENTS_MD,
        content[:begin_idx] + ready_block + content[block_end:],
    )
    return [markers.AGENTS_MD]


def bootstrap(workspace: Workspace) -> list[str]:
    """Idempotently bootstrap AGENTS.md and CLAUDE.md.

    Returns the list of workspace-relative paths that were created or
    modified. Repeated calls never duplicate the managed block or append a
    second pointer to CLAUDE.md.
    """
    changed = _bootstrap_agents_md(workspace)
    changed.extend(_bootstrap_claude_md(workspace))
    return changed


__all__ = [
    "bootstrap",
    "condense_placeholder_block",
    "has_significant_unmanaged_content",
    "is_opted_out",
    "write_opt_out",
]
