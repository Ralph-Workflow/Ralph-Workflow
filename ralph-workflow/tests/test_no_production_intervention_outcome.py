"""Cross-tree guard: no production module produces an operator-intervention outcome.

The intervention hand-off was retired: a spent diagnostic budget or a
stale retained intent is a typed record-fact outcome routed to the
autonomous fallback ladder, never a terminal ``REQUIRE_INTERVENTION`` /
``REPORT_INTERVENTION_REQUIRED`` dead end blocked on a person. This
guard scans the production tree (``ralph/``) — comments and docstrings
stripped — and asserts:

(a) no production module produces ``IntegrationAction.REQUIRE_INTERVENTION``
    as an outcome (allow-list: :mod:`ralph.pipeline._integration_action`,
    the enum home and owner-seam decider, plus comment/docstring text);
(b) no production module emits ``RAISE_DIAGNOSTIC_INTERVENTION`` or sets
    ``diagnostic_intervention_required=True`` (allow-list: the retired
    transition definition, the ``__all__`` re-export, and legacy-record
    load-and-clear readers that CLEAR the flag);
(c) no production code path returns the terminal
    ``PhaseDecision.REPORT_INTERVENTION_REQUIRED`` decision (allow-list:
    the enum definition and the legacy-checkpoint-derived return);
(d) no display module maps a live production state to the terminal
    ``INTERVENTION_REQUIRED`` state (allow-list: retired-string mappings
    kept for legacy-payload rendering).

Every allow-list entry is keyed on ``(file, line)`` and must be matched
by the current tree: a stale entry fails the guard, so the list cannot
rot silently.
"""

from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
from pathlib import Path
from typing import Final

import pytest

_PRODUCTION_ROOT: Final = Path(__file__).resolve().parent.parent / "ralph"

_NEEDLES: Final = (
    "IntegrationAction.REQUIRE_INTERVENTION",
    "RAISE_DIAGNOSTIC_INTERVENTION",
    "diagnostic_intervention_required",
    "REPORT_INTERVENTION_REQUIRED",
)
_DISPLAY_NEEDLE: Final = "INTERVENTION_REQUIRED"

# (a) The enum home and owner-seam decider owns the outcome by design:
# every consumer routes the typed verdict to the autonomous ladder, so
# the only legitimate producer in the tree is this module.
_INTEGRATION_ACTION_PRODUCER_MODULE: Final = "pipeline/_integration_action.py"

_REQUIRE_INTERVENTION_PRODUCER: Final = re.compile(
    r"\breturn\s+IntegrationAction\.REQUIRE_INTERVENTION\b"
    r"|\belse\s+IntegrationAction\.REQUIRE_INTERVENTION\b"
    r"|(?<![=!])=\s*IntegrationAction\.REQUIRE_INTERVENTION\b"
)

# (b) ``RAISE_DIAGNOSTIC_INTERVENTION`` may exist only as the retired
# transition definition (and its registration / ``__all__`` names).
_DIAGNOSTIC_INTERVENTION_EMITTER: Final = re.compile(r"\bRAISE_DIAGNOSTIC_INTERVENTION\b")

#: ``diagnostic_intervention_required=True`` may never be written: the
#: field survives only as durable attempt history that legacy loaders
#: CLEAR at parse time.
_DIAGNOSTIC_FLAG_TRUE_SET: Final = re.compile(
    r"\bdiagnostic_intervention_required\s*=\s*True\b"
    r"|[\"']diagnostic_intervention_required[\"']\s*:\s*True\b"
)

# (c) Only the state machine may RETURN the terminal decision, and only
# from legacy checkpoint outcome values loaded from disk.
_PHASE_DECISION_PRODUCER: Final = re.compile(
    r"\breturn\s+PhaseDecision\.REPORT_INTERVENTION_REQUIRED\b"
)

# (d) Display mapping lines: ``"<key>": ...INTERVENTION_REQUIRED...``.
_DISPLAY_MAPPING: Final = re.compile(
    r"^\s*[\"'](?P<key>[a-z_]+)[\"']\s*:\s*\(?\s*"
    r"(?:OperationDisplayState\.)?[\"']?INTERVENTION_REQUIRED\b"
)

#: Retired display keys: strings only legacy payloads / checkpoints can
#: still carry. Live production states (``ladder_escalation``,
#: ``evidence_diagnosis``, ...) must never map to the terminal state.
_RETIRED_DISPLAY_KEYS: Final = frozenset(
    {
        "intervention_required",
        "intervention",
        "diagnostic_intervention_required",
        "require_intervention",
        "report_intervention_required",
        "unknown",
    }
)

