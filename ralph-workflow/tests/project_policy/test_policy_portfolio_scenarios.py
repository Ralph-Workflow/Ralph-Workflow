"""Black-box scenarios for the bounded policy portfolio."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, parse_portfolio_toml, preflight
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus

if TYPE_CHECKING:
    from ralph.project_policy.models import ReadinessResult

_EVIDENCE = (
    "artifact:evidence.json; immutable subject commit:abc123; run:run-42; "
    "predeclared public observation; validated oracle; independent verdict:PASS"
)


def _control(
    control_id: str,
    *,
    outcome: str,
    lane: str = "default",
    cost: float = 1.0,
    owner: str = "maintainers",
    disposition: str = "KEEP",
    evidence: str = _EVIDENCE,
) -> str:
    return f"""
[[controls]]
id = "{control_id}"
protected_outcome = "{outcome}"
fault_sensitivity = "distinct black-box failure"
layer = "public scenario"
owner = "{owner}"
trigger = "declared risk"
lane = "{lane}"
marginal_cost_seconds = {cost}
evidence = "{evidence}"
lifecycle = "review when risk or contract changes"
disposition = "{disposition}"
"""


def _manifest(
    *,
    context: str,
    controls: str,
    profiles: str = "",
    extra_lanes: str = "",
    kernel: str = '["public-contract"]',
    extra: str = "",
) -> str:
    return f"""
schema_version = "v1"
default_budget_seconds = 5.0
kernel_controls = {kernel}

[context]
{context}

[[lanes]]
id = "default"
owner = "maintainers"
trigger = "every change"

{extra_lanes}
{controls}
{profiles}

[local_tightening]
controls = []
{extra}
"""


def _run_ready(manifest: str) -> tuple[MemoryWorkspace, ReadinessResult]:
    workspace = MemoryWorkspace()
    seed_complete_corpus(workspace, portfolio=manifest)
    result = preflight.run_policy_readiness_preflight(
        workspace,
        ProjectStack(primary_language="Python"),
    )
    return workspace, result


def test_small_python_cli_reports_kernel_and_default_lane() -> None:
    manifest = _manifest(
        context=(
            'impact = "low"\nlikelihood = "low"\nuncertainty = "low"\n'
            'recoverability = "high"\narchitectures = ["library"]\n'
            'rate_of_change = "low"\nexpected_lifetime = "long"\nobligations = []'
        ),
        controls=_control("public-contract", outcome="library API remains stable"),
    )

    workspace, readiness = _run_ready(manifest)
    portfolio = parse_portfolio_toml(workspace.read(markers.PORTFOLIO_PATH))

    assert readiness.is_ready()
    assert portfolio.selected_profiles == ()
    assert portfolio.default_cost_seconds == 1.0
    assert portfolio.default_cost_seconds <= portfolio.default_budget_seconds
    assert portfolio.controls[0].disposition == "KEEP"
    assert "predeclared public observation" in portfolio.controls[0].evidence
    assert "independent verdict:PASS" in portfolio.controls[0].evidence


def test_medium_web_cli_reports_profile_replacement_and_owned_triggered_lane() -> None:
    lanes = """
[[lanes]]
id = "platform"
owner = "web platform team"
trigger = "browser or deployment-platform change"
"""
    controls = _control("public-contract", outcome="web API remains stable")
    controls += _control(
        "browser-contract",
        outcome="supported browsers render the public workflow",
        lane="platform",
        cost=7.0,
        owner="web platform team",
        disposition="REPLACE",
    )
    profiles = """
[[profiles]]
id = "web"
version = 1
controls = ["browser-contract"]
incompatible_with = []
[profiles.requires]
architectures = ["web"]
"""
    manifest = _manifest(
        context=(
            'impact = "medium"\nlikelihood = "medium"\nuncertainty = "medium"\n'
            'recoverability = "high"\narchitectures = ["web"]\n'
            'rate_of_change = "high"\nexpected_lifetime = "long"\n'
            'obligations = ["browser-support"]'
        ),
        controls=controls,
        profiles=profiles,
        extra_lanes=lanes,
    )

    workspace, readiness = _run_ready(manifest)
    portfolio = parse_portfolio_toml(workspace.read(markers.PORTFOLIO_PATH))
    browser = next(control for control in portfolio.controls if control.id == "browser-contract")
    platform = next(lane for lane in portfolio.lanes if lane.id == "platform")

    assert readiness.is_ready()
    assert portfolio.selected_profiles == ("web@1",)
    assert portfolio.default_cost_seconds == 1.0
    assert browser.disposition == "REPLACE"
    assert browser.lane == "platform"
    assert platform.owner == "web platform team"
    assert platform.trigger == "browser or deployment-platform change"
    assert "artifact:" in browser.evidence


def test_high_risk_service_cli_reports_durable_controls_human_lane_and_evidence_contract() -> None:
    lanes = """
[[lanes]]
id = "triggered"
owner = "security team"
trigger = "security or data-integrity change"

