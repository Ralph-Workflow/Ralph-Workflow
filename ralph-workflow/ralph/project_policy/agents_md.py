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


def _managed_block() -> str:
    """Return the managed AGENTS.md instruction block content."""
    return _AGENTS_PLACEHOLDER_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )


def _has_managed_block(content: str) -> bool:
    """Return True when ``content`` already contains the managed block."""
    return markers.AGENTS_BLOCK_BEGIN in content and markers.AGENTS_BLOCK_END in content


def _bootstrap_agents_md(workspace: Workspace) -> list[str]:
    """Create or update AGENTS.md; return the changed-file list."""
    if not workspace.exists(markers.AGENTS_MD):
        workspace.write(markers.AGENTS_MD, _managed_block().rstrip() + "\n")
        return [markers.AGENTS_MD]
    content = workspace.read(markers.AGENTS_MD)
    if _has_managed_block(content):
        return []  # idempotent: block already present, no-op.
    if not content.endswith("\n"):
        content = content + "\n"
    workspace.write(
        markers.AGENTS_MD,
        content + "\n" + _managed_block(),
    )
    return [markers.AGENTS_MD]


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
    "is_opted_out",
]
