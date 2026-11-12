from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, NamedTuple

from ralph.project_policy import markers
from ralph.project_policy._findings import PolicyFinding

if TYPE_CHECKING:
    from ralph.project_policy._readiness_assessment import ReadinessAssessment
    from ralph.workspace.protocol import Workspace

_AGENTS_BLOCK_BEGIN_RE: Final[re.Pattern[str]] = re.compile(
    r"<!-- ralph-workflow-policy:begin v\d+ -->"
)

_READINESS_DIMENSIONS_RE: Final[re.Pattern[str]] = re.compile(
    r"structure complete=(?P<structure_complete>[a-z]+), "
    r"implementation verified=(?P<implementation_verified>[a-z]+), "
    r"outcome evidence reviewed=(?P<outcome_evidence_reviewed>[a-z]+)"
)


class _ManagedBlock(NamedTuple):
    begin_marker: str
    begin_index: int
    end_index: int
    body: str


_AGENTS_PLACEHOLDER_TEMPLATE = """{begin}
This project participates in Ralph Workflow's project-policy-readiness
preflight. Before ordinary planning or development begins, the canonical
quality-policy files under {canonical_dir} must exist, be customized for
this project's languages, frameworks, and commands, and pass the
deterministic validator (see marker contract below). All agent roles MUST
load policies in this order: execution-kernel.md first, then their role slice,
then any triggered domain policy before changing the project. Use the effective bounded policy
portfolio: load its invariant kernel, selected profiles, local tightening, and
valid exceptions instead of applying every conditional domain.

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

Dead code is prohibited; remove it — rewriting later is better than retaining it.

The remediation agent MUST NOT mark any policy complete while any RALPH-FACT
placeholder token, RALPH-COMMAND without a real value, or unresolved
RALPH-LANG coverage remains.

The readiness preflight is byte-exact deterministic; near-miss prose, extra
whitespace, or case changes do not satisfy any requirement.
{end}
"""

_PLACEHOLDER_SENTINEL = "The remediation agent MUST, in order:"

_RALPH_MANAGED_SIGNATURE = "Readiness reports THREE dimensions, not two:"


class _AgentsReadyTemplate(str):
    def format(self, *args: object, **kwargs: object) -> str:
        kwargs.setdefault("structure_complete", "unknown")
        kwargs.setdefault("implementation_verified", "unknown")
        kwargs.setdefault("outcome_evidence_reviewed", "unknown")
        return super().format(*args, **kwargs)


_AGENTS_READY_TEMPLATE = _AgentsReadyTemplate(
    """{begin}
Quality policies live under {canonical_dir}. All agent roles MUST load policies in this order: execution-kernel.md first, then their role slice, then any triggered domain policy before changing the project; load only its effective bounded policy portfolio: kernel, selected profiles, local tightening, and valid exceptions.
Run declared RALPH-COMMAND gates; read {canonical_dir}gate-script-policy.md FIRST when touching a build, gate, gate script, or CI.
Search nearest coverage before a control; record REMOVE, MERGE, REPLACE, or KEEP; use the cheapest sufficient layer and keep the default-gate budget neutral — the testing policy's B5 below-budget browser carve-out (a new browser journey under the measured `browser_budget_seconds` needs no offset elsewhere in the lane) is the only relaxation of the mandatory offset, and the aggregate ceiling plus the distinct-risk requirement still hold.
Give non-default work an owner, trigger, lane, evidence, and review condition. Material claims need a predeclared public-surface observation tied to an immutable subject and run, preserved artifact, validated oracle, and independent verdict.
Telemetry, provenance, private-state inspection, and agent narration are not correctness proof. These are living documents: keep facts current without amending policy intent.
Resolve RALPH-PENDING lines whose review trigger is met; leaving a resolvable item is a violation. Dead code is prohibited; remove it — rewriting later is better than retaining it.
Readiness reports THREE dimensions, not two: structure complete={structure_complete}, implementation verified={implementation_verified}, outcome evidence reviewed={outcome_evidence_reviewed}. A structural READY is the caching gate; structural READY is NOT semantic correctness.
{end}
"""
)

