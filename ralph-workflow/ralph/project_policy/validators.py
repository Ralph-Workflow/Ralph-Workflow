"""Deterministic project-policy validator.

This module is the ONLY authority on whether a project is policy-ready.
There is no AI, no network, no prose NLP — every check is a structural
pattern match against the canonical file content. Each failure emits a
:class:`~ralph.project_policy.models.PolicyFinding` with a stable
``requirement_id`` (one of the ``ID_*`` constants in
:mod:`ralph.project_policy.markers`), the affected path, the missing
evidence, and the required remediation outcome.

The validator is the consumer of the shared readiness-evidence inventory
in :mod:`ralph.project_policy.evidence`. The inventory enumerates every
file the validator needs to read; the cache hashes the same inventory so
the cache cannot drift from the validator.

A project is ready iff ``validate_readiness`` returns an empty list.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.project_policy import _content_checks, evidence, markers
from ralph.project_policy._content_checks import (
    _check_commands,
    _check_markers,
    _check_per_language_coverage,
    _check_placeholders,
    _check_required_fact_keys,
    _check_required_headings,
    _check_research_basis,
    _check_template_banner,
    _frozen_schema_version,
)
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy._scanners import (
    _HEADING_LINE_RE,
    _check_pending_facts,
    _command_first_token,
    _command_is_approved,
    _headings,
)
from ralph.project_policy.models import PolicyFinding, PortfolioError
from ralph.project_policy.portfolio import parse_portfolio_toml

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace


def _check_agents_md(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the AGENTS.md contract (managed block + canonical-dir reference).

    The managed block contract is unique-count: exactly one
    :data:`markers.AGENTS_BLOCK_BEGIN` AND exactly one
    :data:`markers.AGENTS_BLOCK_END` must appear, the begin must precede the
    end, and the block body must reference :data:`markers.CANONICAL_DIR`. A
    duplicate begin/end, mismatched counts, or a begin-after-end pairing is
    rejected with a stable ``RWP-MARKER:agents-block-*`` finding so the
    remediation agent must reconcile the file before development can begin.
    """
    findings: list[PolicyFinding] = []
    if not workspace.exists(markers.AGENTS_MD):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_AGENTS_MD_MISSING}:missing",
                path=markers.AGENTS_MD,
                missing_evidence="AGENTS.md is missing",
                required_outcome="create AGENTS.md containing the managed Ralph Workflow block",
            )
        ]
    content = workspace.read(markers.AGENTS_MD)
    findings.extend(_check_managed_block_uniqueness(content))
    if not findings:
        # Only check the canonical-dir reference when the managed block is
        # structurally valid (unique begin/end + begin before end). A
        # malformed block already emits its own finding; adding a
        # canonical-dir-ref finding on top of multiple marker findings would
        # bury the actionable defect.
        findings.extend(_check_managed_block_canonical_dir(content))
    return findings


def _check_managed_block_uniqueness(content: str) -> list[PolicyFinding]:
    """Validate the managed block has exactly one begin AND exactly one end.

    Emits a stable RWP-MARKER:agents-block-* finding for every malformed
    case so the user sees precise pointers for duplicates, missing halves,
    or begin-after-end pairing.
    """
    findings: list[PolicyFinding] = []
    begin_count = content.count(markers.AGENTS_BLOCK_BEGIN)
    end_count = content.count(markers.AGENTS_BLOCK_END)
    begin_idx = content.find(markers.AGENTS_BLOCK_BEGIN)
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    if begin_count == 0 and end_count == 0:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:missing",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"missing managed block markers {markers.AGENTS_BLOCK_BEGIN} "
                    f"/ {markers.AGENTS_BLOCK_END}"
                ),
                required_outcome=(
                    "append exactly one managed Ralph Workflow block "
                    "(preserve all existing content)"
                ),
            )
        )
        return findings
    if begin_count == 0 or end_count == 0:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:unmatched",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"unmatched managed block markers "
                    f"(begin={begin_count}, end={end_count}); "
                    "expected exactly one of each"
                ),
                required_outcome=(
                    "remove the partial managed block and append exactly one "
                    f"complete pair ({markers.AGENTS_BLOCK_BEGIN} ... "
                    f"{markers.AGENTS_BLOCK_END}) preserving user content"
                ),
            )
        )
        return findings
    if begin_count > 1 or end_count > 1:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:duplicate",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"duplicate managed block markers "
                    f"(begin={begin_count}, end={end_count}); expected "
                    "exactly one of each"
                ),
                required_outcome=(
                    "consolidate to exactly one managed block: remove all "
                    "duplicate Ralph-managed begin/end markers while "
                    "preserving any user-authored content outside the "
                    "managed block"
                ),
            )
        )
        return findings
    if begin_idx > end_idx:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:misordered",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"managed block end marker appears before begin marker; "
                    f"expected {markers.AGENTS_BLOCK_BEGIN} to precede "
                    f"{markers.AGENTS_BLOCK_END}"
                ),
                required_outcome=(
                    "restore the managed block order so the begin marker "
                    "precedes the end marker; remove any duplicate or "
                    "out-of-order Ralph-managed markers"
                ),
            )
        )
    return findings


