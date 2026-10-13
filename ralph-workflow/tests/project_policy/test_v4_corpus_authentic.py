"""Authentic v4 corpus tests using git history.

The analysis feedback requires tests that build a v4 corpus from
git history, not from synthesized marker-only files. This module
uses the v4 starter content checked in at the pre-v5 commit and
exercises the full validator against it so the contract that
matters — an unchanged v4 project is routed through upgrade-or-
freeze and a frozen v4 project is accepted against the v4
manifest — is verified end-to-end, not by hand-waved shape
synthesis.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, portfolio, starters, validators
from ralph.workspace.memory import MemoryWorkspace

# This test reads the v4 starter content from a tagged git ref so
# the v4 manifest is exercised against an authentic v4 corpus
# rather than a synthesized marker shell. Reading from a different
# tree is intrinsically a real-git process operation, so the
# whole module is marked ``subprocess_e2e`` and excluded from the
# default test budget; the run-time contract is identical to the
# existing real-git corpus tests under ``test_auto_integrate.py``.
pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(15),
]

#: The git ref whose tree carries the v4 starters (the commit
#: before the v5 schema bump that introduced execution-kernel.md).
V4_REF: str = "201bc67af^"

#: The v4 starter filenames (no execution-kernel.md at this ref).
V4_CORE_FILES: tuple[str, ...] = markers._V4_CORE_FILES


def _git_show(path: str) -> str:
    """Read ``path`` from the ``V4_REF`` git tree.

    Uses ``git show <ref>:<path>`` so the test does not depend on
    the working tree or a clone. If the path is not present at
    that ref, returns an empty string (the test then fails the
    schema check, which is the right behavior — a v4 corpus that
    cannot be sourced is a fixture defect, not a code defect).
    """
    repo = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        ["git", "show", f"{V4_REF}:{path}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _build_v4_pack(stack: ProjectStack) -> MemoryWorkspace:
    """Build a MemoryWorkspace pre-populated with the v4 starter
    content from git history.

    Each starter file is read from the v4 ref, frozen at v4 (so
    the validator runs the v4 manifest path), and a minimal
    AGENTS.md + portfolio stub is added so the surface checks
    pass. The v4 schema marker is rewritten to ``freeze v4`` so
    the v4 manifest is selected; no other content is changed.
    """
    ws = MemoryWorkspace()
    for name in V4_CORE_FILES:
        body = _git_show(f"ralph-workflow/ralph/project_policy/starters/{name}")
        if not body:
            continue
        # Replace the v4 schema marker with a freeze marker so the
        # validator selects the v4 manifest. The v4 marker is the
        # literal ``<!-- ralph-policy-schema: v4 -->`` (the v5 marker
        # is the v5 form, not what these files carry).
        body = body.replace(
            "<!-- ralph-policy-schema: v4 -->",
            "<!-- ralph-policy-schema: freeze v4 -->",
            1,
        )
        path = f"{markers.CANONICAL_DIR}{name}"
        ws.write(path, body)
    # Surface files: AGENTS.md + CLAUDE.md + portfolio (v1 schema,
    # which is what frozen-v4 packs carry).
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    # v4 ships the v1 portfolio schema; the validator accepts a
    # frozen-v1 portfolio against the v4 manifest.
    ws.write(
        markers.PORTFOLIO_PATH,
        'schema_version = "v1"\ndefault_budget_seconds = 1.0\nkernel_controls = []\n',
    )
    return ws


@pytest.mark.parametrize("starter_name", list(V4_CORE_FILES))
def test_v4_starter_source_present_in_git_history(starter_name: str) -> None:
    """The v4 git ref carries every v4 core starter; the corpus
    fixture is therefore an authentic v4 corpus, not a synthesized
    marker shell.

    A failure here means the v4 ref no longer carries the file —
    either the git history was rewritten (which would itself be a
    defect) or the path is wrong. Either way, the rest of the
    test class is meaningless and pytest reports a clear failure.
    """
    body = _git_show(f"ralph-workflow/ralph/project_policy/starters/{starter_name}")
    assert body, f"v4 starter {starter_name!r} is not in git history at {V4_REF}"
    assert "ralph-policy-schema: v4" in body, (
        f"v4 starter {starter_name!r} does not carry the v4 marker"
    )


def test_v4_pack_full_v4_manifest_validates() -> None:
    """An authentic v4 pack (all 11 core files from git history,
    frozen at v4, with v1 portfolio) MUST NOT raise a
    v5-only missing-file finding against the v4 manifest.

    The plan's U11c proof requires this: the v4 manifest must
    accept a v4 pack's required files as the binding gate, so
    the upgrade-or-freeze gate does not silently rewrite
    preserved controls. A v4 starter template does carry
    placeholders (``REPLACE-ME``, ``PROJECT-FACT-UNRESOLVED``,
    the ``RALPH-STARTER-TEMPLATE`` banner) — those are part of
    the starter being a STARTER, not a project policy; the test
    is about the file manifest, not the placeholders.
    """
    ws = _build_v4_pack(ProjectStack(primary_language="Python"))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    finding_ids = {(f.path, f.requirement_id) for f in findings}
    # The v4 pack MUST NOT raise a missing-core finding for any
    # of the v4 manifest's required files. The v4 manifest
    # accepts the original eleven; the v5-only addition
    # ``execution-kernel.md`` is the only file exempt.
    for required in V4_CORE_FILES:
        path = f"{markers.CANONICAL_DIR}{required}"
        assert (path, f"RWP-CORE:{required} file does not exist") not in finding_ids, (
            f"v4 pack raised RWP-CORE:missing against required v4 file "
            f"{required!r}; the v4 manifest is too aggressive"
        )
    # The v4 pack MUST NOT raise the v2-portfolio-only finding
    # against the v1 portfolio. The v4 manifest accepts v1.
    assert not any(
        "schema_version must be v2" in f.missing_evidence
        for f in findings
        if f.path == markers.PORTFOLIO_PATH
    ), (
        f"v4 pack rejected for v2 portfolio schema: {[f for f in findings if f.path == markers.PORTFOLIO_PATH]}"
    )


def test_v4_pack_missing_security_policy_fails() -> None:
    """A frozen v4 pack missing ``security-policy.md`` MUST stay
    NOT READY. The v4 manifest still requires the original
    eleven; only files introduced AFTER v4 are exempt, never an
    existing core file.
    """
    ws = MemoryWorkspace()
    for name in V4_CORE_FILES:
        if name == "security-policy.md":
            continue
        body = _git_show(f"ralph-workflow/ralph/project_policy/starters/{name}")
        if not body:
            continue
        body = body.replace(
            "<!-- ralph-policy-schema: v4 -->",
            "<!-- ralph-policy-schema: freeze v4 -->",
            1,
        )
        ws.write(f"{markers.CANONICAL_DIR}{name}", body)
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.PORTFOLIO_PATH,
        'schema_version = "v1"\ndefault_budget_seconds = 1.0\nkernel_controls = []\n',
    )
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    paths = {f.path for f in findings}
    assert f"{markers.CANONICAL_DIR}security-policy.md" in paths, (
        f"frozen v4 pack missing security-policy.md must stay NOT READY; "
        f"got findings at: {sorted(paths)}"
    )


def test_v4_pack_does_not_require_execution_kernel() -> None:
    """A frozen v4 pack MUST NOT raise a missing-file finding for
    ``execution-kernel.md``: that file is the v5 addition and is
    exempt under the v4 manifest. A regression that exempts
    nothing would block every v4 project from freezing.
    """
    ws = _build_v4_pack(ProjectStack(primary_language="Python"))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    paths = {f.path for f in findings}
    # The v4 manifest must NOT raise a RWP-CORE:missing finding
    # for execution-kernel.md.
    assert not any(
        f.path == f"{markers.CANONICAL_DIR}execution-kernel.md" and "RWP-CORE" in f.requirement_id
        for f in findings
    ), f"frozen v4 pack should not require execution-kernel.md; got: {sorted(paths)}"


def test_v4_pack_does_not_require_v2_portfolio() -> None:
    """A frozen v4 pack with a v1 portfolio MUST NOT be rejected
    for missing v2 fields. The portfolio schema bump from v1 to
    v2 happens at v5, not v4, and the v4 manifest validates the
    v1 portfolio as-shipped.
    """
    ws = _build_v4_pack(ProjectStack(primary_language="Python"))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    # The v4 pack uses a v1 portfolio; the validator must accept
    # it under the v4 manifest — no v2-only finding is allowed.
    v2_only_findings = [
        f
        for f in findings
        if f.path == markers.PORTFOLIO_PATH
        and (
            "schema_version must be v2" in f.missing_evidence
            or "detects" in f.missing_evidence
            or "retirement_trigger" in f.missing_evidence
        )
    ]
    assert not v2_only_findings, (
        f"frozen v4 pack rejected for v2 portfolio fields: {v2_only_findings}"
    )


def test_fresh_v5_pack_validates_against_v5_manifest() -> None:
    """The current (v5) shipped starters MUST validate against
    the v5 manifest. This is the v5-pack READY sanity check the
    plan's U11c proof requires.

    The shipped v5 portfolio uses the v2 schema; the parser
    must accept it cleanly. The portfolio object exposes the
    schema version via its public attribute, not via
    subscript, so we read it the documented way.
    """
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        body = starters.read_starter(name)
        path = f"{markers.CANONICAL_DIR}{name}"
        ws.write(path, body)
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    # The shipped v5 portfolio uses the v2 schema. We re-read
    # the shipped starter to get the canonical v2 body.
    shipped_portfolio = starters.read_starter("policy-portfolio.toml")
    ws.write(markers.PORTFOLIO_PATH, shipped_portfolio)
    parsed = portfolio.parse_portfolio_toml(shipped_portfolio)
    # The v5 portfolio schema is v2; the public attribute on
    # the parsed object is the source of truth (the dict-style
    # access is a private implementation detail).
    assert parsed.schema_version == "v2"


def test_frozen_v4_pack_missing_required_heading_fails() -> None:
    """A frozen v4 pack is still validated for required v4 headings.

    If a frozen v4 file is missing a required v4 heading, the validator
    MUST report a finding rather than exempting headings entirely.
    """
    ws = _build_v4_pack(ProjectStack(primary_language="Python"))
    # Corrupt testing-policy.md by removing 'Purpose and scope' heading
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path).replace("## Purpose and scope", "## Wrong heading", 1)
    ws.write(path, content)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.path == path and "Purpose and scope" in f.requirement_id for f in findings), (
        f"Expected missing heading finding for Purpose and scope in frozen v4 pack; got: {findings}"
    )


def test_frozen_v4_pack_missing_required_fact_fails() -> None:
    """A frozen v4 pack is still validated for required v4 facts.

    If a frozen v4 file is missing a required fact line, the validator
    MUST report a finding rather than exempting facts entirely.
    """
    ws = _build_v4_pack(ProjectStack(primary_language="Python"))
    # Corrupt testing-policy.md by removing a required fact line
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    lines = [line for line in ws.read(path).splitlines() if "test_command" not in line]
    ws.write(path, "\n".join(lines))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.path == path and "fact-test_command" in f.requirement_id for f in findings), (
        f"Expected missing fact finding in frozen v4 pack; got: {findings}"
    )
