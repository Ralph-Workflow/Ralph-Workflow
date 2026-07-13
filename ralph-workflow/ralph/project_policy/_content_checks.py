"""Per-file content checks for the deterministic project-policy validator.

Split out of :mod:`ralph.project_policy.validators` (repo structure policy caps
a module at 1000 lines). Each function here takes already-read content and
returns :class:`PolicyFinding` objects; the orchestration (which files to read,
in what order) stays in ``validators``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ralph.project_policy import evidence, markers, starters
from ralph.project_policy._scanners import (
    _LANG_LINE_RE,
    _MANDATORY_GATE_FILES,
    _REVIEW_LINE_RE,
    _REVIEW_VALUE_RE,
    _check_individual_pendings,
    _citation_blocks,
    _command_first_token,
    _command_is_approved,
    _command_raw_lines,
    _command_values,
    _contains_placeholder,
    _fact_lines,
    _headings,
    _inapplicable_lines,
    _inapplicable_raw_lines,
    _lang_blocks,
    _pending_raw_lines,
    _pending_values,
)
from ralph.project_policy.models import PolicyFinding

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

def _fact_key(line: str) -> str | None:
    match = re.fullmatch(r"RALPH-FACT:\s*([^:]+):\s*\S.*", line)
    return str(match.group(1)).strip() if match else None


def _check_required_fact_keys(
    content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Require every fact key declared by the bundled contract exactly once."""
    required = {
        key
        for line in starters.read_starter(filename).splitlines()
        if (key := _fact_key(line)) is not None
    }
    present_keys = [
        key
        for line in content.splitlines()
        if (key := _fact_key(line)) is not None
    ]
    findings: list[PolicyFinding] = []
    for key in sorted(required):
        count = present_keys.count(key)
        if count == 1:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}:fact-{key}",
                path=path,
                missing_evidence=(
                    f"required RALPH-FACT key {key!r} occurs {count} times; expected once"
                ),
                required_outcome=(
                    f"record exactly one verified `RALPH-FACT: {key}: <value>` line"
                ),
            )
        )
    return findings


def _check_template_banner(
    content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Reject a policy file that still carries the starter template banner.

    Separate from the PLACEHOLDER_TOKENS scan (which also covers the whole
    file but reports only the first token found) so the banner always gets
    its own stable finding id and an explicit delete-the-banner outcome
    instead of being shadowed by an unresolved fact placeholder.
    """
    if markers.STARTER_TEMPLATE_TOKEN not in content:
        return []
    return [
        PolicyFinding(
            requirement_id=(
                f"{markers.ID_PLACEHOLDER}:{filename}:starter-template-banner"
            ),
            path=path,
            missing_evidence=(
                f"file still contains the {markers.STARTER_TEMPLATE_TOKEN} "
                "banner comment"
            ),
            required_outcome=(
                "rewrite the file into verified project policy and delete "
                "the starter template banner comment"
            ),
        )
    ]


def _check_markers(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the schema + policy-id markers."""
    findings: list[PolicyFinding] = []
    lines = content.splitlines()
    first_line = next((line for line in lines if line.strip()), "")
    frozen_version = _frozen_schema_version(content)
    current_version = int(markers.SCHEMA_VERSION.removeprefix("v"))
    frozen_schema = frozen_version is not None and frozen_version < current_version
    if first_line != markers.POLICY_SCHEMA_MARKER and not frozen_schema:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:schema-{filename}",
                path=path,
                missing_evidence=f"missing schema marker {markers.POLICY_SCHEMA_MARKER}",
                required_outcome=(
                    "choose whether to upgrade this policy to the bundled schema "
                    "or freeze its current schema in place, then add the selected "
                    "schema marker at the top of the file"
                ),
            )
        )
    expected_id = f"{markers.POLICY_ID_PREFIX} {filename} -->"
    if lines.count(expected_id) != 1:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:id-{filename}",
                path=path,
                missing_evidence=f"missing policy-id line: {expected_id}",
                required_outcome=(
                    f"add `<!-- ralph-policy-id: {filename} -->` comment to the file"
                ),
            )
        )
    return findings


def _frozen_schema_version(content: str) -> int | None:
    """Return an explicitly frozen older schema version, if well formed."""
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    match = re.fullmatch(r"<!-- ralph-policy-schema: freeze v([0-9]+) -->", first_line)
    return int(match.group(1)) if match else None


