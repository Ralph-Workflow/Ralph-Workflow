from __future__ import annotations

import re
from datetime import date
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy._content_checks import get_research_freshness_clock
from ralph.project_policy._findings import PolicyFinding

TIME_BUDGET_POLICY_FILES: Final[frozenset[str]] = frozenset(
    {"verification-policy.md", "testing-policy.md"}
)

OVERAGE_FACT_KEY: Final[str] = "verification_budget_overage"

_VERIFICATION_POLICY: Final[str] = "verification-policy.md"

_TESTING_POLICY: Final[str] = "testing-policy.md"

_TIMEOUT_FACT_KEY: Final[str] = "timeout_enforcement_mechanism"

_TIMEOUT_LINE_RE: Final[re.Pattern[str]] = re.compile(
    rf"{re.escape(markers.FACT_MARKER)}\s*{_TIMEOUT_FACT_KEY}:\s*(?P<value>.*)$",
    re.MULTILINE,
)

_NO_TIMEOUT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:none|n/?a|not (?:configured|installed|enforced)|absent|missing)\b",
    re.IGNORECASE,
)

_FIXED_CAP_RE: Final[re.Pattern[str]] = re.compile(
    r"hard cap:? (?:(?:30|60) seconds|2 minutes)(?! above)"
    r"|startup alone exceeding the target is a defect to fix",
    re.IGNORECASE,
)

_OVERAGE_LINE_RE: Final[re.Pattern[str]] = re.compile(
    rf"{re.escape(markers.FACT_MARKER)}\s*{OVERAGE_FACT_KEY}:\s*(?P<value>.*)$"
)

_OVERAGE_NONE_RE: Final[re.Pattern[str]] = re.compile(r"none(?:\W.*)?")

_OVERAGE_RECORD_RE: Final[re.Pattern[str]] = re.compile(
    r"excess: (?P<excess>.+?); owner: (?P<owner>.+?); plan: (?P<plan>.+); "
    r"review: (?P<review>\d{4}-\d{2}-\d{2})"
)

_OVERAGE_FORM: Final[str] = (
    "`none`, or `excess: <seconds over each cap>; owner: <person or team>; "
    "plan: <reductions applied and remaining>; review: <YYYY-MM-DD>`"
)

_REQUIRED_OUTCOME: Final[str] = (
    "replace every fixed universal time cap with the bundled starter's measured "
    "cost-floor contract (verification-policy.md rules 9, 16 and 21; "
    "testing-policy.md rule 9): caps bind only the time above the project's "
    "measured cost floor, and the policy records `RALPH-FACT: "
    "verification_cost_floor`, `RALPH-FACT: fast_path_time_budget`, and "
    "`RALPH-FACT: verification_budget_overage` from measurement, never from a cap; "
    "a figure still over its cap after every achievable optimization is recorded "
    "as measured and its excess is tracked in `verification_budget_overage`"
)


def _normalized(content: str) -> str:
    return " ".join(content.replace("*", "").split())


def _fixed_cap_findings(content: str, path: str, filename: str) -> list[PolicyFinding]:
    matches = [m.group(0) for m in _FIXED_CAP_RE.finditer(_normalized(content))]
    if not matches:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_TIME_BUDGET}:{filename}:fixed-cap",
            path=path,
            missing_evidence=(
                "fixed universal time cap wording that ignores the project's measured "
                f"cost floor: {'; '.join(sorted(set(matches)))}"
            ),
            required_outcome=_REQUIRED_OUTCOME,
        )
    ]


def _overage_values(content: str) -> list[str]:
    return [
        str(match.group("value")).strip()
        for line in content.splitlines()
        if (match := _OVERAGE_LINE_RE.match(line.strip())) is not None
    ]


def _is_deferred_or_placeholder(value: str) -> bool:
    return value.startswith("RALPH-PENDING") or any(
        token in value for token in markers.PLACEHOLDER_TOKENS
    )


def _review_date(value: str) -> date | None:
    record = _OVERAGE_RECORD_RE.fullmatch(value)
    if record is None:
        return None
    if not all(str(record.group(field)).strip() for field in ("excess", "owner", "plan")):
        return None
    try:
        return date.fromisoformat(str(record.group("review")))
    except ValueError:
        return None


def _overage_finding(path: str, suffix: str, evidence: str, outcome: str) -> PolicyFinding:
    return PolicyFinding(
        requirement_id=f"{markers.ID_TIME_BUDGET}:{_VERIFICATION_POLICY}:{suffix}",
        path=path,
        missing_evidence=evidence,
        required_outcome=outcome,
    )


def _overage_findings(content: str, path: str) -> list[PolicyFinding]:
    values = _overage_values(content)
    if len(values) != 1:
        return []
    value = values[0]
    if _is_deferred_or_placeholder(value) or _OVERAGE_NONE_RE.fullmatch(value):
        return []
    review = _review_date(value)
    if review is None:
        return [
            _overage_finding(
                path,
                "overage-malformed",
                f"`{OVERAGE_FACT_KEY}` is not in the required form: {value}",
                f"record `{OVERAGE_FACT_KEY}` as {_OVERAGE_FORM}, every field non-empty",
            )
        ]
    if review < get_research_freshness_clock()():
        return [
            _overage_finding(
                path,
                "overage-review-overdue",
                f"`{OVERAGE_FACT_KEY}` review date {review.isoformat()} has passed",
                "optimize again, re-measure, and record an excess no higher than the "
                "current one with a new review date, or `none` once every figure is "
                "within its cap",
            )
        ]
    return []


def _timeout_enforcement_findings(content: str, path: str) -> list[PolicyFinding]:
    for match in _TIMEOUT_LINE_RE.finditer(content):
        value = str(match.group("value")).strip()
        if _is_deferred_or_placeholder(value) or not _NO_TIMEOUT_RE.match(value):
            continue
        return [
            PolicyFinding(
                requirement_id=(
                    f"{markers.ID_TIME_BUDGET}:{_TESTING_POLICY}:timeout-enforcement-missing"
                ),
                path=path,
                missing_evidence=(
                    f"`{_TIMEOUT_FACT_KEY}` records no runner-enforced limit: {value}; a hung "
                    "or crash-looping test then blocks every unattended gate run until an "
                    "outer timeout fires"
                ),
                required_outcome=(
                    "install a runner-level limit (testing-policy.md rule 8) that terminates "
                    "a test or suite run past its limit and fails the gate, then record the "
                    f"mechanism and its limit in `{_TIMEOUT_FACT_KEY}`; a wrapper that only "
                    "compares elapsed time after the command exits does not count"
                ),
            )
        ]
    return []


def check_fixed_time_caps(content: str, path: str, filename: str) -> list[PolicyFinding]:
    if filename not in TIME_BUDGET_POLICY_FILES:
        return []
    findings = _fixed_cap_findings(content, path, filename)
    if filename == _VERIFICATION_POLICY:
        findings.extend(_overage_findings(content, path))
    if filename == _TESTING_POLICY:
        findings.extend(_timeout_enforcement_findings(content, path))
    return findings


__all__ = ["OVERAGE_FACT_KEY", "TIME_BUDGET_POLICY_FILES", "check_fixed_time_caps"]
