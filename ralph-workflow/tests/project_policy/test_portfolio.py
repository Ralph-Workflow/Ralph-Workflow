"""Black-box contract tests for deterministic project-policy portfolios."""

from __future__ import annotations

from datetime import date

import pytest

from ralph.project_policy import PortfolioError, parse_portfolio_toml


def _control(
    control_id: str,
    *,
    outcome: str | None = None,
    lane: str = "default",
    cost: float = 1.0,
    owner: str = "quality",
    disposition: str = "KEEP",
) -> str:
    protected = outcome or f"{control_id} outcome"
    return f"""
[[controls]]
id = "{control_id}"
protected_outcome = "{protected}"
fault_sensitivity = "{control_id} distinct failure"
layer = "unit"
owner = "{owner}"
trigger = "every change"
lane = "{lane}"
marginal_cost_seconds = {cost}
evidence = "artifact:{control_id}"
lifecycle = "review when protected outcome changes"
disposition = "{disposition}"
"""


def _manifest(
    *,
    controls: str,
    kernel: tuple[str, ...] = ("kernel",),
    profiles: str = "",
    context: str = "",
    extra: str = "",
) -> str:
    kernel_literal = ", ".join(f'"{item}"' for item in kernel)
    return f"""
schema_version = "v1"
default_budget_seconds = 3.0
kernel_controls = [{kernel_literal}]

[context]
impact = "low"
likelihood = "low"
uncertainty = "low"
recoverability = "high"
architectures = ["library"]
obligations = []
{context}

[[lanes]]
id = "default"
owner = "quality"
trigger = "every change"

[[lanes]]
id = "human"
owner = "security"
trigger = "security-sensitive change"

{controls}
{profiles}
{extra}
"""


def test_composition_is_stable_independent_of_declaration_order() -> None:
    first = _manifest(
        controls=_control("kernel-a") + _control("kernel-b"),
        kernel=("kernel-b", "kernel-a"),
    )
    second = _manifest(
        controls=_control("kernel-b") + _control("kernel-a"),
        kernel=("kernel-a", "kernel-b"),
    )

    left = parse_portfolio_toml(first, today=date(2026, 9, 11))
    right = parse_portfolio_toml(second, today=date(2026, 9, 11))

    assert left == right
    assert [control.id for control in left.controls] == ["kernel-a", "kernel-b"]
    assert left.default_cost_seconds == 2.0


def test_profiles_are_selected_from_risk_and_obligations_not_size_or_counts() -> None:
    profiles = """
[[profiles]]
id = "security"
version = 1
requires = { impact = ["high"], obligations = ["pii"] }
controls = ["security-boundary"]
"""
    text = _manifest(
        controls=_control("kernel") + _control("security-boundary", lane="human"),
        profiles=profiles,
    ).replace('impact = "low"', 'impact = "high"').replace(
        "obligations = []", 'obligations = ["pii"]'
    )

    portfolio = parse_portfolio_toml(text, today=date(2026, 9, 11))

    assert portfolio.selected_profiles == ("security@1",)
    assert [control.id for control in portfolio.controls] == [
        "kernel",
        "security-boundary",
    ]

    with pytest.raises(PortfolioError, match="repository_size"):
        parse_portfolio_toml(
            text.replace('impact = "high"', 'impact = "high"\nrepository_size = 20'),
            today=date(2026, 9, 11),
        )


def test_local_tightening_is_composed_after_selected_profiles() -> None:
    extra = """
[local_tightening]
controls = ["local-contract"]
"""
    portfolio = parse_portfolio_toml(
        _manifest(
            controls=_control("kernel") + _control("local-contract", lane="human"),
            extra=extra,
        ),
        today=date(2026, 9, 11),
    )

    assert [control.id for control in portfolio.controls] == [
        "kernel",
        "local-contract",
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_control("kernel"), "duplicate control"),
        (
            """
[[profiles]]
id = "web"
version = 1
requires = { architectures = ["library"] }
controls = ["kernel"]
incompatible_with = ["web@1"]
""",
            "incompatible profile",
        ),
    ],
)
def test_duplicates_and_incompatible_versions_fail_closed(
    mutation: str, message: str
) -> None:
    with pytest.raises(PortfolioError, match=message):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel") + mutation),
            today=date(2026, 9, 11),
        )


def test_ambiguous_ownership_and_unknown_lane_fail_closed() -> None:
    with pytest.raises(PortfolioError, match="owner"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel", owner="")),
            today=date(2026, 9, 11),
        )
    with pytest.raises(PortfolioError, match="unknown lane"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel", lane="elsewhere")),
            today=date(2026, 9, 11),
        )


def test_expired_and_broad_exceptions_fail_closed() -> None:
    expired = """
[[exceptions]]
id = "temporary"
control = "kernel"
owner = "quality"
reason = "vendor outage"
review_trigger = "vendor recovery"
expires = "2026-09-10"
"""
    broad = expired.replace('control = "kernel"', 'control = "*"').replace(
        "2026-09-10", "2026-09-12"
    )

    with pytest.raises(PortfolioError, match="expired"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel"), extra=expired),
            today=date(2026, 9, 11),
        )
    with pytest.raises(PortfolioError, match="one exact control"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel"), extra=broad),
            today=date(2026, 9, 11),
        )


def test_narrow_current_exception_removes_exact_control() -> None:
    exception = """
[[exceptions]]
id = "temporary"
control = "optional"
owner = "quality"
reason = "vendor outage"
review_trigger = "vendor recovery"
expires = "2026-09-12"
"""
    portfolio = parse_portfolio_toml(
        _manifest(
            controls=_control("kernel") + _control("optional"),
            extra='[local_tightening]\ncontrols = ["optional"]\n' + exception,
        ),
        today=date(2026, 9, 11),
    )

    assert [control.id for control in portfolio.controls] == ["kernel"]
    assert portfolio.applied_exceptions == ("temporary",)


def test_default_budget_overflow_fails_closed() -> None:
    with pytest.raises(PortfolioError, match="default budget"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel", cost=3.1)),
            today=date(2026, 9, 11),
        )


def test_relabeled_duplicate_cost_in_another_lane_fails_closed() -> None:
    duplicate = _control(
        "renamed",
        outcome="same protected outcome",
        lane="human",
        owner="security",
    )
    original = _control("original", outcome="same protected outcome")

    with pytest.raises(PortfolioError, match="cost laundering"):
        parse_portfolio_toml(
            _manifest(controls=original + duplicate),
            today=date(2026, 9, 11),
        )


def test_input_and_diagnostics_are_bounded() -> None:
    too_many = "".join(_control(f"c-{number}") for number in range(65))
    with pytest.raises(PortfolioError, match="at most 64 controls") as exc_info:
        parse_portfolio_toml(
            _manifest(controls=too_many),
            today=date(2026, 9, 11),
        )

    assert len(str(exc_info.value)) <= 500


def test_unknown_fields_and_invalid_evidence_fail_closed() -> None:
    with pytest.raises(PortfolioError, match="unknown root field"):
        parse_portfolio_toml(
            _manifest(controls=_control("kernel")).replace(
                '[context]', 'repository_size = 3\n\n[context]'
            ),
            today=date(2026, 9, 11),
        )
    with pytest.raises(PortfolioError, match="inspectable evidence"):
        parse_portfolio_toml(
            _manifest(
                controls=_control("kernel").replace(
                    'evidence = "artifact:kernel"', 'evidence = "process exit 0"'
                )
            ),
            today=date(2026, 9, 11),
        )