[[lanes]]
id = "human"
owner = "independent reviewer"
trigger = "judgment-dependent release"
"""
    controls = _control("public-contract", outcome="service API remains stable")
    controls += _control(
        "security-boundary",
        outcome="unauthorized access is rejected",
        lane="triggered",
        cost=12.0,
        owner="security team",
    )
    controls += _control(
        "data-integrity",
        outcome="committed records remain consistent",
        lane="triggered",
        cost=10.0,
        owner="data owner",
    )
    controls += _control(
        "release-review",
        outcome="regulated release evidence is independently judged",
        lane="human",
        cost=20.0,
        owner="independent reviewer",
    )
    profiles = """
[[profiles]]
id = "data-integrity"
version = 1
controls = ["data-integrity", "release-review"]
incompatible_with = []
[profiles.requires]
obligations = ["regulated"]

[[profiles]]
id = "security"
version = 1
controls = ["security-boundary"]
incompatible_with = []
[profiles.requires]
impact = ["high", "critical"]
"""
    manifest = _manifest(
        context=(
            'impact = "high"\nlikelihood = "medium"\nuncertainty = "high"\n'
            'recoverability = "low"\narchitectures = ["service"]\n'
            'rate_of_change = "medium"\nexpected_lifetime = "long"\n'
            'obligations = ["regulated"]'
        ),
        controls=controls,
        profiles=profiles,
        extra_lanes=lanes,
    )

    workspace, readiness = _run_ready(manifest)
    portfolio = parse_portfolio_toml(workspace.read(markers.PORTFOLIO_PATH))
    selected = {control.id: control for control in portfolio.controls}

    assert readiness.is_ready()
    assert portfolio.selected_profiles == ("data-integrity@1", "security@1")
    assert portfolio.default_cost_seconds == 1.0
    assert selected["security-boundary"].disposition == "KEEP"
    assert selected["data-integrity"].disposition == "KEEP"
    assert selected["release-review"].lane == "human"
    assert len(portfolio.controls) == 4
    assert all("validated oracle" in control.evidence for control in portfolio.controls)


Mutation = Callable[[str], str]


@pytest.mark.parametrize(
    ("case", "mutation", "diagnostic"),
    [
        (
            "quota inflation",
            lambda text: text.replace(
                'default_budget_seconds = 5.0',
                'coverage_target = 100\ndefault_budget_seconds = 5.0',
            ),
            "coverage_target",
        ),
        (
            "lane cost laundering",
            lambda text: text
            + _control(
                "renamed",
                outcome="library API remains stable",
                lane="triggered",
            ),
            "cost laundering",
        ),
        (
            "delete to green",
            lambda text: text
            + """
[[exceptions]]
id = "delete-kernel"
control = "public-contract"
owner = "maintainers"
reason = "make the gate green"
review_trigger = "later"
expires = "2099-01-01"
""",
            "cannot remove an invariant kernel control",
        ),
        (
            "renamed duplicate",
            lambda text: text
            + _control("alias-contract", outcome="library API remains stable"),
            "cost laundering",
        ),
        (
            "exception expiry bypass",
            lambda text: text
            + """
[[exceptions]]
id = "expired"
control = "optional"
owner = "maintainers"
reason = "temporary"
review_trigger = "incident"
expires = "2020-01-01"
""",
            "expired exception",
        ),
        (
            "incompatible inheritance",
            lambda text: text.replace(
                "[local_tightening]",
                """
[[profiles]]
id = "left"
version = 1
controls = []
incompatible_with = ["right@1"]
[profiles.requires]
impact = ["low"]

[[profiles]]
id = "right"
version = 1
controls = []
incompatible_with = ["left@1"]
[profiles.requires]
impact = ["low"]

[local_tightening]""",
            ),
            "incompatible profile",
        ),
        (
            "unowned one off",
            lambda text: text
            + _control(
                "one-off",
                outcome="one-off migration remains correct",
                lane="triggered",
                owner="",
            ),
            "owner",
        ),
        (
            "telemetry as proof",
            lambda text: text.replace(_EVIDENCE, "telemetry dashboard"),
            "inspectable evidence",
        ),
    ],
)
def test_adversarial_portfolios_fail_through_public_preflight(
    case: str,
    mutation: Mutation,
    diagnostic: str,
) -> None:
    lanes = """
[[lanes]]
id = "triggered"
owner = "domain owner"
trigger = "declared risk"
"""
    base = _manifest(
        context=(
            'impact = "low"\nlikelihood = "low"\nuncertainty = "low"\n'
            'recoverability = "high"\narchitectures = ["library"]\n'
            'rate_of_change = "low"\nexpected_lifetime = "long"\nobligations = []'
        ),
        controls=(
            _control("public-contract", outcome="library API remains stable")
            + _control(
                "optional",
                outcome="optional migration remains correct",
                lane="triggered",
            )
        ),
        profiles="""
[[profiles]]
id = "optional"
version = 1
controls = ["optional"]
incompatible_with = []
[profiles.requires]
impact = ["low"]
""",
        extra_lanes=lanes,
    )

    _workspace, readiness = _run_ready(mutation(base))
    portfolio_findings = [
        finding
        for finding in readiness.findings
        if finding.requirement_id == f"{markers.ID_PORTFOLIO}:invalid"
    ]

    assert readiness.requires_remediation(), case
    assert len(portfolio_findings) == 1, case
    assert diagnostic in portfolio_findings[0].missing_evidence, case
    assert "protected outcomes" in portfolio_findings[0].required_outcome, case
