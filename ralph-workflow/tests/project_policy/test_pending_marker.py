from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import _scanners, markers, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.test_validator import (
    _complete_policy_body,
    _seed_agents_md,
    _seed_all_core_complete,
    _seed_claude_md,
)

_SEC_PATH = f"{markers.CANONICAL_DIR}security-policy.md"
_TEST_PATH = f"{markers.CANONICAL_DIR}testing-policy.md"


def _pendings(values: list[str], filename: str) -> list[str]:
    findings = _scanners._check_individual_pendings(
        values, f"{markers.CANONICAL_DIR}{filename}", filename
    )
    return [f.requirement_id for f in findings]


def test_valid_pending_gate_produces_no_finding() -> None:
    assert (
        _pendings(
            ["pytest (assumed 2026-07-12); review trigger: once test deps are installed"],
            "security-policy.md",
        )
        == []
    )


def test_pending_gate_unapproved_tool_is_flagged() -> None:
    ids = _pendings(
        ["definitely-not-a-tool (assumed 2026-07-12); review trigger: later on"],
        "security-policy.md",
    )
    assert any("unapproved" in rid for rid in ids)


def test_pending_gate_without_assumed_date_is_flagged() -> None:
    ids = _pendings(["make check; review trigger: later on"], "security-policy.md")
    assert any("undated" in rid for rid in ids)
    assert not any("unapproved" in rid for rid in ids), ids
    assert not any("no-trigger" in rid for rid in ids), ids


def test_pending_gate_with_impossible_calendar_date_is_flagged() -> None:
    ids = _pendings(
        ["pytest (assumed 2026-99-99); review trigger: once test deps are installed"],
        "security-policy.md",
    )

    assert any("invalid-date" in rid for rid in ids), ids


def test_pending_gate_rejects_repo_local_script_outside_fixed_allowlist() -> None:
    ids = _pendings(
        ["./scripts/check.sh (assumed 2026-07-12); review trigger: once installed"],
        "security-policy.md",
    )

    assert any("unapproved" in rid for rid in ids), ids


def test_pending_gate_without_review_trigger_is_flagged() -> None:
    ids = _pendings(["pytest (assumed 2026-07-12)"], "security-policy.md")
    assert any("no-trigger" in rid for rid in ids)


def test_pending_gate_with_placeholder_is_flagged() -> None:
    ids = _pendings(
        ["pytest (assumed <date>); review trigger: <trigger>"],
        "security-policy.md",
    )
    assert any("placeholder" in rid for rid in ids)


def test_empty_pending_gate_is_flagged() -> None:
    ids = _pendings([""], "security-policy.md")
    assert any("empty" in rid for rid in ids)


def test_pending_gate_allowed_on_testing_policy() -> None:
    assert (
        _pendings(
            ["pytest (assumed 2026-07-12); review trigger: once test deps are installed"],
            "testing-policy.md",
        )
        == []
    )


def test_valid_pending_fact_produces_no_finding() -> None:
    content = (
        "RALPH-FACT: secret_scan_command: RALPH-PENDING (assumed 2026-07-12); "
        "review trigger: once a scanner is chosen"
    )
    assert _scanners._check_pending_facts(content, _SEC_PATH, "security-policy.md") == []


def test_pending_fact_without_review_trigger_is_flagged() -> None:
    content = "RALPH-FACT: secret_scan_command: RALPH-PENDING (assumed 2026-07-12)"
    findings = _scanners._check_pending_facts(content, _SEC_PATH, "security-policy.md")
    assert any("fact-no-trigger" in f.requirement_id for f in findings)


def test_pending_fact_without_assumed_date_is_flagged() -> None:
    content = "RALPH-FACT: secret_scan_command: RALPH-PENDING review trigger: later"
    findings = _scanners._check_pending_facts(content, _SEC_PATH, "security-policy.md")
    assert any("fact-undated" in f.requirement_id for f in findings)


def test_pending_fact_near_miss_sentinel_is_flagged() -> None:
    content = (
        "RALPH-FACT: secret_scan_command: RALPH-PENDINGLY "
        "(assumed 2026-07-12); review trigger: once a scanner is chosen"
    )

    findings = _scanners._check_pending_facts(content, _SEC_PATH, "security-policy.md")

    assert any("fact-invalid-sentinel" in f.requirement_id for f in findings)


def test_full_testing_policy_with_pending_gate_validates_clean() -> None:
    body = (
        _complete_policy_body(filename="testing-policy.md")
        .replace(
            "RALPH-COMMAND: make test",
            "RALPH-PENDING: pytest (assumed 2026-07-12); review trigger: once test deps are installed",
        )
        .replace(
            "deterministic: make test",
            "advisory: pending test deps; review: once test deps are installed",
        )
    )
    ws = MemoryWorkspace()
    ws.write(_TEST_PATH, body)
    assert validators._check_policy_file(ws, _TEST_PATH, "testing-policy.md") == []


def test_full_security_policy_with_pending_fact_validates_clean() -> None:
    body = _complete_policy_body(filename="security-policy.md").replace(
        ": verified-value",
        ": RALPH-PENDING (assumed 2026-07-12); review trigger: once chosen",
        1,
    )
    ws = MemoryWorkspace()
    ws.write(_SEC_PATH, body)
    assert validators._check_policy_file(ws, _SEC_PATH, "security-policy.md") == []


def test_malformed_pending_gate_blocks_through_full_validator() -> None:
    body = _complete_policy_body(filename="architecture-policy.md").replace(
        "RALPH-COMMAND: make test",
        "RALPH-PENDING: pytest (assumed 2026-07-12)",
    )
    path = f"{markers.CANONICAL_DIR}architecture-policy.md"
    ws = MemoryWorkspace()
    ws.write(path, body)
    ids = [
        f.requirement_id for f in validators._check_policy_file(ws, path, "architecture-policy.md")
    ]
    assert any("no-trigger" in rid for rid in ids), ids


def test_malformed_pending_fact_blocks_through_full_validator() -> None:
    body = _complete_policy_body(filename="security-policy.md").replace(
        ": verified-value",
        ": RALPH-PENDING (assumed 2026-07-12)",
        1,
    )
    ws = MemoryWorkspace()
    ws.write(_SEC_PATH, body)
    ids = [
        f.requirement_id for f in validators._check_policy_file(ws, _SEC_PATH, "security-policy.md")
    ]
    assert any("fact-no-trigger" in rid for rid in ids), ids


def test_fully_pending_project_reaches_ready_without_remediation() -> None:
    stack = ProjectStack(primary_language="Python")
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, stack)

    test_body = _complete_policy_body(filename="testing-policy.md").replace(
        "RALPH-COMMAND: make test",
        "RALPH-PENDING: pytest (assumed 2026-07-12); review trigger: once test deps are installed",
    )
    ws.write(_TEST_PATH, test_body)

    sec_body = _complete_policy_body(filename="security-policy.md").replace(
        ": verified-value",
        ": RALPH-PENDING (assumed 2026-07-12); review trigger: once chosen",
        1,
    )
    ws.write(_SEC_PATH, sec_body)

    assert validators.validate_readiness(ws, stack) == []