_CLAUDE_MINIMAL_CONTENT = (
    "# CLAUDE.md\n\n"
    "Claude-compatible agents working in this repository MUST follow the\n"
    "instructions in `AGENTS.md`. AGENTS.md is the single source of truth\n"
    "for project quality policy and project-specific agent behaviour.\n"
    "Do not duplicate or contradict its directives here.\n"
)


def is_opted_out(workspace: Workspace) -> bool:
    if not workspace.exists(markers.AGENTS_MD):
        return False
    content = workspace.read(markers.AGENTS_MD)
    return markers.OPT_OUT_MARKER in content


def has_significant_unmanaged_content(workspace: Workspace) -> bool:
    if not workspace.exists(markers.AGENTS_MD):
        return False
    content = workspace.read(markers.AGENTS_MD)
    if _has_any_managed_marker(content) or markers.OPT_OUT_MARKER in content:
        return False
    nonempty_lines = [line for line in content.splitlines() if line.strip()]
    if any(line.lstrip().startswith(markers.SIGNIFICANT_HEADING_PREFIX) for line in nonempty_lines):
        return True
    return len(nonempty_lines) >= markers.SIGNIFICANT_NONEMPTY_LINE_THRESHOLD


def write_opt_out(workspace: Workspace) -> list[str]:
    content = workspace.read(markers.AGENTS_MD) if workspace.exists(markers.AGENTS_MD) else ""
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
    return _AGENTS_PLACEHOLDER_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )


def _extract_readiness_dimensions(body: str) -> tuple[str, str, str]:
    match = _READINESS_DIMENSIONS_RE.search(body)
    if match:
        return (
            match.group("structure_complete"),
            match.group("implementation_verified"),
            match.group("outcome_evidence_reviewed"),
        )
    return ("unknown", "unknown", "unknown")


def _render_ready_block(
    structure_complete: str,
    implementation_verified: str,
    outcome_evidence_reviewed: str,
) -> str:
    return _AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
        structure_complete=structure_complete,
        implementation_verified=implementation_verified,
        outcome_evidence_reviewed=outcome_evidence_reviewed,
    ).strip()


def _locate_managed_block(content: str) -> _ManagedBlock | None:
    begin_matches = list(_AGENTS_BLOCK_BEGIN_RE.finditer(content))
    end_count = content.count(markers.AGENTS_BLOCK_END)
    if len(begin_matches) != 1 or end_count != 1:
        return None
    begin_match = begin_matches[0]
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    if begin_match.start() >= end_idx:
        return None
    body = content[begin_match.end() : end_idx]
    return _ManagedBlock(
        begin_marker=begin_match.group(0),
        begin_index=begin_match.start(),
        end_index=end_idx,
        body=body,
    )


def _has_well_formed_managed_block(content: str) -> bool:
    return _locate_managed_block(content) is not None


def _maybe_migrate_ralph_block(
    workspace: Workspace,
    content: str,
    block: _ManagedBlock,
) -> list[str] | None:
    body = block.body
    is_placeholder = _PLACEHOLDER_SENTINEL in body
    is_ralph_managed = _RALPH_MANAGED_SIGNATURE in body
    if not (is_placeholder or is_ralph_managed):
        return None

    has_loading_order = "execution-kernel.md first" in body
    has_current_marker = block.begin_marker == markers.AGENTS_BLOCK_BEGIN

    if has_loading_order and has_current_marker:
        return None

    if is_placeholder:
        new_block = _managed_block().strip()
        workspace.write(
            markers.AGENTS_MD,
            content[: block.begin_index]
            + new_block
            + content[block.end_index + len(markers.AGENTS_BLOCK_END) :],
        )
        return [markers.AGENTS_MD]

    if is_ralph_managed:
        sc, iv, oer = _extract_readiness_dimensions(body)
        new_block = _render_ready_block(sc, iv, oer)
        workspace.write(
            markers.AGENTS_MD,
            content[: block.begin_index]
            + new_block
            + content[block.end_index + len(markers.AGENTS_BLOCK_END) :],
        )
        return [markers.AGENTS_MD]

    return None


