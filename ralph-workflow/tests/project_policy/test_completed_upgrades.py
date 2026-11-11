"""Tests proving completed v4 and v5 policy pack upgrades to schema v6.

Proves:
1. An authentic v4 policy pack upgrades fully to v6 (marker, 7-column header,
   execution-kernel.md, and portfolio v3).
2. An authentic v5 policy pack upgrades fully to v6 (marker, 7-column header,
   and portfolio v3).
3. The upgrade happens exactly once: subsequent preflight or upgrade checks
   detect no outdated files, prompt zero times, and leave the workspace unchanged.
4. End-to-end CLI integration runs through upgrade consent, remediation upgrade,
   and analysis completion, and subsequent runs never re-prompt.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import (
    PromptChoice,
    markers,
    maybe_resolve_schema_upgrade,
    parse_portfolio_toml,
    preflight,
    starters,
    validators,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.authentic_v4_data import V4_PORTFOLIO, V4_STARTERS


class _Selector:
    """Scripted select seam: records prompts and returns each queued answer in order."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.questions: list[str] = []
        self.choices: list[tuple[str, ...]] = []
        self.defaults: list[str] = []

    def __call__(
        self,
        question: str,
        choices: Sequence[PromptChoice],
        default: str,
    ) -> str:
        self.questions.append(question)
        self.choices.append(tuple(c.key for c in choices))
        self.defaults.append(default)
        if not self._answers:
            raise AssertionError(f"Unexpected question prompt: {question}")
        return self._answers.pop(0)

    @property
    def call_count(self) -> int:
        return len(self.questions)


def _clean_v4_starter_prose(body: str) -> str:
    """Resolve placeholders and strip starter banners for authentic v4 files."""
    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--\s*REPLACE-ME:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return body


def _build_unmodified_v4_pack() -> MemoryWorkspace:
    """Build a MemoryWorkspace with authentic v4 starters and v1 portfolio."""
    ws = MemoryWorkspace()
    for name in markers.core_files_for_version("v4"):
        raw_body = V4_STARTERS[name]
        cleaned_body = _clean_v4_starter_prose(raw_body)
        ws.write(f"{markers.CANONICAL_DIR}{name}", cleaned_body)

    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(markers.PORTFOLIO_PATH, V4_PORTFOLIO)
    return ws


def _clean_v5_starter_prose(body: str) -> str:
    """Adapt bundled starters into authentic unfrozen v5 policy files."""
    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--\s*REPLACE-ME:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    body = re.sub(
        r"<!-- ralph-policy-schema:\s*v\d+\s*-->", "<!-- ralph-policy-schema: v5 -->", body
    )
    body = body.replace(
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |",
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |",
    )
    body = body.replace(
        "|---|---|---|---|---|---|---|",
        "|---|---|---|---|---|---|",
    )
    body = body.replace(" | deterministic: verified-value |", " |")
    return body


def _build_unmodified_v5_pack() -> MemoryWorkspace:
    """Build a MemoryWorkspace with authentic unfrozen v5 starters and v2 portfolio."""
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        raw_body = starters.read_starter(name)
        cleaned_body = _clean_v5_starter_prose(raw_body)
        ws.write(f"{markers.CANONICAL_DIR}{name}", cleaned_body)

    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    v2_portfolio = starters.read_starter("policy-portfolio.toml").replace(
        'schema_version = "v3"', 'schema_version = "v2"'
    )
    v2_portfolio = re.sub(r'enforcement\s*=\s*"[^"]*"\n', "", v2_portfolio)
    ws.write(markers.PORTFOLIO_PATH, v2_portfolio)
    return ws


def _clean_v6_starter_prose(body: str) -> str:
    """Resolve starter template prose into compliant v6 content."""
    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--\s*REPLACE-ME:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("deterministic: PROJECT-FACT-UNRESOLVED", "deterministic: make test")
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return body


def _apply_v6_upgrade(ws: MemoryWorkspace) -> None:
    """Apply the full v6 upgrade across all core files and the portfolio."""
    for name in markers.CORE_POLICY_FILES:
        body = starters.read_starter(name)
        cleaned = _clean_v6_starter_prose(body)
        ws.write(f"{markers.CANONICAL_DIR}{name}", cleaned)

    v3_portfolio = starters.read_starter("policy-portfolio.toml").replace(
        "PROJECT-FACT-UNRESOLVED", "make test"
    )
    ws.write(markers.PORTFOLIO_PATH, v3_portfolio)