def _extract_managed_block_body(content: str) -> str | None:
    """Return the body text strictly between the unique begin/end markers.

    The validator slices AGENTS.md between the validated unique
    :data:`markers.AGENTS_BLOCK_BEGIN` and
    :data:`markers.AGENTS_BLOCK_END` markers and returns the body. A
    :keyword:`None` return means the block is malformed (no unique pair)
    and the caller has already emitted its own marker finding; this helper
    is a no-op for malformed blocks so the canonical-dir gate does not
    produce duplicate or buried findings.
    """
    if content.count(markers.AGENTS_BLOCK_BEGIN) != 1:
        return None
    if content.count(markers.AGENTS_BLOCK_END) != 1:
        return None
    begin_idx = content.find(markers.AGENTS_BLOCK_BEGIN)
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    if begin_idx >= end_idx:
        return None
    return content[begin_idx + len(markers.AGENTS_BLOCK_BEGIN) : end_idx]


def _check_managed_block_canonical_dir(content: str) -> list[PolicyFinding]:
    """Validate the managed block BODY references the canonical policy dir.

    The check is scoped strictly to the body sliced between the validated
    unique begin/end markers so a CANONICAL_DIR reference OUTSIDE the
    managed block (e.g. in user-authored prose before or after the block)
    cannot satisfy the gate. A malformed block returns no finding here
    because :func:`_check_managed_block_uniqueness` already surfaces the
    precise marker finding.
    """
    body = _extract_managed_block_body(content)
    if body is None:
        return []
    if markers.CANONICAL_DIR in body:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_MARKER_MISSING}:canonical-dir-ref",
            path=markers.AGENTS_MD,
            missing_evidence=(
                f"managed block body does not reference canonical policy "
                f"dir {markers.CANONICAL_DIR} (reference outside the block "
                "does not satisfy the gate)"
            ),
            required_outcome=(
                f"add a reference to {markers.CANONICAL_DIR} INSIDE the "
                f"managed block between {markers.AGENTS_BLOCK_BEGIN} and "
                f"{markers.AGENTS_BLOCK_END}"
            ),
        )
    ]