def _bootstrap_agents_md(workspace: Workspace) -> list[str]:
    if not workspace.exists(markers.AGENTS_MD):
        workspace.write(markers.AGENTS_MD, _managed_block().rstrip() + "\n")
        return [markers.AGENTS_MD]
    content = workspace.read(markers.AGENTS_MD)
    block = _locate_managed_block(content)
    if block is not None:
        migrated = _maybe_migrate_ralph_block(workspace, content, block)
        if migrated is not None:
            return migrated
        return []
    if _has_any_managed_marker(content):
        return []
    if not content.endswith("\n"):
        content = content + "\n"
    workspace.write(
        markers.AGENTS_MD,
        content + "\n" + _managed_block(),
    )
    return [markers.AGENTS_MD]


def _has_any_managed_marker(content: str) -> bool:
    return _AGENTS_BLOCK_BEGIN_RE.search(content) is not None or markers.AGENTS_BLOCK_END in content


def _bootstrap_claude_md(workspace: Workspace) -> list[str]:
    if not workspace.exists(markers.CLAUDE_MD):
        workspace.write(markers.CLAUDE_MD, _CLAUDE_MINIMAL_CONTENT)
        return [markers.CLAUDE_MD]
    content = workspace.read(markers.CLAUDE_MD)
    if "AGENTS.md" in content:
        return []
    separator = "" if content.endswith("\n") else "\n"
    workspace.write(
        markers.CLAUDE_MD,
        content + separator + "\n# AGENTS.md\n\nSee `AGENTS.md` for project policy.\n",
    )
    return [markers.CLAUDE_MD]


def condense_placeholder_block(
    workspace: Workspace,
    assessment: ReadinessAssessment | None = None,
) -> list[str]:
    if not workspace.exists(markers.AGENTS_MD):
        return []
    content = workspace.read(markers.AGENTS_MD)
    block = _locate_managed_block(content)
    if block is None:
        return []
    body = block.body
    is_placeholder = _PLACEHOLDER_SENTINEL in body
    is_ralph_managed = _RALPH_MANAGED_SIGNATURE in body
    if not (is_placeholder or is_ralph_managed):
        return []
    if assessment is not None:
        sc = "true" if assessment.structure_complete else "false"
        iv = "true" if assessment.implementation_verified else "false"
        oer = "true" if assessment.outcome_evidence_reviewed else "false"
    else:
        sc, iv, oer = _extract_readiness_dimensions(body)

    has_loading_order = "execution-kernel.md first" in body
    has_current_marker = block.begin_marker == markers.AGENTS_BLOCK_BEGIN

    if (
        has_loading_order
        and has_current_marker
        and f"structure complete={sc}" in body
        and f"implementation verified={iv}" in body
        and f"outcome evidence reviewed={oer}" in body
    ):
        return []

    ready_block = _render_ready_block(sc, iv, oer)
    block_end = block.end_index + len(markers.AGENTS_BLOCK_END)
    workspace.write(
        markers.AGENTS_MD,
        content[: block.begin_index] + ready_block + content[block_end:],
    )
    return [markers.AGENTS_MD]


MANAGED_BLOCK_LINE_BUDGET: Final[int] = markers.MANAGED_BLOCK_LINE_BUDGET

PROJECT_AGENTS_MD_LINE_BUDGET: Final[int] = markers.PROJECT_AGENTS_MD_LINE_BUDGET

_GIT_REFS: Final[frozenset[str]] = frozenset(
    {"main", "master", "origin/main", "origin/master", "HEAD"}
)