# (b) allow-list: file-relative path -> {(line, expected snippet): justification}.
_ALLOW_DIAGNOSTIC_EMITTERS: Final = {
    ("pipeline/auto_integrate_transition.py", 266): (
        "RAISE_DIAGNOSTIC_INTERVENTION = Transition(",
        "retired transition definition kept ONLY as a legacy-payload parsing input",
    ),
    ("pipeline/auto_integrate_transition.py", 267): (
        '"RAISE_DIAGNOSTIC_INTERVENTION"',
        "retired transition definition kept ONLY as a legacy-payload parsing input",
    ),
    ("pipeline/auto_integrate_transition.py", 311): (
        "RAISE_DIAGNOSTIC_INTERVENTION,",
        "registration of the retired transition into the closed Transition.ALL set",
    ),
    ("pipeline/auto_integrate_transition.py", 358): (
        '"RAISE_DIAGNOSTIC_INTERVENTION",',
        "__all__ export of the retired transition name",
    ),
    ("pipeline/auto_integrate_record.py", 49): (
        "RAISE_DIAGNOSTIC_INTERVENTION,",
        "import of the retired transition name for re-export via __all__ (legacy parsing surface)",
    ),
    ("pipeline/auto_integrate_record.py", 947): (
        '"RAISE_DIAGNOSTIC_INTERVENTION",',
        "__all__ re-export of the retired transition name (parsing surface)",
    ),
}

# (c) allow-list.
_ALLOW_PHASE_DECISION_PRODUCERS: Final = {
    ("pipeline/_pipeline_state_machine.py", 199): (
        "return PhaseDecision.REPORT_INTERVENTION_REQUIRED",
        "reachable only from legacy checkpoint outcome values (retired "
        "INTERVENTION_REQUIRED / DIAGNOSTIC_INTERVENTION_REQUIRED / UNKNOWN) "
        "loaded from disk; the live LADDER_ESCALATION escalation returns "
        "BLOCK_FOR_RECOVERY before this line",
    ),
}

# (d) allow-list.
_ALLOW_DISPLAY_MAPPINGS: Final = {
    ("display/status_bar.py", 324): (
        '"intervention_required": ("INTERVENTION_REQUIRED", "error", "theme.status.error")',
        "ATTENTION_PRESENTATION retains the retired attention key for legacy "
        "checkpoint rendering; live ladder escalation renders as evidence_diagnosis",
    ),
    ("display/auto_integrate_message.py", 163): (
        '"intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value',
        "_LEGACY_ACTION_DISPLAY_MAP: retired action strings kept for legacy-payload parsing only",
    ),
    ("display/auto_integrate_message.py", 164): (
        '"intervention": OperationDisplayState.INTERVENTION_REQUIRED.value',
        "_LEGACY_ACTION_DISPLAY_MAP: retired action strings kept for legacy-payload parsing only",
    ),
    ("display/auto_integrate_message.py", 167): (
        '"diagnostic_intervention_required": OperationDisplayState.INTERVENTION_REQUIRED.value',
        "_LEGACY_ACTION_DISPLAY_MAP: retired action strings kept for legacy-payload parsing only",
    ),
    ("display/auto_integrate_message.py", 182): (
        '"unknown": OperationDisplayState.INTERVENTION_REQUIRED.value',
        "fail-closed rendering of an unrecognised legacy action string; the "
        "live ladder escalation maps to EVIDENCE_DIAGNOSIS",
    ),
    ("display/auto_integrate_message.py", 203): (
        '"require_intervention": OperationDisplayState.INTERVENTION_REQUIRED',
        "_ACTION_TO_DISPLAY_STATE retains the retired action name so legacy "
        "records display without inventing behavior",
    ),
    ("display/auto_integrate_message.py", 204): (
        '"intervention": OperationDisplayState.INTERVENTION_REQUIRED',
        "_ACTION_TO_DISPLAY_STATE retains the retired action name so legacy "
        "records display without inventing behavior",
    ),
    ("display/auto_integrate_message.py", 205): (
        '"intervention_required": OperationDisplayState.INTERVENTION_REQUIRED',
        "_ACTION_TO_DISPLAY_STATE retains the retired action name so legacy "
        "records display without inventing behavior",
    ),
    ("display/auto_integrate_message.py", 206): (
        '"diagnostic_intervention_required": OperationDisplayState.INTERVENTION_REQUIRED',
        "_ACTION_TO_DISPLAY_STATE retains the retired action name so legacy "
        "records display without inventing behavior",
    ),
}


def _comment_and_docstring_lines(source: str) -> set[int]:
    """Line numbers that are comments or docstrings (not code)."""
    dropped: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            dropped.add(token.start[0])
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                end = first.end_lineno if first.end_lineno is not None else first.lineno
                dropped.update(range(first.lineno, end + 1))
    return dropped


def _code_lines(source: str) -> dict[int, str]:
    dropped = _comment_and_docstring_lines(source)
    return {
        number: text
        for number, text in enumerate(source.splitlines(), start=1)
        if number not in dropped and text.strip()
    }