def _check_claude_md(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the CLAUDE.md contract (AGENTS.md pointer when CLAUDE.md exists)."""
    if not workspace.exists(markers.CLAUDE_MD):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_CLAUDE_MD_MISSING}:missing",
                path=markers.CLAUDE_MD,
                missing_evidence="CLAUDE.md is missing",
                required_outcome=f"create CLAUDE.md pointing Claude-compatible agents at {markers.AGENTS_MD}",
            )
        ]
    content = workspace.read(markers.CLAUDE_MD)
    if "AGENTS.md" not in content:
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:claude-agents-ref",
                path=markers.CLAUDE_MD,
                missing_evidence=f"CLAUDE.md does not reference {markers.AGENTS_MD}",
                required_outcome=f"append a pointer from CLAUDE.md to {markers.AGENTS_MD}",
            )
        ]
    return []


def _check_core_policy_file(workspace: Workspace, filename: str) -> list[PolicyFinding]:
    """Validate one canonical core policy file."""
    path = f"{markers.CANONICAL_DIR}{filename}"
    return _check_policy_file(workspace, path, filename)


def _check_policy_file(workspace: Workspace, path: str, filename: str) -> list[PolicyFinding]:
    """Validate one canonical policy file (presence, structure, fields, markers).

    Composed from small focused helpers to keep the branch count low.
    """
    if not workspace.exists(path):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_CORE_MISSING}:{filename}",
                path=path,
                missing_evidence=f"file does not exist: {path}",
                required_outcome=f"create the canonical policy file at {path}",
            )
        ]
    content = workspace.read(path)
    return _validate_existing_policy_file_with_workspace(workspace, content, path, filename)


def get_citation_workspace_slot() -> Workspace | None:
    """Return the current workspace bound to the citation-path check slot."""
    return (
        _content_checks._CITATION_PATH_WORKSPACE[0]
        if _content_checks._CITATION_PATH_WORKSPACE
        else None
    )


def set_citation_workspace_slot(workspace: Workspace | None) -> None:
    """Set the workspace bound to the citation-path check slot (for testing/isolation)."""
    if _content_checks._CITATION_PATH_WORKSPACE:
        _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    else:
        _content_checks._CITATION_PATH_WORKSPACE.append(workspace)


def _validate_existing_policy_file_with_workspace(
    workspace: Workspace, content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Like :func:`_validate_existing_policy_file` but also publishes the workspace seam.

    The workspace seam is required by the citation ``path:`` existence
    check (see :data:`_content_checks._CITATION_PATH_WORKSPACE`). The
    publication is scoped to the lifetime of the call so concurrent
    validators and unit tests do not see a stale reference.
    """
    previous = _content_checks._CITATION_PATH_WORKSPACE[0]
    _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    try:
        return _validate_existing_policy_file(content, path, filename)
    finally:
        _content_checks._CITATION_PATH_WORKSPACE[0] = previous


def _validate_existing_policy_file(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate every check that operates on an existing policy file's content."""
    findings: list[PolicyFinding] = []
    findings.extend(_check_markers(content, path, filename))
    findings.extend(_check_required_headings(content, path, filename))
    findings.extend(_check_research_basis(content, path, filename))
    findings.extend(_check_placeholders(content, path, filename))
    findings.extend(_check_pending_facts(content, path, filename))
    findings.extend(_check_required_fact_keys(content, path, filename))
    findings.extend(_check_commands(content, path, filename))
    findings.extend(_check_template_banner(content, path, filename))
    # v5 guard-inventory check (B9). Every applicable starter (core and
    # conditional) MUST carry a `## Guard inventory` table. Frozen packs
    # at v4 are exempt: the table was introduced in v5 and a frozen
    # v4 pack is validated against its own version's manifest, not
    # the v5 contract.
    if _frozen_schema_version(content) is None:
        findings.extend(_check_guard_inventory(content, path, filename))
    return findings


def _check_research_basis_with_workspace(
    workspace: Workspace, content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Run the research-basis check with the workspace seam in scope for the citation path check.

    The workspace is published to the module-level slot
    (:data:`_content_checks._CITATION_PATH_WORKSPACE`) only for the
    duration of the call, so unit tests that bypass the validator
    ingress are not perturbed.
    """
    previous = _content_checks._CITATION_PATH_WORKSPACE[0]
    _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    try:
        return _content_checks._check_research_basis(content, path, filename)
    finally:
        _content_checks._CITATION_PATH_WORKSPACE[0] = previous


def _check_verification_bypass(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the verification-policy bypass-detection gate.

    The gate requires the 'Bypass detection' heading AND at least one
    non-empty, placeholder-free, approved-tools RALPH-COMMAND line under
    it. The check is identical to the per-policy command gate except the
    finding id is namespaced under ``:bypass-cmd:<kind>`` so the user
    sees a precise pointer for an unusable bypass-detection gate:

    * ``:bypass-cmd:empty`` — the only command in the section is empty.
    * ``:bypass-cmd:placeholder`` — the value contains a placeholder
      token.
    * ``:bypass-cmd:unapproved`` — the value's first token is not on the
      fixed :data:`markers.APPROVED_GATE_TOOLS` allowlist (analysis
      feedback regression: arbitrary text such as ``definitely-not-a-command``
      must NOT satisfy the bypass-detection contract).
    * ``:bypass-cmd`` — no ``RALPH-COMMAND:`` line at all.
    """
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    findings: list[PolicyFinding] = []
    if not workspace.exists(path):
        return findings  # core-file presence check covers it.
    content = workspace.read(path)
    headings_present = _headings(content)
    if "bypass detection" not in headings_present:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_HEADING_MISSING}:verification-policy.md:bypass-detection",
                path=path,
                missing_evidence=(
                    "verification-policy.md is missing the 'Bypass detection' heading"
                ),
                required_outcome=(
                    "add a 'Bypass detection' heading with at least one "
                    "non-empty, placeholder-free RALPH-COMMAND that runs "
                    "the lint/typecheck bypass audit"
                ),
            )
        )
        return findings
    # The bypass-detection block must include at least one non-empty,
    # placeholder-free, approved-tools command. Empty RALPH-COMMAND:
    # markers are NOT accepted; the value after the marker must contain
    # non-whitespace text, no placeholder token, and a first token on the
    # approved allowlist.
    in_section = False
    saw_real_command = False
    saw_empty_command = False
    for line in content.splitlines():
        heading_match = _HEADING_LINE_RE.match(line)
        if heading_match is not None:
            heading_text = str(heading_match.group(1)).strip().lower()
            in_section = heading_text == "bypass detection"
            continue
        if not in_section:
            continue
        if not line.startswith(markers.COMMAND_MARKER):
            continue
        value = line[len(markers.COMMAND_MARKER) :].strip()
        if not value:
            saw_empty_command = True
            continue
        if any(token in value for token in markers.PLACEHOLDER_TOKENS):
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:placeholder"
                    ),
                    path=path,
                    missing_evidence=(
                        "'Bypass detection' RALPH-COMMAND contains a placeholder token"
                    ),
                    required_outcome=(
                        "replace the placeholder in the 'Bypass detection' "
                        "RALPH-COMMAND with a real runnable bypass-audit command"
                    ),
                )
            )
            continue
        if not _command_is_approved(value):
            first_token = _command_first_token(value)
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:unapproved"
                    ),
                    path=path,
                    missing_evidence=(
                        f"'Bypass detection' RALPH-COMMAND starts with "
                        f"'{first_token}', which is not in the approved "
                        "gate-tools allowlist; the bypass-audit gate MUST "
                        "be a runnable verification command (analysis "
                        "feedback regression)"
                    ),
                    required_outcome=(
                        "replace the command with one whose first token "
                        "is on the approved gate-tools allowlist (e.g. "
                        "`make <target>`, `pytest`, `mypy`, `ruff`, "
                        "`cargo`, `go`, `npm`, `pnpm`, `yarn`, `uv run`, "
                        "...)"
                    ),
                )
            )
            continue
        saw_real_command = True
    if saw_empty_command and not saw_real_command:
        findings.append(
            PolicyFinding(
                requirement_id=(
                    f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:empty"
                ),
                path=path,
                missing_evidence=(
                    "'Bypass detection' section contains only an empty "
                    "RALPH-COMMAND marker; the gate MUST declare a runnable "
                    "non-empty command"
                ),
                required_outcome=(
                    "add a non-empty, placeholder-free RALPH-COMMAND under "
                    "'Bypass detection' that runs the lint/typecheck bypass audit"
                ),
            )
        )
    if not saw_real_command and not saw_empty_command:
        findings.append(
            PolicyFinding(
                requirement_id=(f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd"),
                path=path,
                missing_evidence=("'Bypass detection' section has no RALPH-COMMAND line"),
                required_outcome=(
                    "add a non-empty, placeholder-free RALPH-COMMAND under "
                    "'Bypass detection' that runs the bypass-detection audit"
                ),
            )
        )
    return findings


def _check_conditional_domain(
    workspace: Workspace,
    domain: str,
    filename: str,
    required: bool,
) -> list[PolicyFinding]:
    """Validate a required or already-present conditional policy file."""
    path = f"{markers.CANONICAL_DIR}{filename}"
    if not required and not workspace.exists(path):
        return []
    return _check_policy_file(workspace, path, filename)


def _check_migration(workspace: Workspace) -> list[PolicyFinding]:
    """Emit a finding for every unresolved migration candidate."""
    findings: list[PolicyFinding] = []
    for candidate in evidence.migration_candidates(workspace):
        if candidate.resolved:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MIGRATE}:{candidate.path}",
                path=candidate.path,
                missing_evidence=(
                    f"policy-like content with heading '{candidate.recognized_heading}' "
                    "has not been migrated into the canonical policy directory"
                ),
                required_outcome=(
                    "consolidate the policy into the matching canonical file "
                    f"under {markers.CANONICAL_DIR} and add the "
                    "ralph-workflow-policy:migrated -> marker at this location; "
                    "preserve conventional community headings and unrelated content"
                ),
            )
        )
    return findings