def _check_required_headings(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate every required heading is present (case-insensitive)."""
    required = markers.REQUIRED_HEADINGS.get(filename, ())
    headings_present = _headings(content)
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_HEADING_MISSING}:{filename}:{required_heading}",
            path=path,
            missing_evidence=f"missing required heading '{required_heading}'",
            required_outcome=f"add a heading with the exact text '{required_heading}'",
        )
        for required_heading in required
        if required_heading.lower() not in headings_present
    ]


def _check_research_basis(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the Research basis section exists and every citation is complete."""
    findings: list[PolicyFinding] = []
    headings_lower = {h.lower() for h in _headings(content)}
    if "research basis" not in headings_lower:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_HEADING_MISSING}:{filename}:research-basis",
                path=path,
                missing_evidence="Research basis section is missing",
                required_outcome="add a 'Research basis' heading with at least one citation",
            )
        )
        return findings
    blocks = _citation_blocks(content)
    if not blocks:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CITATION_MISSING}:{filename}:empty",
                path=path,
                missing_evidence=(
                    "Research basis section has no citations; "
                    f"each citation must include {', '.join(markers.CITATION_REQUIRED_FIELDS)}"
                ),
                required_outcome=(
                    "add at least one citation under Research basis with "
                    "publisher, title, URL (http), and review date"
                ),
            )
        )
        return findings
    findings.extend(_check_individual_citations(blocks, path, filename))
    return findings


def _check_individual_citations(
    blocks: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every citation block contains the required fields."""
    findings: list[PolicyFinding] = []
    for index, block in enumerate(blocks, start=1):
        lines = [line.strip().lstrip("*- ") for line in block.splitlines()]
        fields = {
            key: value.strip()
            for line in lines
            if ":" in line
            for key, value in [line.split(":", 1)]
        }
        missing = [field for field in markers.CITATION_REQUIRED_FIELDS if not fields.get(field)]
        url = fields.get("http", "")
        parsed = urlparse(url)
        valid_url = parsed.scheme == "https" and bool(parsed.netloc)
        valid_date = bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", fields.get("review date", "")))
        if not missing and valid_url and valid_date:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CITATION_MISSING}:{filename}:block-{index}",
                path=path,
                missing_evidence=(
                    f"Research basis citation {index} has missing/invalid fields: "
                    f"missing={missing}, https_url={valid_url}, iso_date={valid_date}"
                ),
                required_outcome=(
                    f"add missing fields {missing} to citation {index} "
                    f"(publisher, title, http URL, review date)"
                ),
            )
        )
    return findings


def _check_placeholders(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate every RALPH-FACT line is resolved and at least one exists.

    A complete policy must declare at least one resolved ``RALPH-FACT:``
    line — the validator rejects generic prose that is missing machine-
    checkable project facts. Placeholder tokens anywhere in the file are
    rejected (the contract is project-specific content, not template
    copy).
    """
    findings: list[PolicyFinding] = []
    placeholder = _contains_placeholder(content)
    if placeholder is not None:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}",
                path=path,
                missing_evidence=f"unresolved placeholder token: {placeholder}",
                required_outcome=f"replace '{placeholder}' with verified project fact",
            )
        )
    fact_lines = _fact_lines(content)
    if not fact_lines:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}:no-fact",
                path=path,
                missing_evidence=(
                    "policy file declares no resolved RALPH-FACT line; every "
                    "project-specific policy must enumerate its machine-"
                    "checkable project facts (test commands, paths, "
                    "owners, budgets, supported platforms, etc.) as "
                    "`RALPH-FACT: <key>: <value>` lines"
                ),
                required_outcome=(
                    "add at least one resolved `RALPH-FACT:` line naming a "
                    "verified project-specific fact; replace any starter "
                    "placeholder with a real value before removing this finding"
                ),
            )
        )
    for line in content.splitlines():
        if not line.startswith(markers.FACT_MARKER):
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in line:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}:fact-line",
                        path=path,
                        missing_evidence=(
                            f"RALPH-FACT line contains placeholder '{placeholder_token}'"
                        ),
                        required_outcome="resolve every RALPH-FACT line with a verified value",
                    )
                )
                break
    return findings


