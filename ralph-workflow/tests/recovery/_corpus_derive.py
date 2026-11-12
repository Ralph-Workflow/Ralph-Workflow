from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.parsers import get_parser
from ralph.config.enums import AgentTransport
from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ALLOWED_ATTRIBUTIONS",
    "ALLOWED_LABELS",
    "ALLOWED_VERDICTS",
    "CORPUS_DIR",
    "CorpusCase",
    "derive_attempt_facts",
    "derive_workspace_change",
    "load_corpus",
    "load_fixtures",
]

CORPUS_DIR = Path(__file__).parent / "dead_agent_corpus"

ALLOWED_LABELS = frozenset({"positive", "negative"})
ALLOWED_ATTRIBUTIONS = frozenset({"agent", "runtime", "task_or_environment", "unknown"})
ALLOWED_VERDICTS = frozenset({"agent_dead", "healthy", "not_agent_fault", "unknown"})

_TRISTATE_VALUES: dict[str, bool | None] = {"true": True, "false": False, "unknown": None}
_TOOL_USE = "tool_use"


@dataclass(frozen=True)
class CorpusCase:
    id: str
    label: str
    expected_bench: bool
    expected_verdict: str
    expected_attribution: str
    facts: AttemptFacts


def load_fixtures(directory: Path | None = None) -> tuple[dict[str, object], ...]:
    root = CORPUS_DIR if directory is None else directory
    payloads: list[dict[str, object]] = []
    for path in sorted(root.glob("*.json")):
        decoded: object = json.loads(path.read_text(encoding="utf-8"))
        payloads.append(cast("dict[str, object]", decoded))
    return tuple(payloads)


def load_corpus(directory: Path | None = None) -> tuple[CorpusCase, ...]:
    return tuple(_case_from_fixture(fixture) for fixture in load_fixtures(directory))


def derive_workspace_change(fixture: Mapping[str, object]) -> bool | None:
    return _tri_state(fixture.get("workspace_changed", "unknown"))


def derive_attempt_facts(fixture: Mapping[str, object]) -> AttemptFacts:
    transport = AgentTransport(str(fixture["transport"]))
    lines = _captured_lines(fixture.get("captured_lines"))
    event_kinds, tool_calls = _classify(transport, lines)
    return AttemptFacts(
        event_kinds=event_kinds,
        tool_calls=tool_calls,
        workspace_changed_by_tool=_tri_state(fixture.get("workspace_changed_by_tool", "unknown")),
        artifact_valid=bool(fixture.get("artifact_valid", False)),
        workspace_changed=derive_workspace_change(fixture),
        elapsed_to_terminal_ms=_optional_int(fixture.get("elapsed_to_terminal_ms")),
        exit_status=_optional_str(fixture.get("exit_status")),
        liveness_observed=_optional_bool(fixture.get("liveness_observed")),
        subagent_activity_observed=_optional_bool(fixture.get("subagent_activity_observed")),
        termination=_termination(fixture.get("termination")),
    )


def _case_from_fixture(fixture: Mapping[str, object]) -> CorpusCase:
    return CorpusCase(
        id=str(fixture["id"]),
        label=_validated(str(fixture["label"]), ALLOWED_LABELS, "label"),
        expected_bench=bool(fixture["expected_bench"]),
        expected_verdict=_validated(
            str(fixture["expected_verdict"]), ALLOWED_VERDICTS, "expected_verdict"
        ),
        expected_attribution=_validated(
            str(fixture["expected_attribution"]), ALLOWED_ATTRIBUTIONS, "expected_attribution"
        ),
        facts=derive_attempt_facts(fixture),
    )


def _validated(value: str, allowed: frozenset[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _classify(
    transport: AgentTransport, lines: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    strategy = strategy_for_transport(transport)
    event_kinds: list[str] = []
    for line in lines:
        signal = strategy.classify_activity_line(line)
        if signal is None:
            continue
        event_kinds.append(signal.kind.value)
    parser = get_parser(transport.value)
    tool_calls: list[str] = []
    for parsed in parser.parse(iter(lines)):
        if parsed.type != _TOOL_USE:
            continue
        name = parsed.metadata.get("tool")
        if isinstance(name, str) and name.strip():
            tool_calls.append(name.strip())
            continue
        content = parsed.content.strip()
        if content:
            tool_calls.append(content)
    return tuple(event_kinds), tuple(tool_calls)


def _termination(value: object) -> TerminationRecord | None:
    if not isinstance(value, dict):
        return None
    issuer = TerminationIssuer(str(value.get("issuer", TerminationIssuer.UNKNOWN.value)))
    return TerminationRecord.create(
        issuer,
        observed_returncode=_optional_int(value.get("observed_returncode")),
        reported_returncode=_optional_int(value.get("reported_returncode")),
        liveness_observed=_optional_bool(value.get("liveness_observed")),
    )


def _captured_lines(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(line) for line in value)


def _tri_state(value: object) -> bool | None:
    key = str(value).strip().lower()
    if key not in _TRISTATE_VALUES:
        raise ValueError(f"tri-state value must be true, false or unknown, got {value!r}")
    return _TRISTATE_VALUES[key]


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