def _check_applicability_overrides(workspace: Workspace) -> list[PolicyFinding]:
    """Validate explicit conditional-domain decisions without silent fallback."""
    path = markers.APPLICABILITY_OVERRIDES_PATH
    if not workspace.exists(path):
        return []
    findings: list[PolicyFinding] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'([a-z][a-z-]+)\s*=\s*"(required|inactive); reason: ([^;]+); review trigger: ([^"]+)"'
    )
    for index, raw_line in enumerate(workspace.read(path).splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "[domains]":
            continue
        match = pattern.fullmatch(line)
        if match is None:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_DOMAIN}:override-line-{index}",
                    path=path,
                    missing_evidence="malformed applicability decision",
                    required_outcome=(
                        'use `domain = "required|inactive; reason: ...; review trigger: ..."`'
                    ),
                )
            )
            continue
        domain = str(match.group(1))
        reason = str(match.group(3)).strip()
        review_trigger = str(match.group(4)).strip()
        if not reason or not review_trigger:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_DOMAIN}:override-empty-{index}",
                    path=path,
                    missing_evidence="applicability reason or review trigger is empty",
                    required_outcome="record non-whitespace evidence and a concrete review trigger",
                )
            )
        if domain not in markers.CONDITIONAL_POLICY_FILES:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_DOMAIN}:override-unknown-{index}",
                    path=path,
                    missing_evidence=f"unknown conditional domain {domain!r}",
                    required_outcome="use a domain declared by CONDITIONAL_POLICY_FILES",
                )
            )
        if domain in seen:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_DOMAIN}:override-duplicate-{domain}",
                    path=path,
                    missing_evidence=f"duplicate decision for {domain}",
                    required_outcome="keep exactly one evidence-backed decision per domain",
                )
            )
        seen.add(domain)
    return findings