def check_agents_md_line_budgets(content: str) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    lines = content.splitlines()
    if len(lines) > PROJECT_AGENTS_MD_LINE_BUDGET:
        findings.append(
            PolicyFinding(
                requirement_id=markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID,
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"AGENTS.md has {len(lines)} lines, exceeding the "
                    f"{PROJECT_AGENTS_MD_LINE_BUDGET}-line budget"
                ),
                required_outcome=(
                    f"trim project AGENTS.md to <= {PROJECT_AGENTS_MD_LINE_BUDGET} lines "
                    "by moving detail to dedicated policy files"
                ),
                blocking=False,
            )
        )
    block = _locate_managed_block(content)
    if block is not None:
        block_content = content[block.begin_index : block.end_index + len(markers.AGENTS_BLOCK_END)]
        block_lines = block_content.splitlines()
        if len(block_lines) > MANAGED_BLOCK_LINE_BUDGET:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID,
                    path=markers.AGENTS_MD,
                    missing_evidence=(
                        f"managed instruction block has {len(block_lines)} lines, exceeding "
                        f"the {MANAGED_BLOCK_LINE_BUDGET}-line budget"
                    ),
                    required_outcome=(
                        f"condense the managed AGENTS.md block to <= {MANAGED_BLOCK_LINE_BUDGET} lines"
                    ),
                    blocking=False,
                )
            )
    return findings


def _check_one_candidate(
    raw: str,
    workspace: Workspace,
    seen: set[str],
    findings: list[PolicyFinding],
) -> None:
    if (
        not raw
        or " " in raw
        or raw.startswith(("-", "#", "http://", "https://", "mailto:"))
        or any(c in raw for c in "<>*|{}[]'\"")
    ):
        return
    if raw.startswith("file://"):
        raw = raw[len("file://") :]
    candidate = raw.split("#")[0].split(":")[0].strip()
    if not candidate or candidate in _GIT_REFS or candidate.startswith("origin/"):
        return
    has_slash = "/" in candidate
    has_ext = any(
        candidate.endswith(ext)
        for ext in (
            ".md",
            ".py",
            ".toml",
            ".json",
            ".yml",
            ".yaml",
            ".sh",
            ".txt",
            ".rst",
            ".ini",
            ".cfg",
            ".lock",
        )
    )
    if not (has_slash or has_ext):
        return
    if candidate in seen:
        return
    seen.add(candidate)

    clean = candidate.rstrip("/")
    exists = (
        workspace.exists(clean)
        or workspace.exists(candidate)
        or workspace.exists(f"ralph-workflow/{clean}")
    )
    if not exists:
        findings.append(
            PolicyFinding(
                requirement_id=markers.AGENTS_MD_CITED_PATH_FINDING_ID,
                path=markers.AGENTS_MD,
                missing_evidence=f"AGENTS.md cites repository path {candidate!r} which does not exist",
                required_outcome=(
                    f"create the cited path {candidate} or remove the reference from AGENTS.md"
                ),
            )
        )


def check_agents_md_path_citations(workspace: Workspace, content: str) -> list[PolicyFinding]:
    block = _locate_managed_block(content)
    if block is not None:
        content = (
            content[: block.begin_index]
            + content[block.end_index + len(markers.AGENTS_BLOCK_END) :]
        )
    findings: list[PolicyFinding] = []
    seen: set[str] = set()
    for line in content.splitlines():
        for link_match in re.finditer(r"\[([^]]*)\]\(([^)]+)\)", line):
            target = link_match.group(2)
            if isinstance(target, str):
                _check_one_candidate(target.strip(), workspace, seen, findings)
        for bt_match in re.finditer(r"`([^`]+)`", line):
            raw = bt_match.group(1)
            if isinstance(raw, str):
                _check_one_candidate(raw.strip(), workspace, seen, findings)
    return findings


def bootstrap(workspace: Workspace) -> list[str]:
    changed = _bootstrap_agents_md(workspace)
    changed.extend(_bootstrap_claude_md(workspace))
    return changed


__all__ = [
    "MANAGED_BLOCK_LINE_BUDGET",
    "PROJECT_AGENTS_MD_LINE_BUDGET",
    "bootstrap",
    "check_agents_md_line_budgets",
    "check_agents_md_path_citations",
    "condense_placeholder_block",
    "has_significant_unmanaged_content",
    "is_opted_out",
    "write_opt_out",
]