def _check_commands(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate at least one non-empty command OR inapplicable marker exists.

    Empty ``RALPH-COMMAND:`` and ``RALPH-INAPPLICABLE:`` lines are explicitly
    rejected so a policy cannot exempt itself from the gate with an empty
    marker. The general inapplicability value must name a reason (e.g. why
    the verification step does not apply to this project).
    """
    findings: list[PolicyFinding] = []
    command_values = _command_values(content)
    command_raw_values = _command_raw_lines(content)
    inapplicable_values = _inapplicable_lines(content)
    inapplicable_raw_values = _inapplicable_raw_lines(content)
    pending_raw_values = _pending_raw_lines(content)
    has_command = bool(command_values)
    has_inapplicable = bool(inapplicable_values)
    has_pending = bool(_pending_values(content))
    review_values = [
        line[len(markers.REVIEW_MARKER):].strip()
        for line in content.splitlines()
        if _REVIEW_VALUE_RE.match(line)
    ]
    review_raw_values = [
        line[len(markers.REVIEW_MARKER):].strip()
        for line in content.splitlines()
        if _REVIEW_LINE_RE.match(line)
    ]
    command_required = filename in _MANDATORY_GATE_FILES
    if (command_required and not has_command and not has_pending) or (
        not command_required
        and not has_command
        and not has_inapplicable
        and not review_values
        and not has_pending
    ):
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:missing",
                path=path,
                missing_evidence=(
                    "file lacks a runnable RALPH-COMMAND gate or a "
                    "RALPH-PENDING deferral"
                    if command_required
                    else "file contains no non-empty RALPH-COMMAND, RALPH-REVIEW, RALPH-INAPPLICABLE, or RALPH-PENDING line"
                ),
                required_outcome=(
                    "add at least one RALPH-COMMAND line with a real runnable "
                    "verification command (or RALPH-INAPPLICABLE with a "
                    "reason, or RALPH-PENDING for a gate not wired yet)"
                ),
            )
        )
    findings.extend(_check_individual_commands(command_raw_values, path, filename))
    findings.extend(
        _check_individual_inapplicables(inapplicable_raw_values, path, filename)
    )
    findings.extend(
        _check_individual_pendings(pending_raw_values, path, filename)
    )
    for index, value in enumerate(review_raw_values, start=1):
        lowered = value.lower()
        if not value or "evidence:" not in lowered or "owner:" not in lowered:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:review-{index}",
                    path=path,
                    missing_evidence="RALPH-REVIEW lacks a procedure, evidence, or owner",
                    required_outcome=(
                        "declare `RALPH-REVIEW: <procedure>; evidence: <record>; owner: <role>`"
                    ),
                )
            )
    return findings


def _check_individual_inapplicables(
    inapplicable_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every RALPH-INAPPLICABLE value is non-empty and placeholder-free.

    The parser filters empty lines before reaching this helper, but
    ``_fact_lines``/``_inapplicable_lines`` also enforce the
    non-whitespace-value contract via regex. A no-op marker (the bare
    ``RALPH-INAPPLICABLE:`` token with only whitespace after the colon)
    MUST produce a stable RWP-CMD unusable finding so the user sees a
    precise pointer at the offending line.
    """
    findings: list[PolicyFinding] = []
    for index, value in enumerate(inapplicable_values, start=1):
        if not value:
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:{filename}:empty-inapplicable-{index}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"RALPH-INAPPLICABLE line {index} is empty; the marker "
                        "must declare a reason or remove the line"
                    ),
                    required_outcome=(
                        "add the reason the gate does not apply "
                        "(e.g. 'no graphical surface in CI') or replace the "
                        "line with a real RALPH-COMMAND"
                    ),
                )
            )
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in value:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:placeholder-inapplicable-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-INAPPLICABLE line {index} contains placeholder "
                            f"token '{placeholder_token}'"
                        ),
                        required_outcome=(
                            "replace the placeholder in RALPH-INAPPLICABLE with "
                            "the real reason the gate does not apply"
                        ),
                    )
                )
                break
        lowered = value.lower()
        if filename == "testing-policy.md":
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:mandatory-{index}",
                    path=path,
                    missing_evidence="automated testing is mandatory for behavior-bearing software",
                    required_outcome="replace RALPH-INAPPLICABLE with a real testing gate",
                )
            )
        elif filename == "typechecking-policy.md" and not (
            lowered.startswith(
                "exceptional case: no suitable maintained checker exists;"
            )
            or lowered.startswith(
                "exceptional case: technically non-checkable first-party surface;"
            )
        ):
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:unsupported-reason-{index}",
                    path=path,
                    missing_evidence="type-checking exception is not an enumerated exceptional case",
                    required_outcome=(
                        "use a maintained checker or an exact exceptional-case declaration"
                    ),
                )
            )
        elif filename == "typechecking-policy.md" and not all(
            field in lowered
            for field in (
                "evidence:",
                "owner:",
                "expiry:",
                "warning:",
                "review trigger:",
            )
        ):
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:incomplete-exception-{index}",
                    path=path,
                    missing_evidence=(
                        "type-checking exception lacks evidence, owner, expiry, warning, or review trigger"
                    ),
                    required_outcome=(
                        "record every required exception field and keep the warning visible"
                    ),
                )
            )
        elif filename == "linting-policy.md" and not (
            lowered.startswith("exceptional case: no suitable maintained linter exists;")
            or lowered.startswith(
                "exceptional case: technically non-lintable first-party surface;"
            )
        ):
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:unsupported-reason-{index}",
                    path=path,
                    missing_evidence=(
                        "inapplicability lacks technical non-support evidence and a review trigger"
                    ),
                    required_outcome=(
                        "use a maintained gate, or state that no suitable maintained tool exists "
                        "or checking is technically inapplicable, with a review trigger"
                    ),
                )
            )
        elif filename == "linting-policy.md" and not all(
            field in lowered
            for field in (
                "evidence:",
                "owner:",
                "expiry:",
                "warning:",
                "review trigger:",
            )
        ):
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:incomplete-exception-{index}",
                    path=path,
                    missing_evidence=(
                        "lint/format exception lacks evidence, owner, expiry, warning, or review trigger"
                    ),
                    required_outcome=(
                        "record every required exception field and keep the warning visible"
                    ),
                )
            )
    return findings






