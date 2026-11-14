from __future__ import annotations

import ast
import re
from collections import OrderedDict
from typing import TYPE_CHECKING

from ralph.project_policy import (
    _content_checks,
    agents_md,
    evidence,
    instruction_surface,
    markers,
)
from ralph.project_policy._content_checks import (
    _check_capture_command_fact,
    _check_commands,
    _check_markers,
    _check_per_language_coverage,
    _check_placeholders,
    _check_required_fact_keys,
    _check_required_headings,
    _check_research_basis,
    _check_template_banner,
    _frozen_schema_version,
    get_research_freshness_clock,
    set_research_freshness_clock,
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
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace


def _check_agents_md(workspace: Workspace) -> list[PolicyFinding]:
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
        findings.extend(_check_managed_block_canonical_dir(content))
    findings.extend(agents_md.check_agents_md_line_budgets(content))
    findings.extend(agents_md.check_agents_md_path_citations(workspace, content))
    findings.extend(instruction_surface.check_hidden_unicode(content, markers.AGENTS_MD))
    return findings


def _check_managed_block_uniqueness(content: str) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    begin_matches = list(markers.AGENTS_BLOCK_BEGIN_RE.finditer(content))
    begin_count = len(begin_matches)
    end_count = content.count(markers.AGENTS_BLOCK_END)
    begin_idx = begin_matches[0].start() if begin_matches else -1
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
    block = agents_md._locate_managed_block(content)
    if block is None:
        return None
    return block.body


def _check_managed_block_canonical_dir(content: str) -> list[PolicyFinding]:
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
    return instruction_surface.check_hidden_unicode(content, markers.CLAUDE_MD)


def _extract_verify_step_labels(source: str) -> set[str]:
    labels: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return labels
    for node in ast.walk(tree):
        target_names: list[str] = []
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                target_names = [node.target.id]
            value_node = node.value

        if "_VERIFY_STEPS" in target_names and isinstance(value_node, (ast.Tuple, ast.List)):
            for elt in value_node.elts:
                if isinstance(elt, (ast.Tuple, ast.List)) and elt.elts:
                    first = elt.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        labels.add(first.value)
    return labels


def _collect_known_enforcers(workspace: Workspace) -> frozenset[str]:
    enforcers: set[str] = set()
    all_files = (*markers.CORE_POLICY_FILES, *markers.CONDITIONAL_POLICY_FILES.values())
    for filename in all_files:
        path = f"{markers.CANONICAL_DIR}{filename}"
        if not workspace.exists(path):
            continue
        for line in workspace.read(path).splitlines():
            if line.startswith(markers.COMMAND_MARKER):
                val = line[len(markers.COMMAND_MARKER) :].strip()
                if val:
                    enforcers.add(val)

    for verify_path in (
        "ralph-workflow/ralph/verify_steps.py",
        "ralph/verify_steps.py",
        "ralph-workflow/ralph/verify.py",
        "ralph/verify.py",
    ):
        if not workspace.exists(verify_path):
            continue
        enforcers.update(_extract_verify_step_labels(workspace.read(verify_path)))
    return frozenset(enforcers)


def _check_core_policy_file(
    workspace: Workspace,
    filename: str,
    *,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
    path = f"{markers.CANONICAL_DIR}{filename}"
    return _check_policy_file(workspace, path, filename, known_enforcers=known_enforcers)


def _check_policy_file(
    workspace: Workspace,
    path: str,
    filename: str,
    *,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
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
    return _validate_existing_policy_file_with_workspace(
        workspace,
        content,
        path,
        filename,
        known_enforcers=known_enforcers,
    )


def get_citation_workspace_slot() -> Workspace | None:
    return (
        _content_checks._CITATION_PATH_WORKSPACE[0]
        if _content_checks._CITATION_PATH_WORKSPACE
        else None
    )


def set_citation_workspace_slot(workspace: Workspace | None) -> None:
    if _content_checks._CITATION_PATH_WORKSPACE:
        _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    else:
        _content_checks._CITATION_PATH_WORKSPACE.append(workspace)


def _validate_existing_policy_file_with_workspace(
    workspace: Workspace,
    content: str,
    path: str,
    filename: str,
    *,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
    previous = _content_checks._CITATION_PATH_WORKSPACE[0]
    _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    try:
        return _validate_existing_policy_file(
            content,
            path,
            filename,
            known_enforcers=known_enforcers
            if known_enforcers is not None
            else _collect_known_enforcers(workspace),
        )
    finally:
        _content_checks._CITATION_PATH_WORKSPACE[0] = previous


_MAX_POLICY_VALIDATION_CACHE_ENTRIES = 256
_POLICY_VALIDATION_CACHE: OrderedDict[
    tuple[str, str, str, frozenset[str] | None], list[PolicyFinding]
] = OrderedDict()


def clear_policy_validation_cache() -> None:
    _POLICY_VALIDATION_CACHE.clear()


def _validate_existing_policy_file(
    content: str,
    path: str,
    filename: str,
    *,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
    can_cache = "path:" not in content
    cache_key = (content, path, filename, known_enforcers) if can_cache else None
    if cache_key is not None and cache_key in _POLICY_VALIDATION_CACHE:
        return list(_POLICY_VALIDATION_CACHE[cache_key])

    findings: list[PolicyFinding] = []
    findings.extend(_check_markers(content, path, filename))
    findings.extend(_check_required_headings(content, path, filename))
    findings.extend(_check_research_basis(content, path, filename))
    findings.extend(_check_placeholders(content, path, filename))
    findings.extend(_check_pending_facts(content, path, filename))
    findings.extend(_check_required_fact_keys(content, path, filename))
    findings.extend(_check_capture_command_fact(content, path, filename))
    findings.extend(_check_commands(content, path, filename))
    findings.extend(_check_template_banner(content, path, filename))
    findings.extend(instruction_surface.check_hidden_unicode(content, path))
    frozen_v = _frozen_schema_version(content)
    file_version = f"v{frozen_v}" if frozen_v is not None else markers.SCHEMA_VERSION
    expected_header = markers.guard_inventory_header_for_version(file_version)
    if expected_header is not None:
        file_commands = {
            line[len(markers.COMMAND_MARKER) :].strip()
            for line in content.splitlines()
            if line.startswith(markers.COMMAND_MARKER)
            and line[len(markers.COMMAND_MARKER) :].strip()
        }
        enforcers_pool: set[str] = set(file_commands)
        if known_enforcers is not None:
            enforcers_pool.update(known_enforcers)
        elif (
            _content_checks._CITATION_PATH_WORKSPACE
            and _content_checks._CITATION_PATH_WORKSPACE[0] is not None
        ):
            enforcers_pool.update(
                _collect_known_enforcers(_content_checks._CITATION_PATH_WORKSPACE[0])
            )
        resolved_enforcers = frozenset(enforcers_pool)
        findings.extend(
            _check_guard_inventory(
                content,
                path,
                filename,
                expected_header=expected_header,
                known_enforcers=resolved_enforcers,
            )
        )
    if cache_key is not None:
        while len(_POLICY_VALIDATION_CACHE) >= _MAX_POLICY_VALIDATION_CACHE_ENTRIES:
            _POLICY_VALIDATION_CACHE.popitem(last=False)
        _POLICY_VALIDATION_CACHE[cache_key] = list(findings)
    return findings


def _check_research_basis_with_workspace(
    workspace: Workspace, content: str, path: str, filename: str
) -> list[PolicyFinding]:
    previous = _content_checks._CITATION_PATH_WORKSPACE[0]
    _content_checks._CITATION_PATH_WORKSPACE[0] = workspace
    try:
        return _content_checks._check_research_basis(content, path, filename)
    finally:
        _content_checks._CITATION_PATH_WORKSPACE[0] = previous


def _check_verification_bypass(workspace: Workspace) -> list[PolicyFinding]:
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    findings: list[PolicyFinding] = []
    if not workspace.exists(path):
        return findings
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
    *,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
    path = f"{markers.CANONICAL_DIR}{filename}"
    if not required and not workspace.exists(path):
        return []
    return _check_policy_file(workspace, path, filename, known_enforcers=known_enforcers)


def _check_migration(workspace: Workspace) -> list[PolicyFinding]:
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


def _is_unfrozen_current_pack(workspace: Workspace, frozen_version: str | None) -> bool:
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
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
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

    has_v1_marker = bool(re.search(r'schema_version\s*=\s*["\']v1["\']', content))
    unfrozen_current = _is_unfrozen_current_pack(workspace, frozen_version)

    if unfrozen_current:
        expected_version = markers.portfolio_schema_version_for_version(markers.SCHEMA_VERSION)
    elif frozen_version is not None and frozen_version in markers.VERSION_SPECS:
        allowed = markers.VERSION_SPECS[frozen_version].portfolio_schema_version
        match = re.search(r'schema_version\s*=\s*["\'](v\d+)["\']', content)
        actual = match.group(1) if match else None
        expected_version = actual if actual in allowed else allowed[0]
    elif has_v1_marker:
        expected_version = "v1"
    else:
        expected_version = markers.portfolio_schema_version_for_version(frozen_version)

    if known_enforcers is None:
        known_enforcers = _collect_known_enforcers(workspace)

    try:
        parse_portfolio_toml(
            content,
            workspace_exists=workspace.exists,
            expected_schema_version=expected_version,
            known_enforcers=known_enforcers,
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
    findings: list[PolicyFinding] = []
    findings.extend(_check_agents_md(workspace))
    findings.extend(_check_claude_md(workspace))
    frozen_version = _detect_frozen_version(workspace)
    known_enforcers = _collect_known_enforcers(workspace)
    findings.extend(
        _check_portfolio(
            workspace,
            frozen_version=frozen_version,
            known_enforcers=known_enforcers,
        )
    )
    core_manifest = (
        markers.core_files_for_version(frozen_version)
        if frozen_version is not None
        else markers.CORE_POLICY_FILES
    )
    for filename in core_manifest:
        findings.extend(
            _check_core_policy_file(
                workspace,
                filename,
                known_enforcers=known_enforcers,
            )
        )

    findings.extend(_check_per_language_coverage(workspace, stack))
    findings.extend(_check_verification_bypass(workspace))
    findings.extend(_check_applicability_overrides(workspace))

    requirements = evidence.conditional_domain_requirements(workspace, stack)
    for domain, filename in markers.CONDITIONAL_POLICY_FILES.items():
        required, _ = requirements[domain]
        findings.extend(
            _check_conditional_domain(
                workspace,
                domain,
                filename,
                required,
                known_enforcers=known_enforcers,
            )
        )

    findings.extend(_check_migration(workspace))
    return findings


def check_capture_command_fact(
    content: str,
    path: str,
    filename: str,
) -> list[PolicyFinding]:
    return _check_capture_command_fact(content, path, filename)


def check_policy_citations(
    content: str,
    path: str = "docs/policy.md",
    filename: str | None = None,
    *,
    workspace: Workspace | None = None,
) -> list[PolicyFinding]:
    if workspace is None:
        workspace = MemoryWorkspace()
    resolved_filename = filename if filename is not None else path.rsplit("/", 1)[-1]
    return _check_research_basis_with_workspace(workspace, content, path, resolved_filename)


__all__ = [
    "check_capture_command_fact",
    "check_policy_citations",
    "get_research_freshness_clock",
    "set_research_freshness_clock",
    "validate_readiness",
]