def _is_unfrozen_v5_pack(workspace: Workspace, frozen_version: str | None) -> bool:
    """Return True if workspace represents an active, unfrozen v5 policy pack."""
    if frozen_version is not None:
        return False
    if workspace.exists(f"{markers.CANONICAL_DIR}execution-kernel.md"):
        return True
    for name in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        if workspace.exists(path) and markers.POLICY_SCHEMA_MARKER in workspace.read(path):
            return True
    return False


def _check_portfolio(
    workspace: Workspace,
    *,
    frozen_version: str | None = None,
) -> list[PolicyFinding]:
    """Require a valid bounded portfolio manifest."""
    path = markers.PORTFOLIO_PATH
    if not workspace.exists(path):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_PORTFOLIO}:missing",
                path=path,
                missing_evidence="canonical policy portfolio manifest is missing",
                required_outcome=(
                    "create the canonical bounded verification portfolio manifest "
                    "and declare each control's protected outcome"
                ),
            )
        ]
    content = workspace.read(path)
    if frozen_version is None:
        frozen_version = _detect_frozen_version(workspace)

    is_frozen_v4 = frozen_version == "v4"
    has_v1_marker = bool(re.search(r'schema_version\s*=\s*["\']v1["\']', content))
    unfrozen_v5 = _is_unfrozen_v5_pack(workspace, frozen_version)

    if unfrozen_v5:
        expected_version = "v2"
    elif is_frozen_v4 or has_v1_marker:
        expected_version = (
            "v2" if re.search(r'schema_version\s*=\s*["\']v2["\']', content) else "v1"
        )
    else:
        expected_version = "v2"

    try:
        parse_portfolio_toml(
            content,
            workspace_exists=workspace.exists,
            expected_schema_version=expected_version,
        )
    except PortfolioError as exc:
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_PORTFOLIO}:invalid",
                path=path,
                missing_evidence=str(exc),
                required_outcome=(
                    "repair the deterministic composition while preserving named "
                    "protected outcomes, lane ownership, evidence, lifecycle, and budget"
                ),
            )
        ]
    return []