@functools.lru_cache(maxsize=1)
def _production_sources() -> dict[str, str]:
    """Relative-path -> source for production modules that mention the needles."""
    sources: dict[str, str] = {}
    for path in sorted(_PRODUCTION_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if any(needle in source for needle in (*_NEEDLES, _DISPLAY_NEEDLE)):
            sources[str(path.relative_to(_PRODUCTION_ROOT))] = source
    return sources


def _check_allow_list(
    rel: str,
    lineno: int,
    text: str,
    allow_list: dict[tuple[str, int], tuple[str, str]],
    hits: set[tuple[str, int]],
    violations: list[str],
) -> None:
    entry = allow_list.get((rel, lineno))
    if entry is None:
        violations.append(f"{rel}:{lineno}: un-allow-listed site: {text.strip()}")
        return
    snippet, justification = entry
    hits.add((rel, lineno))
    if snippet not in text:
        violations.append(
            f"{rel}:{lineno}: allow-list snippet no longer matches: {text.strip()!r} "
            f"(expected {snippet!r}; {justification})"
        )


def _stale_entries(
    allow_list: dict[tuple[str, int], tuple[str, str]],
    hits: set[tuple[str, int]],
) -> list[str]:
    return [
        f"{rel}:{lineno}: stale allow-list entry (site no longer matched)"
        for rel, lineno in sorted(allow_list)
        if (rel, lineno) not in hits
    ]


def _scan_line(
    rel: str,
    lineno: int,
    text: str,
    emitter_hits: set[tuple[str, int]],
    decision_hits: set[tuple[str, int]],
    display_hits: set[tuple[str, int]],
    violations: list[str],
) -> None:
    """Apply every guard pattern to one code line."""
    if _REQUIRE_INTERVENTION_PRODUCER.search(text) and rel != _INTEGRATION_ACTION_PRODUCER_MODULE:
        violations.append(
            f"{rel}:{lineno}: production module produces "
            f"IntegrationAction.REQUIRE_INTERVENTION: {text.strip()}"
        )
    if _DIAGNOSTIC_INTERVENTION_EMITTER.search(text):
        _check_allow_list(rel, lineno, text, _ALLOW_DIAGNOSTIC_EMITTERS, emitter_hits, violations)
    if _DIAGNOSTIC_FLAG_TRUE_SET.search(text):
        violations.append(
            f"{rel}:{lineno}: production module sets "
            f"diagnostic_intervention_required=True: {text.strip()}"
        )
    if _PHASE_DECISION_PRODUCER.search(text):
        _check_allow_list(
            rel, lineno, text, _ALLOW_PHASE_DECISION_PRODUCERS, decision_hits, violations
        )
    if not rel.startswith("display/"):
        return
    mapping = _DISPLAY_MAPPING.search(text)
    if mapping is None:
        return
    key = mapping.group("key")
    if key not in _RETIRED_DISPLAY_KEYS:
        violations.append(
            f"{rel}:{lineno}: display module maps live state {key!r} to "
            f"INTERVENTION_REQUIRED: {text.strip()}"
        )
    else:
        _check_allow_list(rel, lineno, text, _ALLOW_DISPLAY_MAPPINGS, display_hits, violations)


def _scan_tree(sources: dict[str, str]) -> list[str]:
    """Scan production sources; return every violation found (empty when clean)."""
    violations: list[str] = []
    emitter_hits: set[tuple[str, int]] = set()
    decision_hits: set[tuple[str, int]] = set()
    display_hits: set[tuple[str, int]] = set()

    for rel, source in sorted(sources.items()):
        for lineno, text in _code_lines(source).items():
            _scan_line(rel, lineno, text, emitter_hits, decision_hits, display_hits, violations)

    violations.extend(_stale_entries(_ALLOW_DIAGNOSTIC_EMITTERS, emitter_hits))
    violations.extend(_stale_entries(_ALLOW_PHASE_DECISION_PRODUCERS, decision_hits))
    violations.extend(_stale_entries(_ALLOW_DISPLAY_MAPPINGS, display_hits))
    return violations


@pytest.mark.timeout_seconds(5)
def test_no_production_module_produces_an_intervention_outcome() -> None:
    """AC cross-tree guard: the retired hand-off has no live producer anywhere."""
    violations = _scan_tree(_production_sources())
    assert not violations, "intervention-outcome guard violations:\n" + "\n".join(violations)


def test_the_guard_catches_an_injected_intervention_producer() -> None:
    """The guard is real: a newly injected producer string makes it fail, by site."""
    injected = {
        "pipeline/fake_decider.py": (
            "from ralph.pipeline._integration_action import IntegrationAction\n"
            "\n"
            "\n"
            "def decide(record: object) -> IntegrationAction:\n"
            "    return IntegrationAction.REQUIRE_INTERVENTION\n"
        ),
        "pipeline/fake_writer.py": (
            "def write() -> dict[str, bool]:\n"
            "    return {'diagnostic_intervention_required': True}\n"
        ),
        "display/fake_display.py": (
            "DISPLAY = {\n"
            '    "ladder_escalation": OperationDisplayState.INTERVENTION_REQUIRED,\n'
            "}\n"
        ),
    }
    violations = _scan_tree(injected)
    joined = "\n".join(violations)
    assert "fake_decider.py:5" in joined
    assert "fake_writer.py:2" in joined
    assert "fake_display.py:2" in joined