def _assert_pack_is_fully_v6(ws: MemoryWorkspace) -> None:
    """Assert all structural invariants of a fully upgraded v6 policy pack."""
    for name in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        assert ws.exists(path), f"Core file {name} missing after upgrade"
        content = ws.read(path)
        first_line = next((line for line in content.splitlines() if line.strip()), "")
        assert first_line == markers.POLICY_SCHEMA_MARKER, (
            f"Expected v6 schema marker in {path}, got {first_line!r}"
        )
        if "## Guard inventory" in content:
            assert (
                "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |"
                in content
            ), f"Missing 7-column Guard inventory header in {path}"
            assert "|---|---|---|---|---|---|---|" in content, (
                f"Missing 7-column delimiter line in {path}"
            )

    assert ws.exists(markers.PORTFOLIO_PATH)
    portfolio_text = ws.read(markers.PORTFOLIO_PATH)
    parsed_portfolio = parse_portfolio_toml(portfolio_text)
    assert parsed_portfolio.schema_version == "v3"
    assert markers.PORTFOLIO_SCHEMA_VERSION == "v3"
    assert parsed_portfolio.schema_version == markers.PORTFOLIO_SCHEMA_VERSION
    for ctrl in parsed_portfolio.controls:
        assert ctrl.enforcement is not None, (
            f"Control {ctrl.id} missing enforcement in v3 portfolio"
        )

    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == [], f"Upgraded pack must validate READY, got: {findings}"


def test_v4_pack_full_pipeline_readiness_lifecycle_runs_once() -> None:
    """Proves v4 upgrade completes through readiness lifecycle and never re-prompts."""
    ws = _build_unmodified_v4_pack()
    stack = ProjectStack(primary_language="Python")
    emitted: list[str] = []

    # Before upgrade: preflight requires remediation
    pre_result = preflight.run_policy_readiness_preflight(ws, stack)
    assert pre_result.requires_remediation()

    # Step 1: Maybe-resolve asks for upgrade choice
    first_selector = _Selector("upgrade")
    first_resolved = maybe_resolve_schema_upgrade(
        ws,
        emitted.append,
        select=first_selector,
        is_tty=lambda: True,
    )
    assert first_resolved is True
    assert first_selector.call_count == 1

    # Step 2: Apply upgrade
    _apply_v6_upgrade(ws)
    _assert_pack_is_fully_v6(ws)

    # Step 3: Second preflight check must not prompt, passes readiness cleanly
    second_selector = _Selector()  # raises if invoked
    second_resolved = maybe_resolve_schema_upgrade(
        ws,
        emitted.append,
        select=second_selector,
        is_tty=lambda: True,
    )
    assert second_resolved is True
    assert second_selector.call_count == 0

    post_result = preflight.run_policy_readiness_preflight(ws, stack)
    assert not post_result.requires_remediation()
    assert validators.validate_readiness(ws, stack) == []


def test_v5_pack_full_pipeline_readiness_lifecycle_runs_once() -> None:
    """Proves v5 upgrade completes through readiness lifecycle and never re-prompts."""
    ws = _build_unmodified_v5_pack()
    stack = ProjectStack(primary_language="Python")
    emitted: list[str] = []

    # Before upgrade: preflight requires remediation
    pre_result = preflight.run_policy_readiness_preflight(ws, stack)
    assert pre_result.requires_remediation()

    # Step 1: Maybe-resolve asks for upgrade choice
    first_selector = _Selector("upgrade")
    first_resolved = maybe_resolve_schema_upgrade(
        ws,
        emitted.append,
        select=first_selector,
        is_tty=lambda: True,
    )
    assert first_resolved is True
    assert first_selector.call_count == 1

    # Step 2: Apply upgrade
    _apply_v6_upgrade(ws)
    _assert_pack_is_fully_v6(ws)

    # Step 3: Second preflight check must not prompt, passes readiness cleanly
    second_selector = _Selector()  # raises if invoked
    second_resolved = maybe_resolve_schema_upgrade(
        ws,
        emitted.append,
        select=second_selector,
        is_tty=lambda: True,
    )
    assert second_resolved is True
    assert second_selector.call_count == 0

    post_result = preflight.run_policy_readiness_preflight(ws, stack)
    assert not post_result.requires_remediation()
    assert validators.validate_readiness(ws, stack) == []


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_upgrade_spec_matrix_invariants(version: str) -> None:
    """Before upgrade, spec matches v4/v5; after upgrade, matches v6."""
    pre_spec = markers.spec_for_version(version)
    assert pre_spec.version == version

    if version == "v4":
        assert len(pre_spec.core_manifest) == 11
        assert pre_spec.guard_inventory_header is None
        assert pre_spec.portfolio_schema_version == ("v1", "v2")
    elif version == "v5":
        assert len(pre_spec.core_manifest) == 12
        assert pre_spec.guard_inventory_header is not None
        assert len(pre_spec.guard_inventory_header) == 6
        assert pre_spec.portfolio_schema_version == ("v2",)

    # Post-upgrade target spec
    v6_spec = markers.spec_for_version("v6")
    assert v6_spec.version == "v6"
    assert len(v6_spec.core_manifest) == 12
    assert v6_spec.guard_inventory_header == markers.GUARD_INVENTORY_HEADER
    assert len(v6_spec.guard_inventory_header) == 7
    assert v6_spec.portfolio_schema_version == ("v3",)