def _detect_frozen_version(workspace: Workspace) -> str | None:
    """Return the frozen schema version across the canonical policy files.

    Walks every core and conditional file in the current manifest and returns the
    highest ``freeze vN`` marker it finds (or ``None`` when no file
    is frozen). The validator uses this to switch the manifest a
    frozen pack is validated against: a v4-frozen pack is checked
    against the v4 manifest, not the v5 one, so it can stay on its
    own frozen schema.
    """
    highest: str | None = None
    all_files = (*markers.CORE_POLICY_FILES, *markers.CONDITIONAL_POLICY_FILES.values())
    for filename in all_files:
        path = f"{markers.CANONICAL_DIR}{filename}"
        if not workspace.exists(path):
            continue
        v_num = _frozen_schema_version(workspace.read(path))
        if v_num is None:
            continue
        version = f"v{v_num}"
        if highest is None or v_num > int(highest[1:]):
            highest = version
    return highest


def validate_readiness(workspace: Workspace, stack: ProjectStack) -> list[PolicyFinding]:
    """Run every deterministic readiness check and return the findings list.

    The function is pure: it does NOT mutate the workspace and it does NOT
    consult any AI. Every check reads through the workspace seam and emits
    zero or more findings. A project is ready iff the returned list is
    empty.

    The core-file manifest is version-aware (v5): when a project has
    any ``freeze vN`` marker across the canonical core files, the
    manifest switches to ``markers.core_files_for_version(v)`` so a
    frozen v4 pack is validated against the v4 manifest (the original
    eleven core files) and not the v5 manifest (twelve files including
    ``execution-kernel.md``). The check stays the binding gate: a
    frozen v4 pack missing ``security-policy.md`` is NOT READY because
    the v4 manifest still requires it.
    """
    findings: list[PolicyFinding] = []
    findings.extend(_check_agents_md(workspace))
    findings.extend(_check_claude_md(workspace))
    frozen_version = _detect_frozen_version(workspace)
    findings.extend(_check_portfolio(workspace, frozen_version=frozen_version))
    # Manifest selection: a frozen pack is validated against its own
    # version's manifest. An unfrozen pack (or a fresh v5 pack) uses
    # the current CORE_POLICY_FILES.
    core_manifest = (
        markers.core_files_for_version(frozen_version)
        if frozen_version is not None
        else markers.CORE_POLICY_FILES
    )
    for filename in core_manifest:
        findings.extend(_check_core_policy_file(workspace, filename))

    findings.extend(_check_per_language_coverage(workspace, stack))
    findings.extend(_check_verification_bypass(workspace))
    findings.extend(_check_applicability_overrides(workspace))

    requirements = evidence.conditional_domain_requirements(workspace, stack)
    for domain, filename in markers.CONDITIONAL_POLICY_FILES.items():
        required, _ = requirements[domain]
        findings.extend(_check_conditional_domain(workspace, domain, filename, required))

    findings.extend(_check_migration(workspace))
    return findings


__all__ = ["validate_readiness"]
