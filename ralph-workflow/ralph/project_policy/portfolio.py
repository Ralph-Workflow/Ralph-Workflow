"""Typed deterministic composition for bounded project-policy portfolios.

The TOML contract composes controls in one order: invariant kernel, selected
risk/context profiles, local tightening, then narrow exceptions. Declaration
order never affects the returned portfolio.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

from ralph.project_policy.markers import (
    CLAIM_SCOPE_PHRASES,
    PORTFOLIO_PATH,
    PORTFOLIO_SCHEMA_VERSION,
)
from ralph.project_policy.models import PortfolioError

MAX_PORTFOLIO_BYTES: Final[int] = 65_536
MAX_CONTROLS: Final[int] = 64
MAX_PROFILES: Final[int] = 16
MAX_LANES: Final[int] = 16
MAX_EXCEPTIONS: Final[int] = 16
MAX_CONTEXT_VALUES: Final[int] = 16

# Same strict, narrow contract as the citation ``path:`` field. The
# regex restricts the value to a scheme-less relative path so a
# portfolio control cannot smuggle a URL, whitespace, or a ``..``
# escape past the existence check.
_PORTFOLIO_PATH_VALUE_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_\-./]*$")
_ALLOWED_ROOT = frozenset(
    {
        "schema_version",
        "default_budget_seconds",
        "kernel_controls",
        "context",
        "lanes",
        "controls",
        "profiles",
        "local_tightening",
        "exceptions",
    }
)
_ALLOWED_CONTEXT = frozenset(
    {
        "impact",
        "likelihood",
        "uncertainty",
        "recoverability",
        "architectures",
        "rate_of_change",
        "expected_lifetime",
        "obligations",
    }
)
_ALLOWED_LANE = frozenset({"id", "owner", "trigger"})
_ALLOWED_COST_BASIS = frozenset({"measured", "estimate"})
_ALLOWED_CONTROL = frozenset(
    {
        "id",
        "protected_outcome",
        "fault_sensitivity",
        "layer",
        "owner",
        "trigger",
        "lane",
        "marginal_cost_seconds",
        "evidence",
        "evidence_path",
        "lifecycle",
        "disposition",
        "removed-tests",
        "cost_basis",
        # v2 additions: the detector's actual observation and the trigger
        # for retiring the control. The claim-scope rule (B7) checks
        # ``protected_outcome`` against ``detects`` for broad-guarantee
        # phrases; ``retirement_trigger`` is a free-form string that the
        # review surface can quote back.
        "detects",
        "retirement_trigger",
    }
)
_OPTIONAL_CONTROL = frozenset({"evidence_path", "removed-tests"})
_ALLOWED_PROFILE = frozenset({"id", "version", "requires", "controls", "incompatible_with"})
_ALLOWED_TIGHTENING = frozenset({"controls"})
_ALLOWED_EXCEPTION = frozenset({"id", "control", "owner", "reason", "review_trigger", "expires"})
_REQUIRED_CONTROL = _ALLOWED_CONTROL - _OPTIONAL_CONTROL
_RISK_VALUES = {
    "impact": frozenset({"low", "medium", "high", "critical"}),
    "likelihood": frozenset({"low", "medium", "high"}),
    "uncertainty": frozenset({"low", "medium", "high"}),
    "recoverability": frozenset({"low", "medium", "high"}),
    "rate_of_change": frozenset({"low", "medium", "high"}),
    "expected_lifetime": frozenset({"short", "medium", "long"}),
}
_DISPOSITIONS = frozenset({"REMOVE", "MERGE", "REPLACE", "KEEP"})
_FORBIDDEN_EVIDENCE = ("process exit", "telemetry", "agent narration", "private state")


@dataclass(frozen=True, slots=True)
class _Lane:
    id: str
    owner: str
    trigger: str


@dataclass(frozen=True, slots=True)
class _Control:
    id: str
    protected_outcome: str
    fault_sensitivity: str
    layer: str
    owner: str
    trigger: str
    lane: str
    marginal_cost_seconds: float
    evidence: str
    lifecycle: str
    disposition: str
    cost_basis: str
    detects: str
    retirement_trigger: str
    removed_tests: str | None = None


@dataclass(frozen=True, slots=True)
class _Profile:
    id: str
    version: int
    requires: tuple[tuple[str, tuple[str, ...]], ...]
    controls: tuple[str, ...]
    incompatible_with: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True, slots=True)
class PolicyPortfolio:
    """An immutable effective portfolio suitable for cache signatures."""

    schema_version: str
    context: tuple[tuple[str, str | tuple[str, ...]], ...]
    selected_profiles: tuple[str, ...]
    lanes: tuple[_Lane, ...]
    controls: tuple[_Control, ...]
    applied_exceptions: tuple[str, ...]
    default_budget_seconds: float
    default_cost_seconds: float


def _error(message: str) -> PortfolioError:
    return PortfolioError(message[:500])


def _table_list(root: Mapping[str, object], key: str, limit: int) -> list[dict[str, object]]:
    value = root.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _error(f"{key} must be an array of tables")
    if len(value) > limit:
        raise _error(f"portfolio allows at most {limit} {key}")
    return cast("list[dict[str, object]]", value)


def _reject_unknown(table: Mapping[str, object], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise _error(f"unknown {label} field: {unknown[0]}")


def _parse_evidence_path(
    table: Mapping[str, object],
    *,
    workspace_exists: Callable[[str], bool] | None,
) -> str | None:
    """Return the optional ``evidence_path`` value when it should be existence-checked.

    The portfolio control's evidence is REQUIRED as a prose string
    (existing behaviour, unchanged). The optional ``evidence_path``
    field is additive: it names a repository-local path that
    existence-checks against the workspace root. A value that fails
    the strict regex OR names a non-existent path is reported as
    malformed / missing so a portfolio cannot pass with a stale
    citation.
    """
    raw = table.get("evidence_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    if not _PORTFOLIO_PATH_VALUE_RE.match(raw.strip()):
        return raw.strip()
    return raw.strip()


def _validate_evidence_path(
    control_id: str,
    table: Mapping[str, object],
    *,
    workspace_exists: Callable[[str], bool] | None,
) -> None:
    """Run the optional ``evidence_path`` existence check.

    The ``evidence`` field stays a prose string (existing contract);
    the explicit ``evidence_path`` field is the additive trigger that
    activates the existence check. A malformed value OR a path that
    does not exist under the repository root fails the parse closed.
    """
    evidence_path = _parse_evidence_path(table, workspace_exists=workspace_exists)
    if evidence_path is None:
        return
    if not _PORTFOLIO_PATH_VALUE_RE.match(evidence_path):
        raise _error(
            f"control {control_id} evidence_path {evidence_path!r} is not a "
            "scheme-less relative path under the repository root"
        )
    if workspace_exists is not None and not workspace_exists(evidence_path):
        raise _error(
            f"control {control_id} cites evidence_path {evidence_path!r} "
            "that does not resolve under the repository root"
        )


def _text(table: Mapping[str, object], key: str, label: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} requires non-empty {key}")
    return value.strip()


def _check_claim_scope(control_id: str, protected_outcome: str, detects: str) -> None:
    """Apply the deterministic claim-scope rule (B7).

    A control is rejected when its ``protected_outcome`` carries any phrase
    from :data:`markers.CLAIM_SCOPE_PHRASES` that does NOT also appear in
    the same control's ``detects`` field. The rule enforces the v5
    outcome-oriented kernel: a broad guarantee (``dependency direction
    enforced``, ``all inputs validated``) is a claim about the WORLD, not
    about a detector, and is rejected unless the detector's observation
    literally mentions the same phrase. The substring predicate is the
    deterministic floor; the human-review rubric covers the residual
    semantic gap.
    """
    outcome_lower = protected_outcome.lower()
    detects_lower = detects.lower()
    for phrase in CLAIM_SCOPE_PHRASES:
        if phrase not in outcome_lower:
            continue
        if phrase in detects_lower:
            continue
        raise _error(
            f"control {control_id} protected_outcome carries broad phrase "
            f"{phrase!r} that does not appear in detects; rewrite the "
            "outcome to name only what the detector observes"
        )


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise _error(f"{label} must be a list of non-empty strings")
    if len(value) > MAX_CONTEXT_VALUES:
        raise _error(f"{label} allows at most {MAX_CONTEXT_VALUES} values")
    return tuple(sorted({item.strip() for item in value if isinstance(item, str)}))


def _parse_context(value: object) -> tuple[tuple[str, str | tuple[str, ...]], ...]:
    if not isinstance(value, dict):
        raise _error("context must be a table")
    _reject_unknown(value, _ALLOWED_CONTEXT, "context")
    normalized: list[tuple[str, str | tuple[str, ...]]] = []
    for key, raw in value.items():
        if key in {"architectures", "obligations"}:
            normalized.append((key, _strings(raw, f"context.{key}")))
            continue
        if not isinstance(raw, str) or raw not in _RISK_VALUES[key]:
            raise _error(f"context.{key} has unsupported value")
        normalized.append((key, raw))
    return tuple(sorted(normalized))


def _lane_id(lane: _Lane) -> str:
    return lane.id


def _parse_lanes(root: Mapping[str, object]) -> tuple[_Lane, ...]:
    lanes: list[_Lane] = []
    seen: set[str] = set()
    for table in _table_list(root, "lanes", MAX_LANES):
        _reject_unknown(table, _ALLOWED_LANE, "lane")
        lane = _Lane(
            id=_text(table, "id", "lane"),
            owner=_text(table, "owner", "lane"),
            trigger=_text(table, "trigger", "lane"),
        )
        if lane.id in seen:
            raise _error(f"duplicate lane: {lane.id}")
        seen.add(lane.id)
        lanes.append(lane)
    if "default" not in seen:
        raise _error("lanes must declare the default lane")
    return tuple(sorted(lanes, key=_lane_id))


def _parse_controls(
    root: Mapping[str, object],
    lane_ids: frozenset[str],
    *,
    workspace_exists: Callable[[str], bool] | None = None,
) -> dict[str, _Control]:
    controls: dict[str, _Control] = {}
    outcomes: dict[str, str] = {}
    for table in _table_list(root, "controls", MAX_CONTROLS):
        _reject_unknown(table, _ALLOWED_CONTROL, "control")
        missing = sorted(_REQUIRED_CONTROL - set(table))
        if missing:
            raise _error(f"control requires field: {missing[0]}")
        control_id = _text(table, "id", "control")
        if control_id in controls:
            raise _error(f"duplicate control: {control_id}")
        lane = _text(table, "lane", f"control {control_id}")
        if lane not in lane_ids:
            raise _error(f"control {control_id} references unknown lane: {lane}")
        cost = table["marginal_cost_seconds"]
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise _error(f"control {control_id} requires non-negative marginal cost")
        disposition = _text(table, "disposition", f"control {control_id}")
        if disposition not in _DISPOSITIONS:
            raise _error(f"control {control_id} has unsupported disposition")
        evidence = _text(table, "evidence", f"control {control_id}")
        if any(token in evidence.lower() for token in _FORBIDDEN_EVIDENCE):
            raise _error(f"control {control_id} requires inspectable evidence/oracle")
        # Optional ``evidence_path`` existence check. Extracted into a
        # helper to keep the parser's branch count bounded.
        _validate_evidence_path(control_id, table, workspace_exists=workspace_exists)
        outcome = _text(table, "protected_outcome", f"control {control_id}")
        if outcome in outcomes:
            raise _error(
                f"cost laundering: controls {outcomes[outcome]} and {control_id} "
                "relabel the same protected outcome"
            )
        outcomes[outcome] = control_id
        removed_tests_raw = table.get("removed-tests")
        removed_tests_value: str | None
        if removed_tests_raw is None:
            removed_tests_value = None
        elif isinstance(removed_tests_raw, str):
            removed_tests_value = removed_tests_raw
        else:
            raise _error(f"control {control_id} removed-tests must be a string")
        cost_basis_raw = table.get("cost_basis")
        if not isinstance(cost_basis_raw, str) or cost_basis_raw not in _ALLOWED_COST_BASIS:
            raise _error(f"control {control_id} requires cost_basis = 'measured' or 'estimate'")
        # v2 required fields. ``detects`` and ``retirement_trigger`` are
        # mandatory on every v2 control; missing or empty values fail the
        # parse closed.
        detects = _text(table, "detects", f"control {control_id}")
        retirement_trigger = _text(table, "retirement_trigger", f"control {control_id}")
        # Claim-scope rule (B7). Apply the deterministic substring
        # predicate against the new required fields.
        _check_claim_scope(control_id, outcome, detects)
        controls[control_id] = _Control(
            id=control_id,
            protected_outcome=outcome,
            fault_sensitivity=_text(table, "fault_sensitivity", f"control {control_id}"),
            layer=_text(table, "layer", f"control {control_id}"),
            owner=_text(table, "owner", f"control {control_id}"),
            trigger=_text(table, "trigger", f"control {control_id}"),
            lane=lane,
            marginal_cost_seconds=float(cost),
            evidence=evidence,
            lifecycle=_text(table, "lifecycle", f"control {control_id}"),
            disposition=disposition,
            cost_basis=cost_basis_raw,
            removed_tests=removed_tests_value,
            detects=detects,
            retirement_trigger=retirement_trigger,
        )
    return controls


def _profile_key(profile: _Profile) -> str:
    return profile.key


def _parse_profiles(root: Mapping[str, object]) -> tuple[_Profile, ...]:
    profiles: list[_Profile] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for table in _table_list(root, "profiles", MAX_PROFILES):
        _reject_unknown(table, _ALLOWED_PROFILE, "profile")
        profile_id = _text(table, "id", "profile")
        version = table.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise _error(f"profile {profile_id} requires a positive version")
        key = f"{profile_id}@{version}"
        if key in seen_keys or profile_id in seen_ids:
            raise _error(f"duplicate or incompatible profile version: {key}")
        seen_keys.add(key)
        seen_ids.add(profile_id)
        raw_requires = table.get("requires", {})
        if not isinstance(raw_requires, dict):
            raise _error(f"profile {key} requires must be a table")
        _reject_unknown(raw_requires, _ALLOWED_CONTEXT, f"profile {key} requirement")
        requires = tuple(
            sorted(
                (
                    name,
                    _strings(values, f"profile {key} requires.{name}"),
                )
                for name, values in raw_requires.items()
            )
        )
        profiles.append(
            _Profile(
                id=profile_id,
                version=version,
                requires=requires,
                controls=_strings(table.get("controls", []), f"profile {key} controls"),
                incompatible_with=_strings(
                    table.get("incompatible_with", []),
                    f"profile {key} incompatible_with",
                ),
            )
        )
    return tuple(sorted(profiles, key=_profile_key))


def _profile_matches(profile: _Profile, context: Mapping[str, str | tuple[str, ...]]) -> bool:
    for key, accepted in profile.requires:
        actual = context.get(key)
        if isinstance(actual, tuple):
            if not set(actual).intersection(accepted):
                return False
        elif actual not in accepted:
            return False
    return bool(profile.requires)


def _selected_control_ids(
    root: Mapping[str, object],
    profiles: tuple[_Profile, ...],
    context: Mapping[str, str | tuple[str, ...]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    kernel = _strings(root.get("kernel_controls"), "kernel_controls")
    selected = tuple(profile for profile in profiles if _profile_matches(profile, context))
    selected_keys = frozenset(profile.key for profile in selected)
    for profile in selected:
        conflict = selected_keys.intersection(profile.incompatible_with)
        if conflict:
            raise _error(
                f"incompatible profile: {profile.key} conflicts with {sorted(conflict)[0]}"
            )
    tightening = root.get("local_tightening", {})
    if not isinstance(tightening, dict):
        raise _error("local_tightening must be a table")
    _reject_unknown(tightening, _ALLOWED_TIGHTENING, "local_tightening")
    local = _strings(tightening.get("controls", []), "local_tightening.controls")
    ids = set(kernel)
    for profile in selected:
        ids.update(profile.controls)
    ids.update(local)
    return tuple(sorted(ids)), tuple(profile.key for profile in selected)


def _apply_exceptions(
    root: Mapping[str, object],
    selected_ids: tuple[str, ...],
    kernel_ids: frozenset[str],
    *,
    today: date,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    retained = set(selected_ids)
    applied: list[str] = []
    seen: set[str] = set()
    for table in _table_list(root, "exceptions", MAX_EXCEPTIONS):
        _reject_unknown(table, _ALLOWED_EXCEPTION, "exception")
        exception_id = _text(table, "id", "exception")
        if exception_id in seen:
            raise _error(f"duplicate exception: {exception_id}")
        seen.add(exception_id)
        control = _text(table, "control", f"exception {exception_id}")
        if control == "*" or control not in retained:
            raise _error(f"exception {exception_id} must name one exact control")
        _text(table, "owner", f"exception {exception_id}")
        _text(table, "reason", f"exception {exception_id}")
        _text(table, "review_trigger", f"exception {exception_id}")
        raw_expires = table.get("expires")
        if isinstance(raw_expires, str):
            try:
                expires = date.fromisoformat(raw_expires)
            except ValueError as exc:
                raise _error(f"exception {exception_id} requires an ISO expiry date") from exc
        elif isinstance(raw_expires, date):
            expires = raw_expires
        else:
            raise _error(f"exception {exception_id} requires an ISO expiry date")
        if expires < today:
            raise _error(f"expired exception: {exception_id}")
        if control in kernel_ids:
            raise _error(f"exception {exception_id} cannot remove an invariant kernel control")
        retained.remove(control)
        applied.append(exception_id)
    return tuple(sorted(retained)), tuple(sorted(applied))


def parse_portfolio_toml(
    content: str,
    *,
    today: date | None = None,
    workspace_exists: Callable[[str], bool] | None = None,
) -> PolicyPortfolio:
    """Parse, validate, and deterministically compose one portfolio manifest.

    ``workspace_exists`` is the production-side resolver for the
    optional ``evidence_path`` existence check. The production ingress
    in :func:`ralph.project_policy.validators._check_portfolio` always
    supplies it; unit tests that exercise the parser in isolation may
    omit it, in which case the existence check degrades to a no-op
    while the strict shape validation still runs.
    """

    if len(content.encode("utf-8")) > MAX_PORTFOLIO_BYTES:
        raise _error(f"portfolio exceeds {MAX_PORTFOLIO_BYTES} bytes")
    try:
        root = cast("dict[str, object]", tomllib.loads(content))
    except ValueError as exc:
        raise _error(f"invalid portfolio TOML: {exc}") from exc
    _reject_unknown(root, _ALLOWED_ROOT, "root")
    if root.get("schema_version") != PORTFOLIO_SCHEMA_VERSION:
        raise _error(f"schema_version must be {PORTFOLIO_SCHEMA_VERSION}")
    budget = root.get("default_budget_seconds")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        raise _error("default_budget_seconds must be positive")
    context_tuple = _parse_context(root.get("context", {}))
    context = dict(context_tuple)
    lanes = _parse_lanes(root)
    controls = _parse_controls(
        root,
        frozenset(lane.id for lane in lanes),
        workspace_exists=workspace_exists,
    )
    profiles = _parse_profiles(root)
    selected_ids, selected_profiles = _selected_control_ids(root, profiles, context)
    missing = sorted(set(selected_ids) - set(controls))
    if missing:
        raise _error(f"composition references unknown control: {missing[0]}")
    kernel_ids = frozenset(_strings(root.get("kernel_controls"), "kernel_controls"))
    retained_ids, applied = _apply_exceptions(
        root,
        selected_ids,
        kernel_ids,
        today=today or date.today(),
    )
    effective = tuple(controls[control_id] for control_id in retained_ids)
    default_cost = sum(
        control.marginal_cost_seconds for control in effective if control.lane == "default"
    )
    if default_cost > float(budget):
        raise _error(
            f"aggregate default budget overflow: {default_cost:g}s exceeds {float(budget):g}s"
        )
    return PolicyPortfolio(
        schema_version=PORTFOLIO_SCHEMA_VERSION,
        context=context_tuple,
        selected_profiles=selected_profiles,
        lanes=lanes,
        controls=effective,
        applied_exceptions=applied,
        default_budget_seconds=float(budget),
        default_cost_seconds=default_cost,
    )


__all__ = [
    "MAX_CONTEXT_VALUES",
    "MAX_CONTROLS",
    "MAX_EXCEPTIONS",
    "MAX_LANES",
    "MAX_PORTFOLIO_BYTES",
    "MAX_PROFILES",
    "PORTFOLIO_PATH",
    "PORTFOLIO_SCHEMA_VERSION",
    "PolicyPortfolio",
    "parse_portfolio_toml",
]