def _check_individual_commands(
    command_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every RALPH-COMMAND value is non-empty, placeholder-free, and approved.

    The check enforces three independent gates:

    1. The value is non-empty (an empty ``RALPH-COMMAND:`` line is not a
       valid gate).
    2. The value contains no placeholder token (template copy is not a
       valid gate).
    3. The value's first whitespace-separated token is on the fixed
       :data:`markers.APPROVED_GATE_TOOLS` allowlist (analysis feedback:
       arbitrary non-empty text such as ``definitely-not-a-command`` does
       NOT satisfy the executable-gate contract; the validator must
       reject it with a stable ``RWP-CMD:*:unapproved-cmd-N`` finding).

    A failing gate produces a stable ``RWP-CMD:filename:<kind>-N``
    finding so the user sees a precise pointer at the offending line.
    """
    findings: list[PolicyFinding] = []
    for index, value in enumerate(command_values, start=1):
        if not value:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:empty-cmd-{index}",
                    path=path,
                    missing_evidence=f"RALPH-COMMAND line {index} is empty",
                    required_outcome="remove the empty command or replace it with a runnable command",
                )
            )
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in value:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:placeholder-cmd-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-COMMAND line {index} contains placeholder "
                            f"token '{placeholder_token}'"
                        ),
                        required_outcome=(
                            "replace the placeholder in RALPH-COMMAND with the "
                            "real runnable command"
                        ),
                    )
                )
                break
        else:
            # Only run the approved-tools check when neither empty nor
            # placeholder fired (the per-line ``else`` belongs to the
            # ``for`` loop and runs when no ``break`` was taken, i.e. no
            # placeholder matched). This keeps the findings list minimal:
            # a placeholder command gets the placeholder finding, not an
            # additional unapproved-tools finding on top of it.
            if not _command_is_approved(value):
                first_token = _command_first_token(value)
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:unapproved-cmd-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-COMMAND line {index} starts with "
                            f"'{first_token}', which is not in the "
                            "approved gate-tools allowlist; declared "
                            "gates MUST be executable verification "
                            "commands (analysis feedback regression)"
                        ),
                        required_outcome=(
                            "replace the command with one whose first "
                            "token is on the approved gate-tools "
                            "allowlist (e.g. `make <target>`, `pytest`, "
                            "`mypy`, `ruff`, `cargo`, `go`, `npm`, `pnpm`, "
                            "`yarn`, `uv run`, ...) or declare the gate "
                            "inapplicable via a `RALPH-INAPPLICABLE:` line"
                        ),
                    )
                )
    return findings


def _check_per_language_coverage(
    workspace: Workspace, stack: ProjectStack
) -> list[PolicyFinding]:
    """Validate per-language RALPH-LANG coverage in typecheck and lint policies.

    Per-language blocks must contain a non-empty ``RALPH-COMMAND`` or
    ``RALPH-INAPPLICABLE`` line. An empty inapplicable marker is treated
    as a missing declaration and produces a stable
    ``RWP-LANG`` finding; a no-op exemption cannot silently disable a
    per-language gate.
    """
    required_langs = evidence.required_languages(stack)
    if not required_langs:
        return []
    findings: list[PolicyFinding] = []
    for filename in ("typechecking-policy.md", "linting-policy.md"):
        path = f"{markers.CANONICAL_DIR}{filename}"
        if not workspace.exists(path):
            continue  # core-file presence check already emitted a finding.
        content = workspace.read(path)
        blocks = _lang_blocks(content)
        # Detect empty ``RALPH-INAPPLICABLE:`` lines inside per-language
        # blocks (the parser strips them from the boolean count above).
        empty_lang_inapplicable = _find_empty_per_language_inapplicable(content)
        for language in sorted(required_langs):
            if language not in blocks:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_LANG_COVERAGE}:{filename}:{language}",
                        path=path,
                        missing_evidence=(
                            f"no RALPH-LANG block for '{language}' in {filename}"
                        ),
                        required_outcome=(
                            f"add `RALPH-LANG: {language}` followed by a "
                            "RALPH-COMMAND, RALPH-INAPPLICABLE, or "
                            "RALPH-PENDING line for this language"
                        ),
                    )
                )
                continue
            has_command, has_inapplicable, has_pending = blocks[language]
            if not has_command and not has_inapplicable and not has_pending:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_LANG_COVERAGE}:{filename}:{language}:empty",
                        path=path,
                        missing_evidence=(
                            f"RALPH-LANG block for '{language}' has no "
                            "non-empty RALPH-COMMAND, RALPH-INAPPLICABLE, or "
                            "RALPH-PENDING"
                        ),
                        required_outcome=(
                            f"add a non-empty RALPH-COMMAND, "
                            f"RALPH-INAPPLICABLE, or RALPH-PENDING line after "
                            f"`RALPH-LANG: {language}`"
                        ),
                    )
                )
            if language in empty_lang_inapplicable:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_LANG_COVERAGE}:{filename}:{language}:"
                            f"empty-inapplicable"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-LANG block for '{language}' contains an "
                            "empty RALPH-INAPPLICABLE line; the marker must "
                            "declare a reason or be replaced by RALPH-COMMAND"
                        ),
                        required_outcome=(
                            f"add a real reason after `RALPH-INAPPLICABLE:` in "
                            f"the `{language}` block, or remove the empty "
                            "marker and add a runnable RALPH-COMMAND instead"
                        ),
                    )
                )
    return findings


def _find_empty_per_language_inapplicable(content: str) -> set[str]:
    """Return the set of language names whose block contains an empty
    ``RALPH-INAPPLICABLE:`` line.

    An empty inapplicable marker is a structural defect: it claims
    inapplicability without justification, so it MUST produce a finding
    even when the language block also contains a usable command.
    """
    out: set[str] = set()
    current_lang: str | None = None
    for line in content.splitlines():
        lang_match = _LANG_LINE_RE.match(line)
        if lang_match is not None:
            current_lang = str(lang_match.group(1)).strip()
            continue
        if current_lang is None:
            continue
        stripped = line[len(markers.INAPPLICABLE_MARKER):].strip() if line.startswith(
            markers.INAPPLICABLE_MARKER
        ) else None
        if line.startswith(markers.INAPPLICABLE_MARKER) and not stripped:
            out.add(current_lang)
    return out

