"""Smoke-test plumbing: shared core for the interactive-Claude parity check.

This module is the single owner of the smoke-test agent-invocation loop.
The CLI surface in :mod:`ralph.cli.commands.smoke` stays thin (option
parsing, report rendering, exit codes only).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.agents._agy_upstream_diagnostic import agy_empty_output_reason
from ralph.agents.catalog import AgentCatalog
from ralph.agents.completion_signals import (
    _check_completion_sentinel,
    is_artifact_submitted,
)
from ralph.agents.display_capabilities import surface_to_capability
from ralph.agents.execution_state import strategy_for_command
from ralph.agents.invoke import (
    AgentInvocationError,
    InvokeOptions,
    OpenCodeResumableExitError,
    _clear_session_completion_sentinel,
    extract_transport_session_id,
    invoke_agent,
)
from ralph.agents.invoke._process_reader import _parent_broker_secret
from ralph.agents.parsers import get_parser, resolve_parser_key
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.display.capability_observation_recorder import infer_surface_for_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.preview_payload import payload_from_tool_event
from ralph.display.raw_overflow import detect_raw_log_breaks, raw_log_path_for
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.mcp.server._wire_ledger import wire_evidence_for
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineCore, PipelineDeps
from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime
from ralph.pipeline.plumbing.smoke_evidence import (
    Evidence,
    Provenance,
    absent,
    grade_artifact_submission_evidence,
    grade_completion_sentinel_evidence,
)
from ralph.pipeline.plumbing.smoke_multimodal import (
    SMOKE_FIXTURE_RELNAME,
    build_smoke_fixture_png,
    generate_fixture_geometry,
    grade_multimodal_evidence,
    smoke_media_config_toml,
)
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.pipeline.session_bridge import build_session_bridge
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.agents.display_capabilities import DisplayCapability
    from ralph.agents.display_capability_stance import DisplayCapabilityStance
    from ralph.agents.support import AgentSupport
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.pro_support.hooks import ProPipelineHooks

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_INTERACTIVE_AGENT = "claude/haiku"
_SMOKE_RUN_ID = "interactive-claude-smoke"
_HEADLESS_CLAUDE_SMOKE_RELATIVE_DIR = Path("tmp/headless-claude-smoke")
_HEADLESS_CLAUDE_SMOKE_OUTPUT_FILE = _HEADLESS_CLAUDE_SMOKE_RELATIVE_DIR / "todo-list.js"
_HEADLESS_CLAUDE_SMOKE_RUN_ID = "headless-claude-smoke"
_AGY_SMOKE_RELATIVE_DIR = Path("tmp/interactive-agy-smoke")
_AGY_SMOKE_OUTPUT_FILE = _AGY_SMOKE_RELATIVE_DIR / "todo-list.js"
_NANOCODER_SMOKE_RELATIVE_DIR = Path("tmp/interactive-nanocoder-smoke")
_NANOCODER_SMOKE_OUTPUT_FILE = _NANOCODER_SMOKE_RELATIVE_DIR / "todo-list.js"
_NANOCODER_SMOKE_RUN_ID = "interactive-nanocoder-smoke"
_CURSOR_SMOKE_RELATIVE_DIR = Path("tmp/interactive-cursor-smoke")
_CURSOR_SMOKE_OUTPUT_FILE = _CURSOR_SMOKE_RELATIVE_DIR / "todo-list.js"
_OPENCODE_SMOKE_RELATIVE_DIR = Path("tmp/interactive-opencode-smoke")
_OPENCODE_SMOKE_OUTPUT_FILE = _OPENCODE_SMOKE_RELATIVE_DIR / "todo-list.js"
_OPENCODE_SMOKE_RUN_ID = "interactive-opencode-smoke"
_CODEX_SMOKE_RELATIVE_DIR = Path("tmp/interactive-codex-smoke")
_CODEX_SMOKE_OUTPUT_FILE = _CODEX_SMOKE_RELATIVE_DIR / "todo-list.js"
_CODEX_SMOKE_RUN_ID = "interactive-codex-smoke"
_PI_SMOKE_RELATIVE_DIR = Path("tmp/interactive-pi-smoke")
_PI_SMOKE_OUTPUT_FILE = _PI_SMOKE_RELATIVE_DIR / "todo-list.js"
_PI_SMOKE_RUN_ID = "interactive-pi-smoke"


@dataclass(frozen=True)
class SmokeHarnessSpec:
    """Layout specification for an interactive smoke harness."""

    agent_name: str
    relative_dir: Path
    output_file: Path
    run_id: str


def _resolve_nanocoder_spec(agent_name: str) -> SmokeHarnessSpec | None:
    """Resolve the nanocoder smoke harness spec, or ``None`` if not nanocoder."""
    if agent_name == "nanocoder":
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_NANOCODER_SMOKE_RELATIVE_DIR,
            output_file=_NANOCODER_SMOKE_OUTPUT_FILE,
            run_id=_NANOCODER_SMOKE_RUN_ID,
        )
    if agent_name.startswith("nanocoder/"):
        suffix = agent_name.removeprefix("nanocoder/")
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_NANOCODER_SMOKE_RELATIVE_DIR,
            output_file=_NANOCODER_SMOKE_OUTPUT_FILE,
            run_id=f"{_NANOCODER_SMOKE_RUN_ID}-{sanitized}",
        )
    return None


def _resolve_headless_claude_spec(agent_name: str) -> SmokeHarnessSpec:
    """Resolve the headless-Claude smoke harness spec.

    The headless-Claude smoke harness shares the same scenario as
    the interactive smoke (todo-list.js output, run-id prefix
    ``headless-claude-smoke-``) so the two transports can be checked
    by one shared scenario rather than two divergent ones (per R8
    of wt-04-claude-parsing). ``<model>`` (e.g. ``haiku``) is
    sanitized into the run_id so two concurrent headless smoke
    runs with different model aliases do not collide on the
    completion-sentinel / receipt paths.
    """
    suffix = agent_name.removeprefix("claude-headless/")
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
    run_id = (
        _HEADLESS_CLAUDE_SMOKE_RUN_ID
        if not sanitized
        else f"{_HEADLESS_CLAUDE_SMOKE_RUN_ID}-{sanitized}"
    )
    return SmokeHarnessSpec(
        agent_name=agent_name,
        relative_dir=_HEADLESS_CLAUDE_SMOKE_RELATIVE_DIR,
        output_file=_HEADLESS_CLAUDE_SMOKE_OUTPUT_FILE,
        run_id=run_id,
    )


def resolve_smoke_harness_spec(agent_name: str) -> SmokeHarnessSpec:
    """Return the smoke harness layout for ``agent_name``.

    The ``claude/haiku`` branch preserves the legacy layout so existing
    on-disk artifacts and tests are not orphaned. The ``agy/<model>`` branch
    uses a separate ``tmp/interactive-agy-smoke`` directory so the two
    harnesses can run side by side without collisions.
    """
    if agent_name == _INTERACTIVE_AGENT:
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_SMOKE_RELATIVE_DIR,
            output_file=_SMOKE_OUTPUT_FILE,
            run_id=_SMOKE_RUN_ID,
        )
    if agent_name.startswith("claude-headless/"):
        return _resolve_headless_claude_spec(agent_name)
    if agent_name.startswith("agy/"):
        model = agent_name.removeprefix("agy/")
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model).strip("-")
        run_id = f"interactive-agy-smoke-{sanitized}"
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_AGY_SMOKE_RELATIVE_DIR,
            output_file=_AGY_SMOKE_OUTPUT_FILE,
            run_id=run_id,
        )
    nanocoder_spec = _resolve_nanocoder_spec(agent_name)
    if nanocoder_spec is not None:
        return nanocoder_spec
    if agent_name == "cursor" or agent_name.startswith("cursor/"):
        # Bare ``cursor`` uses the base cursor harness layout so on-disk
        # artifacts stay co-located with the shared output; ``cursor/<model>``
        # branches off a sanitized run_id so two smoke runs with different
        # model aliases do not collide on completion-sentinel / receipt paths.
        suffix = agent_name.removeprefix("cursor")
        suffix = suffix.lstrip("/")
        if not suffix:
            run_id = "interactive-cursor-smoke"
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"interactive-cursor-smoke-{sanitized}"
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_CURSOR_SMOKE_RELATIVE_DIR,
            output_file=_CURSOR_SMOKE_OUTPUT_FILE,
            run_id=run_id,
        )
    if agent_name == "opencode" or agent_name.startswith("opencode/"):
        # ``opencode/<provider>/<model>`` (e.g.
        # ``opencode/minimax/MiniMax-M3``) carries BOTH the
        # provider and the model, so one alias selects the full routing
        # target. The command builder strips the leading ``opencode/`` and
        # passes ``<provider>/<model>`` to ``opencode run --model``, which is
        # exactly the ``provider/model`` form the CLI expects. A sanitized
        # run_id keeps two provider/model smoke runs from colliding on
        # completion-sentinel / receipt paths.
        suffix = agent_name.removeprefix("opencode").lstrip("/")
        if not suffix:
            run_id = _OPENCODE_SMOKE_RUN_ID
            relative_dir = _OPENCODE_SMOKE_RELATIVE_DIR
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_OPENCODE_SMOKE_RUN_ID}-{sanitized}"
            # Receipts and sentinels were alias-scoped already, but the
            # workspace output was shared. Scope it by the same normalized
            # alias so one concurrent provider smoke cannot unlink or satisfy
            # another provider's file assertion.
            relative_dir = _OPENCODE_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=run_id,
        )
    if agent_name == "codex" or agent_name.startswith("codex/"):
        # ``codex/<model>`` (e.g. ``codex/gpt-5-flash``) follows the
        # same dynamic-alias pattern as ``agy/<model>`` -- the
        # command builder strips the leading ``codex/`` and passes
        # ``<model>`` to ``codex exec``. A sanitized run_id keeps
        # two concurrent model smoke runs from colliding on
        # completion-sentinel / receipt paths.
        suffix = agent_name.removeprefix("codex").lstrip("/")
        if not suffix:
            run_id = _CODEX_SMOKE_RUN_ID
            relative_dir = _CODEX_SMOKE_RELATIVE_DIR
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_CODEX_SMOKE_RUN_ID}-{sanitized}"
            relative_dir = _CODEX_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=run_id,
        )
    if agent_name == "pi" or agent_name.startswith("pi/"):
        # ``pi`` (bare) uses the base pi harness layout; ``pi/<model>``
        # branches off a sanitized run_id so two concurrent model
        # smoke runs do not collide on completion-sentinel / receipt
        # paths. Mirrors the ``cursor`` layout.
        suffix = agent_name.removeprefix("pi").lstrip("/")
        if not suffix:
            run_id = _PI_SMOKE_RUN_ID
            relative_dir = _PI_SMOKE_RELATIVE_DIR
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_PI_SMOKE_RUN_ID}-{sanitized}"
            relative_dir = _PI_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=run_id,
        )
    raise ValueError(f"No smoke harness spec defined for agent '{agent_name}'")


_SMOKE_IDLE_TIMEOUT_SECONDS = 30.0
_SMOKE_MAX_SESSION_SECONDS = 120.0
# Per-agent session ceiling overrides. AGY's default --print-timeout is 5m
# (measured in tmp/agy-source-of-truth.txt); give it a 6m ceiling so the smoke
# harness does not kill a run that AGY still considers active.
_AGENT_SESSION_CEILINGS = {  # bounded-accumulator-ok: static per-agent ceiling map, never mutated
    "claude": 120.0,
    "agy": 360.0,
    "opencode": 360.0,
}
_SMOKE_MAX_TURNS = 5
_SMOKE_TRANSCRIPT_MAX_LINES = 400
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
# Visible-output ceiling for the operator-rendered harness surface.
# Interactive-Claude renders one operator-visible line per ~assistant turn
# (debounced deltas, content-block flushes collapsed), so 80 lines is a
# comfortable upper bound for that transport. Headless Claude
# (``claude -p --output-format=stream-json --include-partial-messages``)
# surfaces every partial-thinking tick and every ``content_block_delta``
# as its own rendered line by design (operators WANT to see the stream);
# collapsing those into fewer lines would hide the subagent result and the
# observed dispatched/result/post-activity evidence the smoke report
# depends on. The headless ceiling is sized for an observed mean of ~100-130
# lines on Haiku with --subagents (per
# ``.agent/tmp/claude_headless_haiku_subagents_report.txt``). Reading 0.25
# --- headless is 2x interactive (linear with the depth of streamed
# partial messages) --- is operator-acceptable; reading anything more is
# a regression in the display's debouncer.
_MAX_VISIBLE_OUTPUT_LINES_BY_AGENT: dict[str, int] = {  # bounded-accumulator-ok: static per-agent ceiling map, never mutated
    "claude-headless": 250,
}
_MAX_VISIBLE_OUTPUT_LINES = 80
_SUBAGENT_TOOL_NAMES = frozenset({"agent", "delegate", "spawn_agent", "subagent", "task"})
_DEFAULT_SUBAGENT_PROMPT = (
    "Inspect the requested todo-list API and return two concise edge cases "
    "the main agent should account for. Do not modify files."
)

# Crash-detector patterns are anchored to specific error signatures so that
# incidental words like "crash" in an agent's planning prose do not poison the
# smoke report.
_CRASH_PATTERNS = (
    re.compile(r"^Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"^thread .* panicked at", re.IGNORECASE),
    re.compile(
        r"segmentation fault \(core dumped\)|SIGSEGV|Aborted \(core dumped\)",
        re.IGNORECASE,
    ),
    re.compile(r"^fatal:\s", re.IGNORECASE),
)

# AGY's operational log often explains why --print returned no stdout. The
# smoke detector reads the tail of this file to surface actionable diagnostics.
_AGY_CLI_LOG_PATH: Path = Path.home() / ".gemini" / "antigravity-cli" / "cli.log"


@dataclass(frozen=True)
class SmokeRunResult:
    """Observed results from the interactive Claude smoke run.

    Evidence Provenance (F1): ``explicit_completion_seen``, ``tool_activity_seen``
    and ``artifact_submitted`` are :class:`~ralph.pipeline.plumbing.smoke_evidence.Evidence`
    values, not bare ``bool``. Each carries the :class:`~ralph.pipeline.plumbing.smoke_evidence.Provenance`
    the harness graded it at and a human-readable ``detail`` explaining why.
    Read ``.holds`` for the boolean question "did this fact hold at all";
    read ``.provenance`` for "how much should an operator trust it". The
    report's overall verdict is a pure function of the weakest provenance
    among these three required facts (see ``smoke_evidence.grade_verdict``) —
    it is never derived from ``.holds`` alone, so a run can satisfy every
    boolean check and still report ``DEGRADED``.
    """

    agent_name: str
    transport: str
    output_file: Path
    file_created: bool
    session_id: str | None
    explicit_completion_seen: Evidence
    raw_line_count: int
    parsed_event_count: int
    tool_activity_seen: Evidence
    artifact_submitted: Evidence
    meaningful_output_lines: list[str]
    errors: list[str]
    subagents_requested: bool = False
    subagent_dispatch_count: int = 0
    subagent_dispatch_seen: bool = False
    subagent_result_seen: bool = False
    post_subagent_activity_seen: bool = False
    #: F3: the maximum Provenance this transport's advertised tools could
    #: possibly reach, inferred from the run's ``init``-shaped frame before
    #: the wire ledger is consulted. ``Provenance.ABSENT`` when no such
    #: frame was found in the transcript (e.g. a non-AGY transport whose
    #: parser does not surface one yet).
    transport_evidence_ceiling: Provenance = Provenance.ABSENT
    #: S-5: frozenset of capabilities the display's
    #: :class:`CapabilityObservationRecorder` actually rendered during
    #: the run. Empty when no ``display`` was threaded through
    #: ``SmokeRunParams``. Defaults to ``frozenset()`` so existing
    #: test constructions keep passing.
    observed_capabilities: frozenset[DisplayCapability] = frozenset()
    #: Multimodal scenario flag: True when this run was driven by the
    #: multimodal prompt. ``False`` (default) means every existing smoke
    #: run is unchanged -- the multimodal break detector is no-op.
    multimodal_requested: bool = False
    #: The geometry the prompt told the agent to read -- persisted so the
    #: operator report can cite the same geometry the grader recomputes
    #: the sha256 over. ``None`` when ``multimodal_requested`` is False.
    multimodal_fixture_size: tuple[int, int] | None = None
    #: The graded multimodal fact: did the agent actually use the media
    #: endpoint? Provenance is
    #: :class:`~ralph.pipeline.plumbing.smoke_evidence.Provenance`.
    #: ``absent(...)`` when ``multimodal_requested`` is False so the
    #: dataclass always carries a non-None value and can sit on a
    #: result the conformance matrix renders without special-casing.
    multimodal_tool_used: Evidence | None = None


@dataclass(frozen=True)
class SubagentSmokeEvidence:
    """Ordered subagent lifecycle evidence parsed from a smoke transcript."""

    dispatch_count: int = 0
    dispatch_seen: bool = False
    result_seen: bool = False
    post_result_activity_seen: bool = False


type EnvGetter = Callable[[str], str | None]


# --- F4: conformance matrix across transports -------------------------------
#
# The graded gate (F1) answers "is this run trustworthy" for one transport at
# a time. F4 turns repeated single-transport runs into the shippable
# artefact the brief asks for: a durable, cross-transport table naming which
# runtimes reach Ralph's tools natively, which need a dispatcher, and which
# can only ever run degraded -- on which specific contract fact. Each smoke
# run updates its own transport's row; the matrix accumulates across the
# separate, expensive, manual runs an operator makes over time (one CLI
# invocation drives one transport per run).

#: Canonical column/row order (brief F4: "today ``claude``, ``agy``,
#: ``nanocoder``, ``cursor`` and ``opencode``"). These are the five
#: transports with a registered ``ralph smoke-interactive-*`` CLI command
#: (``ralph/cli/main.py``). A transport observed outside this set (a
#: future addition, or a stale value) is still recorded -- it is simply
#: sorted after the canonical five. See
#: ``tests/test_conformance_matrix.py:test_canonical_tuple_matches_registered_smoke_commands``
#: for the regression that locks this invariant in.
CONFORMANCE_MATRIX_TRANSPORT_ORDER: Final[tuple[str, ...]] = (
    "claude",
    "codex",
    "agy",
    "pi",
    "nanocoder",
    "cursor",
    "opencode",
)

#: The three required contract facts F1's verdict is graded from (mirrors
#: ``ralph.cli.commands.smoke._required_evidence``). Kept as an explicit
#: tuple (not derived from ``SmokeRunResult``'s fields) so the matrix's
#: column set is a documented contract, not an accidental dataclass shape.
CONFORMANCE_MATRIX_FACTS: Final[tuple[str, ...]] = (
    "artifact_submitted",
    "explicit_completion_seen",
    "tool_activity_seen",
)

#: Canonical agent-identity order for the capability table (S-6). The
#: capability matrix is keyed by *agent identity*, not by transport
#: enum, so that ``claude`` and ``claude-headless`` -- which share the
#: same binary but exercise different transports -- appear as
#: distinguishable rows. The full set of eight built-ins appears here
#: so an operator can read which agent implements what at a glance,
#: matching the plan's AC-6.
CANONICAL_CAPABILITY_AGENT_ORDER: Final[tuple[str, ...]] = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "agy",
    "pi",
    "cursor",
)

#: Durable JSON store (source of truth) and its rendered markdown sibling.
#: Follows the ``run_time_report`` pattern (a runtime-generated file written
#: directly to ``.agent/artifacts/``, never submitted through
#: ``ralph_submit_md_artifact`` -- see ``.agent/artifact-formats/
#: run_time_report.md``): no existing artifact type fits a cross-transport
#: matrix, and adding one is out of this step's scope (checked against
#: ``.agent/artifact-formats/artifact_formats_index.md`` first, per the
#: plan -- recorded here as the implementation note the plan asks for).
_CONFORMANCE_MATRIX_JSON_RELPATH: Final[str] = ".agent/artifacts/smoke_conformance_matrix.json"
_CONFORMANCE_MATRIX_MD_RELPATH: Final[str] = ".agent/artifacts/smoke_conformance_matrix.md"


def conformance_matrix_paths(workspace_root: Path) -> tuple[Path, Path]:
    """Return ``(json_path, markdown_path)`` for the durable conformance matrix."""
    return (
        workspace_root / _CONFORMANCE_MATRIX_JSON_RELPATH,
        workspace_root / _CONFORMANCE_MATRIX_MD_RELPATH,
    )


ConformanceMatrix = dict[str, dict[str, Evidence]]


def _evidence_to_json(evidence: Evidence) -> dict[str, object]:
    return {
        "holds": evidence.holds,
        "provenance": evidence.provenance.name,
        "detail": evidence.detail,
    }


def _evidence_from_json(payload: object) -> Evidence | None:
    if not isinstance(payload, dict):
        return None
    payload_dict = cast("dict[str, object]", payload)
    holds = payload_dict.get("holds")
    provenance_name = payload_dict.get("provenance")
    detail = payload_dict.get("detail")
    if not isinstance(holds, bool) or not isinstance(provenance_name, str):
        return None
    try:
        provenance = Provenance[provenance_name]
    except KeyError:
        return None
    return Evidence(
        holds=holds,
        provenance=provenance,
        detail=detail if isinstance(detail, str) else "",
    )


def load_conformance_matrix(
    json_path: Path,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> ConformanceMatrix:
    """Return the durable matrix at ``json_path``, or an empty matrix.

    Read uncertainty (missing file, unreadable, malformed JSON, a row/cell
    that does not parse as :class:`Evidence`) is fail-open -- the run that
    reads a corrupt matrix reports what it can and rebuilds the file from
    this run's own evidence, mirroring ``write_text_if_changed``'s
    fail-open contract for the write side. Reads through ``backend`` (not a
    raw ``Path.read_text``) so the whole matrix pipeline is testable against
    an in-memory ``FileBackend`` -- no real filesystem I/O required.
    """
    try:
        raw = backend.read_text(json_path, encoding="utf-8")
    except (KeyError, OSError, UnicodeDecodeError):
        return {}
    try:
        payload = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    matrix: ConformanceMatrix = {}
    for transport, row in cast("dict[str, object]", payload).items():
        if not isinstance(transport, str) or not isinstance(row, dict):
            continue
        parsed_row: dict[str, Evidence] = {}
        for fact, cell in cast("dict[str, object]", row).items():
            evidence = _evidence_from_json(cell)
            if evidence is not None:
                parsed_row[fact] = evidence
        if parsed_row:
            matrix[transport] = parsed_row
    return matrix


def update_conformance_matrix(
    matrix: ConformanceMatrix,
    *,
    transport: str,
    evidence: Mapping[str, Evidence],
) -> ConformanceMatrix:
    """Return a NEW matrix with ``transport``'s row replaced by ``evidence``.

    Pure: ``matrix`` is never mutated. A later run for the same transport
    replaces its row wholesale (the matrix reports each transport's most
    recently observed grade, not a history), so a fixed transport can never
    accumulate stale facts from an earlier, differently-shaped run.
    """
    updated = {key: dict(row) for key, row in matrix.items()}
    updated[transport] = dict(evidence)
    return updated


def _ordered_transports(matrix: ConformanceMatrix) -> list[str]:
    canonical = [t for t in CONFORMANCE_MATRIX_TRANSPORT_ORDER if t in matrix]
    extra = sorted(t for t in matrix if t not in CONFORMANCE_MATRIX_TRANSPORT_ORDER)
    return canonical + extra


def render_conformance_matrix_markdown(matrix: ConformanceMatrix) -> str:
    """Render the durable, operator-facing conformance matrix (F4).

    One row per transport observed so far, one column per required contract
    fact (F1), each cell naming the :class:`Provenance` grade that
    transport's most recent run achieved for that fact. A transport with no
    recorded run for a given fact renders ``ABSENT`` (never blank -- the
    matrix's whole purpose is to make an unproven fact visible, not silent).
    """
    lines = [
        "# Ralph smoke-gate conformance matrix",
        "",
        "Per-transport, per-fact Evidence Provenance grade (F4). Regenerated "
        "by `ralph smoke-interactive-*`; each run replaces only its own "
        "transport's row.",
        "",
    ]
    if not matrix:
        lines.append("No smoke runs recorded yet.")
        return "\n".join(lines).rstrip() + "\n"
    header = ["Transport", *CONFORMANCE_MATRIX_FACTS]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for transport in _ordered_transports(matrix):
        row = matrix[transport]
        cells = []
        for fact in CONFORMANCE_MATRIX_FACTS:
            evidence = row.get(fact)
            if evidence is None:
                cells.append(Provenance.ABSENT.name)
            else:
                holds_marker = "holds" if evidence.holds else "absent"
                cells.append(f"{evidence.provenance.name} ({holds_marker})")
        lines.append(f"| {transport} | " + " | ".join(cells) + " |")
    return "\n".join(lines).rstrip() + "\n"


def render_capability_matrix_markdown(
    *,
    capability_rows: dict[str, tuple[DisplayCapabilityStance, ...]] | None = None,
) -> str:
    """Render the operator-facing display-capability matrix (S-4 / S-6).

    One row per built-in agent, one column per catalog-derived
    :class:`DisplayCapability`. Each cell shows the agent's declared
    stance verbatim (``SUPPORTED`` / ``NOT_APPLICABLE (<reason>)`` /
    ``UNIMPLEMENTED (<reason>)``). The matrix is sourced from the
    built-in :class:`AgentSupport` declarations so an operator can
    read which agent implements what without reading code.

    The matrix is total and fail-closed: the underlying
    :func:`ralph.agents.display_capabilities.all_display_capabilities`
    vocabulary is the source of truth, and every built-in agent must
    declare a stance for every entry. The matrix render is therefore
    driven by those declarations; an empty ``capability_rows`` mapping
    means the caller has not yet loaded the built-ins.

    The rendered table is meant to live in
    ``.agent/artifacts/smoke_conformance_matrix.md`` below the
    evidence-provenance table, so an operator can see both
    "what the latest run graded" and "what the agent declares it
    supports" in the same file.
    """
    from ralph.agents.builtin import (
        builtin_supports,  # reason: lazy import breaks plumbing<-> ->builtin cycle
    )
    from ralph.agents.display_capabilities import (  # reason: lazy import breaks plumbing<-> ->display_capabilities cycle
        all_display_capabilities,
    )

    capabilities = tuple(all_display_capabilities())
    rows = capability_rows if capability_rows is not None else {
        support.name: support.display_capabilities for support in builtin_supports()
    }
    lines = [
        "## Display-capability declarations (S-4)",
        "",
        "Per-agent tri-state stance for each catalog-derived "
        "display capability. ``SUPPORTED`` means the agent's parser "
        "produces a metadata envelope the canonical "
        "`payload_from_tool_event` recognizes and `build_edit_preview` "
        "renders; ``NOT_APPLICABLE`` means the transport is "
        "structurally incapable; ``UNIMPLEMENTED`` means the transport "
        "can produce the surface but the current parser/display path "
        "is not wired to its frame shape. ``NOT_APPLICABLE`` and "
        "`UNIMPLEMENTED` carry a non-empty reason.",
        "",
    ]
    if not rows:
        lines.append("No built-in capability declarations recorded yet.")
        return "\n".join(lines).rstrip() + "\n"
    header = ["Agent", *(c.name for c in capabilities)]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    ordered = [name for name in CANONICAL_CAPABILITY_AGENT_ORDER if name in rows]
    ordered.extend(sorted(name for name in rows if name not in CANONICAL_CAPABILITY_AGENT_ORDER))
    for agent_name in ordered:
        stances = rows[agent_name]
        stance_by_capability = {stance.capability: stance for stance in stances}
        cells = []
        for capability in capabilities:
            stance = stance_by_capability.get(capability)
            if stance is None:
                cells.append("UNSPECIFIED")
            else:
                cells.append(stance.label())
        lines.append(f"| {agent_name} | " + " | ".join(cells) + " |")
    return "\n".join(lines).rstrip() + "\n"


def record_conformance_matrix(
    workspace_root: Path,
    *,
    transport: str,
    evidence: Mapping[str, Evidence],
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> Path:
    """Update and persist the durable conformance matrix for one run (F4).

    Loads the existing matrix, replaces ``transport``'s row with ``evidence``,
    writes the JSON source of truth and the rendered markdown sibling (both
    idempotently, via :func:`write_text_if_changed`), and returns the
    markdown path -- the durable artefact an operator reads. ``backend``
    defaults to the real filesystem (:data:`DEFAULT_FILE_BACKEND`) but
    accepts an in-memory :class:`FileBackend` so the whole update-and-persist
    pipeline is testable without real file I/O.

    The markdown sibling composes the per-fact evidence table (F1)
    with the per-agent display-capability declarations (S-4) so an
    operator reading the file sees both "what the latest run graded"
    and "what the agent declares it supports" in the same view.
    """
    json_path, md_path = conformance_matrix_paths(workspace_root)
    matrix = load_conformance_matrix(json_path, backend=backend)
    updated = update_conformance_matrix(matrix, transport=transport, evidence=evidence)
    json_payload = {
        row_transport: {fact: _evidence_to_json(ev) for fact, ev in row.items()}
        for row_transport, row in updated.items()
    }
    write_text_if_changed(
        backend,
        json_path,
        json.dumps(json_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(json_path.parent, parents=True, exist_ok=True),
    )
    md_body = render_conformance_matrix_markdown(updated) + render_capability_matrix_markdown()
    write_text_if_changed(
        backend,
        md_path,
        md_body,
        encoding="utf-8",
        prepare_write=lambda: backend.mkdir(md_path.parent, parents=True, exist_ok=True),
    )
    return md_path


def _build_smoke_prompt(
    output_relpath: str,
    *,
    submit_artifact_tool_name: str,
    transport: AgentTransport | None = None,
    subagents: bool = False,
    subagent_prompt: str | None = None,
    multimodal: bool = False,
    multimodal_fixture_relpath: str | None = None,
) -> str:
    """Return the prompt used for the parity smoke test.

    When ``multimodal`` is True the prompt additionally appends the
    contract bullets from
    :func:`ralph.pipeline.plumbing.smoke_multimodal.multimodal_prompt_requirements`
    so the agent is told to actually dial the media endpoint, replay
    the server-minted handle, and write the receipt / geometry /
    sha256 tokens into the output file. The default ``multimodal=False``
    path renders the existing prompt byte-for-byte so every current
    smoke run is unchanged.
    """
    artifact_document = (
        "---\n"
        "type: smoke_test_result\n"
        "status: passed\n"
        f"output_file: {output_relpath}\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] The smoke test completed successfully.\n"
        "\n"
        "## Observed Working\n"
        "\n"
        "- [OK-1] Created todo-list.js.\n"
        "- [OK-2] Submitted the smoke test result.\n"
        "\n"
        "## Headless Guide Checks\n"
        "\n"
        "- [HG-1] Session capture.\n"
        "- [HG-2] Tool activity.\n"
        "- [HG-3] Completion signal.\n"
        "- [HG-4] Parser events.\n"
        "- [HG-5] Tmp artifact creation."
    )

    subagent_requirements = ""
    if subagents:
        delegated_task = subagent_prompt or _DEFAULT_SUBAGENT_PROMPT
        subagent_requirements = (
            "- Before creating the file, delegate exactly one bounded, read-only task "
            "to the agent runtime's native subagent tool. Give the subagent this task:\n"
            f"  {delegated_task.strip()}\n"
            "- Wait for the subagent result. After the subagent result, the main agent "
            "must perform another meaningful tool action itself before submitting the "
            "artifact and completing.\n"
        )

    transport_requirement = (
        f"- Use the tool names exposed by the `{transport.value}` transport exactly.\n"
        if transport is not None
        else ""
    )
    # DA-001: a live opencode/minimax/MiniMax-M3 run repeatedly hallucinated
    # a bare `read_file` tool name (never advertised) instead of the
    # actually-exposed `ralph_read_file`, producing a hard tool-call error
    # the model then never recovered from -- no file created, no artifact
    # submitted, no completion. The generic transport_requirement bullet
    # above measurably did not prevent this. Naming the exact prefix and a
    # concrete example (mirroring the AGY dispatcher-hint pattern below) is
    # the same fix shape already proven to correct a live-model tool-naming
    # miss for a different transport.
    opencode_tool_naming_requirement = (
        "- This transport exposes Ralph's tools with an explicit `ralph_` "
        "prefix (for example `ralph_read_file`, `ralph_write_file`, "
        "`ralph_edit_file`, `ralph_create_directory`). There is no bare "
        "`read_file` / `write_file` / `edit_file` tool -- always call the "
        "`ralph_`-prefixed name exactly as listed in your available tools.\n"
        if transport is AgentTransport.OPENCODE
        else ""
    )
    # AGY does not list Ralph's tools directly (A1): its init frame advertises
    # only the generic `call_mcp_tool` dispatcher (confirmed by a live
    # v1.1.10 capture, never guessed -- see tests/display/_fixtures/
    # agy_wire_provenance.md). A plain "call `{tool}`" bullet plus a
    # permissive "if unavailable, write the file instead" fallback bullet
    # measurably teaches the model nothing: a live v1.1.10 run against that
    # phrasing still took the fallback-file path on every turn (the
    # 2026-08-05-shaped defect this branch replaces). So for AGY the
    # submission, fallback, and completion bullets are rewritten in one
    # piece -- naming `call_mcp_tool` as the *only* first attempt, and
    # narrowing the fallback to a genuine dispatcher error, not mere
    # unfamiliarity with the target tool's name. The exact JSON argument
    # shape for `call_mcp_tool` itself is part of AGY's own tool schema
    # (already visible to the model), so it is deliberately not hand-typed
    # here -- doing so would risk asserting an unmeasured shape.
    is_agy = transport is AgentTransport.AGY
    # S-7 / C1 / DoD 13: the AGY-shaped dispatcher hint lives in a
    # shared helper so the smoke prompt and the master prompt cannot
    # drift (both routes must name the same tool name, server name,
    # and "call_mcp_tool is the only first attempt" rule).
    from ralph.prompts._mcp_dispatcher_hint import agy_dispatcher_hint_text

    submit_call_instruction = (
        (
            agy_dispatcher_hint_text(submit_artifact_tool_name)
            + f"\n- Pass `artifact_type=\"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}\"` and "
            "this complete Markdown document as the content argument when "
            "you make the call. The artifact body is given to you below:\n"
        )
        if is_agy
        else f"- Call `{submit_artifact_tool_name}` with "
        f'artifact_type="{SMOKE_TEST_RESULT_ARTIFACT_TYPE}" '
        "and put this complete Markdown document in the content argument:\n"
    )
    submission_fallback_instruction = (
        "- Only write the same complete Markdown document to "
        "`.agent/tmp/smoke_test_result.md` as a fallback if `call_mcp_tool` itself "
        f"errors when targeting server `{RALPH_MCP_SERVER_NAME}` (for example, the "
        "server is unreachable) -- not merely because "
        f"`{submit_artifact_tool_name}` is absent from your directly listed tools. "
        "Ralph Workflow validates and promotes that fallback, but a genuine "
        "`call_mcp_tool` attempt against the `ralph` server always comes first. "
        "Do not write the canonical artifact directly.\n"
        if is_agy
        else "- Submit through the tool when it is available. If the submission tool is "
        "unavailable, write the same complete Markdown document to "
        "`.agent/tmp/smoke_test_result.md`; Ralph Workflow validates and promotes that "
        "fallback. Do not write the canonical artifact directly.\n"
    )
    # NOTE (measured, not guessed): a live v1.1.10 replay that also routed
    # `declare_complete` through a second `call_mcp_tool` invocation in the
    # same turn did not return -- the run had to be killed after an
    # inactivity timeout, losing even the artifact submission that had
    # already succeeded moments earlier. Unlike the submission instruction
    # above (which is proven to work: a live run produced a real
    # `call_mcp_tool` round trip and a genuine tool result), a second
    # dispatcher round trip for completion is not safe to require. Completion
    # is therefore left on the plain `declare_complete` phrasing for every
    # transport, including AGY: per F7 / DoD 19, the host writes completion
    # evidence for no transport; when the model cannot reach
    # ``declare_complete`` directly, the run grades
    # ``DEGRADED (host-synthesized-or-below)`` and the gate fails, rather
    # than hanging the run.
    completion_requirement = (
        "- After the artifact tool returns a valid receipt, call `declare_complete` "
        "as the mandatory final action. The receipt is not phase completion; do not "
        "stop until the completion call succeeds.\n"
    )

    multimodal_requirements = ""
    if multimodal:
        from ralph.pipeline.plumbing.smoke_multimodal import multimodal_prompt_requirements
        fixture_relpath = multimodal_fixture_relpath or "smoke-fixture.png"
        multimodal_requirements = "\n" + multimodal_prompt_requirements(fixture_relpath)

    return (
        "Create a small JavaScript todo list implementation at "
        f"`{output_relpath}`.\n\n"
        "Requirements:\n"
        "- Keep it tiny: one file only.\n"
        "- Export a small in-memory todo list API.\n"
        "- Do not touch files outside tmp/.\n"
        "- Use the headless semantic guide as a rubric: session capture, tool activity, "
        "completion signal, parser events, and tmp artifact creation.\n"
        f"{transport_requirement}"
        f"{opencode_tool_naming_requirement}"
        f"{subagent_requirements}"
        f"{submit_call_instruction}"
        f"```markdown\n{artifact_document}\n```\n"
        f"{submission_fallback_instruction}"
        "- Do not start background work, run verification, or wait for other tasks; "
        "finish this small smoke task in this turn.\n"
        f"{completion_requirement}"
        f"{multimodal_requirements}"
    )


def _normalized_tool_name(metadata: dict[str, object]) -> str:
    """Return the tool name carried on ``metadata`` in a normalized lowercase form.

    Parsers across transports expose the tool name under different keys:

    - ``metadata["tool"]`` is the canonical slot (:class:`CodexParser`,
      :class:`CursorParser`, :class:`PiParser`, :class:`GeminiParser`,
      :class:`AgyParser`, :class:`OpenCodeParser`, the Claude interactive
      transcript parser, and the headless-Claude ``claude -p`` parser for
      the prefixed-transcript branch).
    - The headless-Claude NDJSON parser
      (:class:`ClaudeParser._parse_message_content`) threads the raw
      ``content_block`` dict as ``metadata`` -- ``name`` is the field
      carrying the tool name on Claude's content blocks (alongside
      ``type``, ``id``, ``input``); ``tool`` is not present on the block.
    - Some Pi code paths and legacy wire shapes emit ``tool_name`` (or
      camelCase ``toolName``), so fall through those for forward-compat.

    Falling through to ``name`` is what lets the headless-Claude
    parser's ``tool_use`` blocks participate in the subagent-dispatch
    detection wired by :data:`_SUBAGENT_TOOL_NAMES`; without the
    fall-through the smoke report surfaces ``subagent dispatch was not
    observed`` even when the transcript plainly dispatched an ``Agent``
    call. The canonical helper in
    :mod:`ralph.agents.parsers._template.summarize_agent_output_tool`
    uses the same ordered fall-through; keep them in lockstep so a
    future parser that emits a new key is handled consistently on both
    sides.
    """
    for key in ("tool", "tool_name", "toolName", "name"):
        raw_name = metadata.get(key)
        if isinstance(raw_name, str) and raw_name.strip():
            return raw_name.strip().lower()
    return ""


def _tool_use_id(metadata: dict[str, object]) -> str | None:
    for key in (
        "tool_use_id",
        "call_id",
        "toolCallId",
        "callID",
        "callId",
        # Headless-Claude content blocks carry the call id under the wire's
        # ``id`` key (the same key the interactive transcript parser maps to
        # ``tool_use_id`` upstream). Falling through ``id`` here lets the
        # helper pull the same correlation id from a headless-Claude raw
        # block without forcing the parser to rewrite the metadata.
        "id",
    ):
        raw_id = metadata.get(key)
        if isinstance(raw_id, str) and raw_id:
            return raw_id
    nested = metadata.get("tool_call")
    if isinstance(nested, dict):
        nested_id = nested.get("toolCallId")
        if isinstance(nested_id, str) and nested_id:
            return nested_id
    # OpenCode carries the call id under ``part.callID`` (see the OpenCode
    # parser's ``_tool_metadata``, which preserves the raw ``part``).
    part = metadata.get("part")
    if isinstance(part, dict):
        for key in ("callID", "callId", "id"):
            part_id = part.get(key)
            if isinstance(part_id, str) and part_id:
                return part_id
    return None


def _subagent_smoke_evidence(
    config: AgentConfig,
    lines: list[str],
) -> SubagentSmokeEvidence:
    """Return ordered, parser-derived evidence for the subagent smoke scenario.

    Plan (Improve AGY parsing fidelity, S-4): the contract is relaxed from
    "exactly one dispatch" to "at least one dispatch, each with a correlated
    result". A real multi-subagent AGY run dispatches more than one subagent
    in a single frame (and the AGY parser correctly classifies
    ``define_subagent`` / ``manage_subagents`` as ordinary tool calls, not
    subagent dispatches, so they no longer inflate this count) -- the
    previous "== 1" rule made a real two-subagent run report ``no`` on every
    transport, not just AGY.

    Dispatches are counted by DISTINCT call id, not by raw tool_use events.
    OpenCode may stream a running state then a completed state for the same
    call, and a completed tool now surfaces both a dispatch and a result --
    both carry the same callID, so counting raw events would see one
    subagent twice. Id-less dispatches (a parser that exposes no id) cannot
    be de-duplicated or correlated by id, so they are tracked by count only.
    """
    parser = get_parser(_parser_key_for_config(config))
    dispatch_ids: set[str] = set()
    idless_dispatch_count = 0
    resulted_ids: set[str] = set()
    idless_result_count = 0
    any_result_seen = False
    post_result_activity_seen = False
    for parsed in parser.parse(iter(lines)):
        metadata = parsed.metadata or {}
        tool_name = _normalized_tool_name(metadata)
        if parsed.type == "tool_use" and tool_name in _SUBAGENT_TOOL_NAMES:
            tool_id = _tool_use_id(metadata)
            if tool_id is None:
                idless_dispatch_count += 1
            else:
                dispatch_ids.add(tool_id)
            continue
        if parsed.type == "tool_result":
            result_id = _tool_use_id(metadata)
            # A ``tool_result`` correlates to a known subagent dispatch in
            # two ways (both keep the S-4 "every dispatch has its own
            # correlated result" invariant intact):
            #
            #   (a) The result's tool name is one of the canonical
            #       subagent dispatch names (``Agent``, ``Task``,
            #       ``delegate``, ``spawn_agent``, ``subagent``). This
            #       path covers parsers (the Claude interactive parser,
            #       Codex, Pi) that attach ``tool`` / ``name`` to the
            #       result block too.
            #   (b) The result's ``tool_use_id`` matches a dispatch we
            #       already saw. This path covers parsers (the headless
            #       Claude ``claude -p`` ``ClaudeParser``) whose
            #       ``tool_result`` block only carries ``type``,
            #       ``tool_use_id``, and ``content`` -- no ``name``.
            #       Correlation by id is exact (a tool_use that
            #       dispatched ``Agent`` with ``id=toolu_X`` only
            #       matches the ``tool_result`` whose
            #       ``tool_use_id == toolu_X``), so this preserves the
            #       "one dispatch, one correlated result" guarantee.
            correlate_by_name = tool_name in _SUBAGENT_TOOL_NAMES
            correlate_by_id = (
                result_id is not None and result_id in dispatch_ids
            )
            if correlate_by_name or correlate_by_id:
                if result_id is not None:
                    resulted_ids.add(result_id)
                else:
                    idless_result_count += 1
                any_result_seen = True
            continue
        if any_result_seen and parsed.type in {"text", "thinking", "tool_use"}:
            post_result_activity_seen = True
    dispatch_count = len(dispatch_ids) + idless_dispatch_count
    # Every id-bearing dispatch must have received its own correlated result;
    # id-less dispatches (a parser exposing no id) can only be checked by count.
    all_dispatches_resulted = dispatch_ids.issubset(resulted_ids) and (
        idless_result_count >= idless_dispatch_count
    )
    return SubagentSmokeEvidence(
        dispatch_count=dispatch_count,
        dispatch_seen=dispatch_count > 0,
        result_seen=dispatch_count > 0 and all_dispatches_resulted,
        post_result_activity_seen=post_result_activity_seen,
    )


def _subagent_smoke_error(evidence: SubagentSmokeEvidence) -> str | None:
    """Return the first missing ordered subagent signal, if any.

    S-4: relaxed from "exactly one dispatch" to "at least one dispatch, each
    with a correlated result". A single dispatch whose result id does not
    match keeps the original generic message (no result frame correlated to
    the one dispatch observed); two-or-more dispatches where at least one
    lacks its own correlated result get a distinct message naming the new
    contract, so the two failure shapes remain distinguishable in reports.
    """
    if not evidence.dispatch_seen:
        return "subagent dispatch was not observed"
    if not evidence.result_seen:
        if evidence.dispatch_count > 1:
            return "not every subagent dispatch has a correlated result"
        return "subagent result was not observed"
    if not evidence.post_result_activity_seen:
        return "no meaningful activity was observed after the subagent result"
    return None


def _parser_key_for_config(config: AgentConfig) -> str:
    return resolve_parser_key(
        config.cmd,
        config.json_parser,
        cast(
            "AgentTransport", config.transport
        ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    )


def _count_parsed_events(config: AgentConfig, lines: list[str]) -> int:
    parser = get_parser(_parser_key_for_config(config))
    events = list(parser.parse(iter(lines)))
    return len(events)


def _tool_activity_seen(config: AgentConfig, lines: list[str]) -> bool:
    transport = config.transport
    assert transport is not None
    strategy = strategy_for_command(config.cmd, transport)
    for line in lines:
        signal = strategy.classify_activity_line(line)
        if signal is not None and signal.kind.value == "tool_use":
            return True
    # S-6 (Evidence Provenance G6 / DoD 20): a second authoritative source,
    # applied uniformly to every transport, not a per-transport softening.
    # The execution-strategy classifier above is watchdog-focused, and some
    # transports (e.g. nanocoder's plain GenericExecutionStrategy) have no
    # custom tool-use override -- their execution strategy can never
    # return a tool_use signal even when their transcript genuinely
    # contains one. Their PARSER (a separately-tested classification
    # layer -- NanocoderParser's ``⚒ Executed <tool>`` convention and the
    # shared ``[plain] tool: NAME`` convention both classify as
    # ``type="tool_use"``) already carries this evidence; falling back to
    # it here closes that gap for every transport whose parser can
    # classify tool markers the strategy cannot, not only nanocoder.
    parser = get_parser(_parser_key_for_config(config))
    return any(parsed.type == "tool_use" for parsed in parser.parse(iter(lines)))


def _meaningful_output_lines(config: AgentConfig, lines: list[str]) -> list[str]:
    parser = get_parser(_parser_key_for_config(config))
    collected: list[str] = []
    for parsed in parser.parse(iter(lines)):
        content = parsed.content.strip()
        if parsed.type in {"text", "thinking", "tool_use", "tool_result", "error"} and content:
            collected.append(f"{parsed.type}: {content}")
        if len(collected) >= _MAX_MEANINGFUL_OUTPUT_LINES:
            break
    return collected


def _looks_like_permission_prompt_surface(line: str) -> bool:
    normalized = normalize_vt_text(line).lower()
    if not normalized.strip():
        return False
    if "bypass permissions on" in normalized:
        return False
    has_confirm_footer = "enter to confirm" in normalized or "esc to cancel" in normalized
    prompt_shaped_markers = (
        "claude requested permissions",
        "allow this action?",
        "enable auto mode?",
        "yes, i accept",
        "yes, i trust this folder",
    )
    return has_confirm_footer and any(marker in normalized for marker in prompt_shaped_markers)


def _detect_break_indicators(lines: list[str]) -> list[str]:
    errors: list[str] = []
    if any(_looks_like_permission_prompt_surface(line) for line in lines):
        errors.append("unexpected permission prompt observed in transcript")
    lowered = [line.strip().lower() for line in lines]
    if any(pattern.search(line) for line in lowered for pattern in _CRASH_PATTERNS):
        errors.append("crash-like transcript output observed")
    return errors


def _tool_calls_exercising_capability(
    config: AgentConfig,
    lines: list[str],
    capability: DisplayCapability,
) -> set[str]:
    """Return the set of tool names whose calls should have rendered ``capability``.

    Uses the exact ``payload_from_tool_event`` / ``infer_surface_for_preview``
    sequence the production ``ParallelDisplay._emit_file_preview`` choke point
    applies at ``parallel_display.py:2640-2665``, so the helper cannot diverge
    from production. The OpenCode parser normalizes ``ralph_*`` tool names at
    the transport boundary (``opencode.py:_canonical_tool_name``), so the
    helper sees the canonical ``read_file`` / ``write_file`` / ``edit_file``
    names already and reuses them.

    A tool call whose ``payload_from_tool_event`` returns ``None`` (no
    recognized file activity) or whose surface is not in the
    catalog-derived vocabulary does NOT count as exercising ``capability``.
    A tool call whose surface maps to ``capability`` does.

    The returned set contains the canonical, lowercase tool names emitted by
    the parser (e.g. ``"read_file"``, ``"edit_file"``) so the smoke report's
    break message can name the specific tool that should have rendered.
    """
    parser = get_parser(_parser_key_for_config(config))
    exercising: set[str] = set()
    for parsed in parser.parse(iter(lines)):
        if parsed.type != "tool_use":
            continue
        metadata = parsed.metadata or {}
        raw_name = metadata.get("tool")
        if not isinstance(raw_name, str) or not raw_name:
            continue
        tool_name = raw_name.lower()
        payload = payload_from_tool_event(tool_name, dict(metadata))
        if payload is None:
            continue
        surface_name = infer_surface_for_preview(None, payload.operation)
        observed = surface_to_capability(surface_name)
        if observed is capability:
            # A tool call can only exercise a renderable capability when
            # its payload carries the content the preview builder needs to
            # materialize a render. AGY v1.1.10's ``write_to_file`` event
            # advertises only ``TargetFile`` (no ``content`` key in
            # ``tool_info.parameters``), so ``build_edit_preview`` correctly
            # returns ``None`` and the capability cannot be observed for
            # that call -- counting it as exercising would be a false
            # positive that masks real regressions on content-bearing
            # transports. See ``agy_wire_tool.jsonl`` /
            # ``agy_wire_provenance.md`` for the measured wire format.
            #
            # ``file_preview`` (read) is exempt: the content arrives on the
            # tool-RESULT event, not the tool-USE event this helper scans,
            # so ``payload.content`` is always empty here for reads -- the
            # capability is exercisable whenever the tool call is
            # recognized, regardless of the use-side payload.
            if surface_name == "syntax_preview" and not (
                isinstance(payload.content, str) and payload.content
            ):
                continue
            if surface_name == "diff_preview" and not payload.hunks:
                continue
            exercising.add(tool_name)
    return exercising


def _detect_capability_breaks(
    support: AgentSupport | None,
    observed: frozenset[DisplayCapability],
    config: AgentConfig,
    lines: list[str],
) -> list[str]:
    """Return break messages for SUPPORTED capabilities that did not render.

    Compares each declared ``SUPPORTED`` stance on ``support`` against
    ``observed`` (the per-capability frozenset the display's
    :class:`CapabilityObservationRecorder` collected during the run) and
    emits a break message when the transcript contained a tool call that
    should have rendered the capability but the recorder never saw one.

    Branch A -- ``support is None``: a custom agent that registered no
    display-capability stance gets NO capability grading. A bare
    ``support is None`` is the "nobody has said" condition; the empty
    ``observed`` set is NOT in this branch -- it is the smoking gun, not
    "nothing to grade".

    Branch B -- per declared SUPPORTED stance: for every stance in
    ``support.display_capabilities`` whose ``kind == "supported"``, ask
    ``_tool_calls_exercising_capability`` whether the transcript contained
    a tool call that should have produced a render. If the helper returns a
    non-empty set AND ``capability not in observed``, the run reports a
    break -- the declared capability claimed to render and the run
    actually exercised the relevant tool calls, but the display never
    materialized the render. This branch covers the empty-observed
    case explicitly: an empty ``observed`` paired with a non-empty
    exercising set emits one break per declared SUPPORTED capability.

    Branch C -- return the collected breaks verbatim.

    Returns an empty list when the agent has no supported stances (the
    ``UNIMPLEMENTED`` / ``NOT_APPLICABLE`` stances do not contribute
    breaks -- they describe absence of capability, not absence of render).
    """
    if support is None:
        return []
    breaks: list[str] = []
    for stance in support.display_capabilities:
        if stance.kind != "supported":
            continue
        target = stance.capability
        exercising = _tool_calls_exercising_capability(config, lines, target)
        if exercising and target not in observed:
            tools = ", ".join(sorted(exercising))
            breaks.append(
                f"declared capability {target.name} never rendered despite "
                f"{tools} tool call"
            )
    return breaks





def _nanocoder_prompt_submission_error(
    params: SmokeRunParams,
    lines: list[str],
    artifact_submitted: bool,
) -> str | None:
    if params.config.transport != AgentTransport.NANOCODER or artifact_submitted:
        return None
    normalized = "\n".join(normalize_vt_text(line).lower() for line in lines)
    saw_startup = "welcome to nanocoder" in normalized or "tips for getting started" in normalized
    if not saw_startup:
        return None
    saw_progress = any(
        marker in normalized
        for marker in (
            "tool_use",
            "tool_result",
            "[plain] tool:",
            "smoke_test_result",
            "task declared complete:",
        )
    )
    if saw_progress or params.output_file.exists():
        return None
    return "nanocoder prompt was not submitted after startup banner"


def _execute_smoke_turns(
    params: SmokeRunParams,
    current_session_id: str | None,
    run_id: str = _SMOKE_RUN_ID,
) -> tuple[list[str], list[str], str | None, AgentInvocationError | None]:
    """Execute smoke test turns and return collected lines and state."""
    all_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    live_output_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    final_exception: AgentInvocationError | None = None
    workspace_scope = resolve_workspace_scope(params.workspace_root)
    # S-2 (Evidence Provenance F3): log the evidence ceiling once, the first
    # turn an init-shaped frame is observed, regardless of which branch below
    # observes it — so a single-turn run that never enters the
    # OpenCodeResumableExitError retry branch still surfaces the ceiling
    # before the run's final report table prints.
    ceiling_reported = False

    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        rendered_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        observed_session_id: str | None = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id

        def _observe_raw_line(line: str, *, _turn_raw_lines: deque[str] = raw_lines) -> None:
            # G1/S-1 (Evidence Provenance F3): report the ceiling as soon as
            # an init-shaped frame is observed while lines stream in, not
            # only after execute_agent_effect returns — for the common
            # single-turn run, waiting for the return means the whole
            # turn's tokens are already spent before the operator learns
            # the transport could never pass. raw_lines already contains
            # this line by the time raw_line_sink fires (it is invoked
            # immediately after the append inside stream_parsed_agent_activity),
            # so list(raw_lines) is a faithful up-to-this-line view.
            #
            # ``_turn_raw_lines`` is bound as a default-value parameter
            # (not read directly from the enclosing loop scope) so this
            # closure is pinned to THIS iteration's ``raw_lines`` object,
            # not whatever ``raw_lines`` is rebound to by a later loop
            # iteration (ruff B023: function-definition-in-loop closures
            # over a loop variable capture by reference, not by value).
            del line  # already appended to raw_lines by the time this fires
            nonlocal ceiling_reported
            if ceiling_reported:
                return
            ceiling_reported = _report_evidence_ceiling_once(
                params.config, list(_turn_raw_lines)
            )

        effect = InvokeAgentEffect(
            agent_name=params.agent_name,
            phase="development",
            prompt_file=str(params.prompt_file),
            drain="development",
        )
        pipeline_deps = params.pipeline_deps
        if pipeline_deps is None:
            raise RuntimeError("SmokeRunParams.pipeline_deps is required")
        try:
            event = execute_agent_effect(
                effect,
                params.unified_config,
                pipeline_deps,
                workspace_scope,
                bridge=cast(
                    "RestartAwareMcpBridge", params.bridge
                ),  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
                display_context=params.display_context,
                display=params.display,
                run_id=run_id,
                raw_output_sink=raw_lines,
                rendered_output_sink=rendered_lines,
                raw_line_sink=_observe_raw_line,
                set_session_id_cb=_capture_session_id,
                invoke_agent=invoke_agent,
                raise_resumable_exit=True,
            )
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            if not ceiling_reported:
                ceiling_reported = _report_evidence_ceiling_once(params.config, list(all_lines))
            current_session_id = observed_session_id or extract_transport_session_id(
                tuple(raw_lines)
            )
            final_exception = None
            if event == PipelineEvent.AGENT_SUCCESS:
                break
            # Non-success event from the shared core ends the turn loop.
            break
        except OpenCodeResumableExitError as exc:
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            if not ceiling_reported:
                ceiling_reported = _report_evidence_ceiling_once(params.config, list(all_lines))
            current_session_id = (
                exc.resumable_session_id
                or observed_session_id
                or extract_transport_session_id(tuple(raw_lines))
            )
            final_exception = exc
            if transport_evidence_ceiling(params.config, list(all_lines)) < Provenance.WIRE:
                break
            continue
        except AgentInvocationError as exc:
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
            merged_output = list(raw_lines)
            for line in exc.parsed_output:
                if line not in merged_output:
                    merged_output.append(line)
            if merged_output:
                exc.parsed_output = merged_output
            final_exception = exc
            break

    return list(all_lines), list(live_output_lines), current_session_id, final_exception


def _clear_smoke_artifact(workspace_root: Path) -> None:
    artifact_path = (
        workspace_root / ".agent" / "artifacts" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.md"
    )
    artifact_path.unlink(missing_ok=True)


def _is_smoke_artifact_submitted(workspace_root: Path, run_id: str = _SMOKE_RUN_ID) -> bool:
    """Return whether a smoke test result artifact was submitted via canonical path.

    Direct os.environ.get() is a composition-root read for a test infrastructure
    bridge that constructs per-test environments; not injectable in test context.
    di-seam-allowlist: composition-root test infrastructure.
    """
    return is_artifact_submitted(
        workspace_root,
        run_id,
        SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        receipt_secret=_parent_broker_secret(),
    )


def _explicit_completion_seen(
    workspace_root: Path,
    *,
    run_id: str = _SMOKE_RUN_ID,
) -> bool:
    """Return whether the durable run-scoped completion sentinel is valid."""
    return _check_completion_sentinel(
        workspace_root,
        run_id,
        sentinel_secret=_parent_broker_secret(),
    )


def _parser_event_error(
    config: AgentConfig,
    lines: list[str],
) -> str | None:
    """Return a parser-event error, or None when not applicable / passing."""
    parsed_event_count = _count_parsed_events(config, lines) if lines else 0
    if parsed_event_count == 0:
        return "no parser events were observed"
    return None


def _meaningful_output_error(
    config: AgentConfig,
    live_output_lines: list[str],
    lines: list[str],
) -> str | None:
    """Return a meaningful-output error, or None when not applicable / passing.

    Three-tier check:

      1. Count non-blank rendered lines (``live_output_lines``).
      2. Fall back to parser-classified events (``_meaningful_output_lines``)
         when the rendered count is below the threshold. Some parsers
         (e.g. AgyParser via TextAccumulator) coalesce many short lines
         into a single ``text`` event at paragraph boundaries, so a
         text-rich transcript can still score low at this layer.
      3. Fall back to counting non-blank raw transcript lines
         (``lines``) when both the rendered and parser-classified
         counts are below the threshold. This is the line-by-line
         signal the agent actually emitted; the parser-coalesced
         count is a structural artefact of the text-accumulation
         strategy, not a signal of an under-producing agent.
    """
    meaningful_output = [line for line in live_output_lines if line.strip()]
    if len(meaningful_output) < _MIN_MEANINGFUL_OUTPUT_LINES and lines:
        meaningful_output = _meaningful_output_lines(config=config, lines=lines)
    if len(meaningful_output) < _MIN_MEANINGFUL_OUTPUT_LINES and lines:
        raw_meaningful = [line for line in lines if line.strip()]
        meaningful_output = raw_meaningful[:_MAX_MEANINGFUL_OUTPUT_LINES]
    meaningful_output = meaningful_output[:_MAX_MEANINGFUL_OUTPUT_LINES]
    if len(meaningful_output) < _MIN_MEANINGFUL_OUTPUT_LINES:
        return "fewer than 3 meaningful output lines were observed"
    return None


def _agy_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    """Return the raw ``RALPH_AGY_BINARY`` env value, if set.

    Callers may inject ``env_getter`` for tests and composed runtimes; the
    production default is centralized here so smoke plumbing callers do not
    read ambient environment directly.
    """
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_AGY_BINARY")


def _cursor_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    """Return the raw ``RALPH_CURSOR_BINARY`` env value, if set.

    Callers may inject ``env_getter`` for tests and composed runtimes; the
    production default is centralized here so smoke plumbing callers do not
    read ambient environment directly.  There is no bundled mock for
    cursor (the AGY mock fixture does not apply), so a non-empty
    override points at a real wrapper, alternate live binary, or a
    test-only stub that the operator wires themselves.
    """
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_CURSOR_BINARY")


def _opencode_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    """Return the raw ``RALPH_OPENCODE_BINARY`` env value, if set.

    Backward-compat delegate: the canonical home moved to
    :func:`ralph.config.agent_detection.opencode_binary_override` so
    non-smoke callers (``ralph.agents.invoke``) can read the override
    without importing smoke plumbing. The override is honored by the
    smoke CLI's opencode smoke command and by the per-harness multimodal
    smoke suite when a deterministic stub agent needs to take the
    opencode binary's place.
    """
    from ralph.config.agent_detection import (  # reason: lazy import keeps the backward-compat delegate free of the agent_detection<->smoke_plumbing chain
        opencode_binary_override,
    )

    return opencode_binary_override(env_getter)


def is_mock_agy_override() -> bool:
    """Return True when ``RALPH_AGY_BINARY`` points at the known mock binary.

    The deterministic mock lives at ``tests/_support/mock_agy.sh`` (shell
    wrapper) and ``tests/_support/mock_agy.py`` (Python module). We detect
    the mock by checking the basename of the configured override path: a
    basename that starts with ``mock_agy`` (or equals ``mock_agy``) is
    treated as the mock. A real wrapper, alternate live binary path, or
    ``agy`` on ``PATH`` is treated as the general binary override, not as
    the mock. The detection is purely name-based so a future
    general-purpose wrapper (e.g. ``/opt/agy-wrapper/agy``) is not
    misdiagnosed as a mock run and can still report a real upstream
    diagnostic from ``~/.gemini/antigravity-cli/cli.log``.
    """
    override = _agy_binary_override_env()
    if not override:
        return False
    basename = Path(override).name
    return basename.startswith("mock_agy") or basename == "mock_agy"


def _agy_upstream_diagnostic(lines: list[str], workspace_root: Path) -> str | None:
    """Return an actionable diagnostic when AGY --print produced no usable output.

    AGY's headless --print mode is known to exit 0 with empty stdout when the
    account's API quota is exhausted or the requested model ID is invalid. The
    CLI writes the real reason to ~/.gemini/antigravity-cli/cli.log, so the
    smoke detector surfaces that reason instead of leaving the user with a
    generic "no output" message.

    When the override points at the known mock binary (see
    :func:`is_mock_agy_override`), an empty stdout is expected when
    ``MOCK_AGY_BEHAVIOR`` is ``quota_exhausted`` or ``invalid_model``; in that
    case we surface an informational note instead of the live quota
    diagnostic. A general ``RALPH_AGY_BINARY`` override (a real wrapper, an
    alternate live binary path, or any non-mock executable) does NOT take
    this branch and is diagnosed against the live ``cli.log`` instead, so a
    genuine live-AGY failure is never masked as a mock-empty informational
    note.
    """
    if lines:
        return None
    if read_smoke_test_result_artifact(workspace_root) is not None:
        return None
    if is_mock_agy_override():
        return (
            "mock AGY produced empty stdout by design "
            "(MOCK_AGY_BEHAVIOR=quota_exhausted or invalid_model) "
            "— harness captured this correctly"
        )
    reason = agy_empty_output_reason(lines, cli_log_path=_AGY_CLI_LOG_PATH)
    if reason is not None:
        return reason
    return (
        "AGY --print returned empty stdout; "
        "check ~/.gemini/antigravity-cli/cli.log for model-resolution or quota errors"
    )


def _parser_diagnostics(config: AgentConfig, lines: list[str]) -> list[str]:
    """Return parser and empty-transcript failures from the transport boundary."""
    diagnostics: list[str] = []
    if parser_error := _parser_event_error(config, lines):
        diagnostics.append(parser_error)
    if empty_opencode_error := _opencode_empty_transcript_error(config, lines):
        diagnostics.append(empty_opencode_error)
    return diagnostics


def _opencode_empty_transcript_error(
    config: AgentConfig,
    lines: list[str],
) -> str | None:
    """Explain an OpenCode process failure that produced no parser input."""
    if lines or config.transport != AgentTransport.OPENCODE:
        return None
    return (
        "OpenCode produced no transcript output; verify the configured provider/model with "
        "`opencode models` and inspect its stderr."
    )


def _tool_activity_seen_for_errors(
    params: SmokeRunParams,
    lines: list[str],
    tool_activity_seen: bool | None,
    artifact_submitted: bool,
) -> bool:
    """Resolve whether tool activity was observed from authoritative sources only.

    The earlier AGY-only fallback that read the persisted
    ``smoke_test_result`` artifact's ``headless_guide_checks`` was removed:
    tool activity must be derived from authoritative runtime evidence
    (parser-classified tool events, file-write side effects, or transport
    telemetry), not from the contents of the model-authored artifact. The
    smoke prompt still tells the model to declare ``"tool activity"`` in
    the artifact, but the harness MUST NOT trust the model-authored
    self-report. The companion regression test
    ``tests/test_smoke_plumbing_uses_canonical_submit.py::test_agy_tool_activity_must_not_come_from_artifact``
    pins this invariant: a transcript that emits no parser-classified tool
    events and writes no workspace file but writes a self-reporting
    ``headless_guide_checks=["tool activity"]`` artifact fails the smoke
    run with ``"no tool activity was observed"``.

    Authoritative tool-activity sources, in priority order:

    1. Parser-classified tool events from the transcript (the
       ``[plain] tool: NAME`` convention handled by ``GenericParser``,
       plus structured tool events from JSON-aware parsers like ``AgyParser``).

    S-6 (Evidence Provenance G6 / DoD 20): a NANOCODER-only fallback that
    treated ``artifact_submitted`` as tool-activity evidence used to live
    here. Research confirmed it is the exact self-report-as-evidence shape
    DoD 19 removed for AGY, not a genuine structural constraint --
    ``NanocoderParser`` DOES emit parser-classified ``tool_use`` events
    (the ``⚒ Executed <tool>`` convention and the shared ``[plain] tool:``
    convention both match; see ``ralph/agents/parsers/nanocoder.py``), so
    authoritative tool-activity evidence is available for this transport
    the same way it is for every other one. The branch is deleted;
    nanocoder meets the same bar as every other transport here.
    """
    del artifact_submitted
    if tool_activity_seen is not None:
        return tool_activity_seen
    return bool(_tool_activity_seen(params.config, lines)) if lines else False


def _detect_multimodal_break(
    params: SmokeRunParams,
    run_id: str,
    errors: list[str],
) -> None:
    """Grade the multimodal contract and append a named break on downgrade.

    Extracted so :func:`_detect_smoke_errors` stays below the
    audit's PLR0912 branch-count cap (14); this helper itself
    carries the well-named break string the operator-visible
    table renders when the multimodal fact is not at WIRE.
    """
    fixture_size = (
        params.multimodal_fixture_size if params.multimodal_fixture_size else (0, 0)
    )
    mult_evidence = grade_multimodal_evidence(
        params.workspace_root,
        run_id,
        output_file=params.output_file,
        fixture_relpath=SMOKE_FIXTURE_RELNAME,
        fixture_size=fixture_size,
        secret=_parent_broker_secret(),
    )
    # Persist graded evidence on the params instance via a
    # well-known attribute; ``_run_smoke_agent`` reads it back
    # below when assembling the :class:`SmokeRunResult`.
    object.__setattr__(params, "_multimodal_tool_used_evidence", mult_evidence)
    if mult_evidence.provenance is not Provenance.WIRE:
        mult_break = (
            "multimodal break: agent did not produce a verified "
            "read_media / read_image tools/call carrying the "
            "replay handle "
            f"(graded {mult_evidence.provenance.name.lower()}; "
            f"{mult_evidence.detail})"
        )
        errors.append(mult_break)


def _detect_smoke_errors(  # 15 branches: each contract check is a documented short-circuit
    params: SmokeRunParams,
    lines: list[str],
    live_output_lines: list[str],
    session_id: str | None,
    final_exception: AgentInvocationError | None,
    tool_activity_seen: bool | None = None,
    artifact_submitted: bool = False,
    *,
    run_id: str = _SMOKE_RUN_ID,
    observed_capabilities: frozenset[DisplayCapability] = frozenset(),
    support: AgentSupport | None = None,
) -> list[str]:
    """Detect errors in smoke run results.

    Two new optional keyword-only kwargs close the S-4 capability gap:

    - ``observed_capabilities``: frozenset of capabilities the display's
      :class:`CapabilityObservationRecorder` recorded during the run.
      Defaults to ``frozenset()`` so every existing call site
      (``tests/test_harness_run_diagnosis.py``, ``tests/test_cli_smoke.py``,
      ``tests/integration/test_cli_plumbing_uses_factory.py``) keeps
      passing.
    - ``support``: the registered :class:`AgentSupport` resolved for
      ``params.agent_name`` via ``AgentRegistry.from_config(...).catalog.get(...)``.
      Defaults to ``None`` so a bare ``_detect_smoke_errors`` call is
      hermetic (Branch A returns ``[]``).

    The capability branch only fires when BOTH kwargs are supplied
    explicitly. With the defaults, the original error-taxonomy is
    unchanged for callers that do not thread the recorder through.
    """
    errors = _detect_break_indicators(lines)
    errors.extend(_raw_transcript_corruption_errors(params.workspace_root, params.config))
    if support is not None and params.agent_name:
        errors.extend(
            _detect_capability_breaks(
                support, observed_capabilities, params.config, lines
            )
        )
    if final_exception is not None:
        errors.append(str(final_exception))
    if prompt_submission_error := _nanocoder_prompt_submission_error(
        params,
        lines,
        artifact_submitted,
    ):
        errors.append(prompt_submission_error)
    if not params.output_file.exists():
        errors.append("expected todo-list.js was not created")
    # S-6 (Evidence Provenance G6 / DoD 20): the exemption is expressed as
    # a general, transport-declared property (session_identifier_observable,
    # defaulting to True) rather than a literal transport comparison, so
    # every registered transport is held to the same bar unless it
    # explicitly documents why it cannot clear it. See
    # ``AgentSupport.session_identifier_observable``'s docstring for why
    # nanocoder is currently the only documented ``False`` case.
    session_identifier_observable = support is None or support.session_identifier_observable
    if session_id is None and session_identifier_observable:
        errors.append("session ID was not observed")

    if not _explicit_completion_seen(params.workspace_root, run_id=run_id):
        errors.append("completion sentinel was not observed")

    errors.extend(_parser_diagnostics(params.config, lines))

    if not _tool_activity_seen_for_errors(params, lines, tool_activity_seen, artifact_submitted):
        errors.append("no tool activity was observed")

    if not artifact_submitted:
        errors.append("smoke_test_result artifact was not submitted")

    if output_error := _meaningful_output_error(params.config, live_output_lines, lines):
        errors.append(output_error)

    if params.subagents_requested:
        subagent_evidence = _subagent_smoke_evidence(params.config, lines)
        if subagent_error := _subagent_smoke_error(subagent_evidence):
            errors.append(subagent_error)

    if params.multimodal_requested:
        _detect_multimodal_break(params, run_id, errors)

    if params.config.transport == AgentTransport.AGY:
        diagnostic = _agy_upstream_diagnostic(lines, params.workspace_root)
        if diagnostic is not None:
            errors.append(diagnostic)

    visible_output_count = len([line for line in live_output_lines if line.strip()])
    overrun_error = _visible_output_overrun_error(params.config, visible_output_count)
    if overrun_error is not None:
        errors.append(overrun_error)
    return errors


def _raw_transcript_corruption_errors(workspace_root: Path, config: AgentConfig) -> list[str]:
    """S-4 (G4 / DoD 15): surface a corrupted/truncated raw transcript as a
    reported break at the smoke seam, mirroring the same real writer's path
    formula (\"unit_id\" via ``shlex.split(config.cmd)[0]``, matching the
    private ``_agent_command_name`` helper in ``_process_reader.py``, and
    ``model`` read directly from ``config.model``) so this reads the exact
    file the real ``RawOverflowLog`` writer used, not a guessed path.

    Never raises: an unreadable/malformed ``config.cmd`` degrades to an
    empty break list rather than crashing the whole smoke error sweep.
    """
    try:
        unit_id = shlex.split(config.cmd)[0]
    except (ValueError, IndexError):
        return []
    raw_path = raw_log_path_for(workspace_root, unit_id, model=config.model)
    return [
        f"raw transcript corrupted: {br.detail}" for br in detect_raw_log_breaks(raw_path)
    ]


def _visible_output_overrun_error(
    config: AgentConfig, visible_output_count: int
) -> str | None:
    """Return the visible-output overrun error string, or ``None`` when within the ceiling.

    The cmd carries transport-specific flags (``claude -p``,
    ``agy --print``, etc.) and a per-agent suffix; the per-transport
    ceiling is keyed on the bare command name and short option that names
    the transport, so a ``claude -p`` invocation falls under the same
    ``claude-headless`` bucket as a plain ``claude-headless`` binary.
    """
    agent_prefix = _resolve_visible_output_agent_prefix(config)
    visible_output_ceiling = _MAX_VISIBLE_OUTPUT_LINES_BY_AGENT.get(
        agent_prefix, _MAX_VISIBLE_OUTPUT_LINES
    )
    if visible_output_count <= visible_output_ceiling:
        return None
    return (
        "interactive output overran into too many visible lines; "
        f"semantic output parity is still insufficient ("
        f"{visible_output_count} > {visible_output_ceiling})"
    )


def _resolve_visible_output_agent_prefix(config: AgentConfig) -> str:
    """Return the agent-prefix key used to look up the per-transport visible-output ceiling.

    The cmd carries transport-specific flags (``claude -p``,
    ``agy --print``, etc.) and a per-agent suffix; the per-transport
    ceiling is keyed on the bare command name and short option that names
    the transport, so a ``claude -p`` invocation falls under the same
    ``claude-headless`` bucket as a plain ``claude-headless`` binary.
    """
    cmd_tokens = (config.cmd or "").split() if config.cmd else []
    cmd_name = cmd_tokens[0].lower() if cmd_tokens else ""
    cmd_flags = set(cmd_tokens[1:])
    is_headless_claude = cmd_name == "claude" and "-p" in cmd_flags
    return "claude-headless" if is_headless_claude else cmd_name


def _artifact_submission_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    submitted: bool,
    secret: str | None,
) -> Evidence:
    """Grade the artifact-submission fact (A3): fallback promotion vs. a wire hit.

    Thin call-through to the shared
    ``smoke_evidence.grade_artifact_submission_evidence`` (S-2) so the smoke
    gate and ``ralph.agents.completion_signals.evaluate_completion`` grade a
    submitted artifact's provenance identically — no behavior change here.
    """
    return grade_artifact_submission_evidence(
        workspace_root,
        run_id,
        submitted=submitted,
        secret=secret,
    )


def _completion_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    present: bool,
    host_synthesized: bool,
    secret: str | None,
) -> Evidence:
    """Grade the completion-sentinel fact (A4/A5).

    Thin call-through to the shared
    ``smoke_evidence.grade_completion_sentinel_evidence`` (S-2) so the smoke
    gate and ``ralph.agents.completion_signals.evaluate_completion`` grade a
    completion sentinel's provenance identically — no behavior change here.
    """
    return grade_completion_sentinel_evidence(
        workspace_root,
        run_id,
        present=present,
        host_synthesized=host_synthesized,
        secret=secret,
    )


def _tool_activity_evidence(
    params: SmokeRunParams,
    lines: list[str],
    *,
    run_id: str,
    secret: str | None,
    tool_activity_holds: bool,
) -> Evidence:
    """Grade the tool-activity fact from the strongest authoritative source available."""
    if wire_evidence_for(params.workspace_root, run_id, secret=secret):
        return Evidence(
            holds=True,
            provenance=Provenance.WIRE,
            detail="a tools/call record was observed on the wire ledger",
        )
    if not tool_activity_holds:
        return absent("no tool activity was observed")
    if lines and _tool_activity_seen(params.config, lines):
        return Evidence(
            holds=True,
            provenance=Provenance.TRANSCRIPT,
            detail="the parser classified a tool_use event in the transcript",
        )
    return Evidence(
        holds=True,
        provenance=Provenance.WORKSPACE_EFFECT,
        detail="inferred from a workspace side effect (expected output file present)",
    )


_RALPH_DISPATCHER_TOOL_NAMES = frozenset({"call_mcp_tool"})


def _tool_name_reaches_ralph(name: str) -> bool:
    """Return True when an advertised tool name is (or can dial) a Ralph MCP tool."""
    lowered = name.lower()
    return (
        lowered.startswith("ralph_")
        or lowered.startswith("mcp__ralph__")
        or lowered in _RALPH_DISPATCHER_TOOL_NAMES
    )


def _advertised_tool_names_from_init_frame(obj: dict[str, object]) -> list[str] | None:
    """Return the tool names an ``init``-shaped frame advertises, or None if not one."""
    if obj.get("event") != "init":
        return None
    init_info = obj.get("init")
    if not isinstance(init_info, dict):
        return None
    raw_tools = cast("dict[str, object]", init_info).get("tools")
    if not isinstance(raw_tools, list):
        return None
    names: list[str] = []
    for entry in cast("list[object]", raw_tools):
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            name = cast("dict[str, object]", entry).get("name")
            if isinstance(name, str):
                names.append(name)
    return names


def _opencode_tool_use_reaches_ralph(first_lines: list[str]) -> bool | None:
    """Return whether an OpenCode-shaped ``tool_use`` frame names a Ralph tool.

    OpenCode 1.18.14's measured wire format (see
    ``tests/display/_fixtures/opencode_wire_provenance.md``) never emits an
    ``init``-shaped frame, so the scan in :func:`transport_evidence_ceiling`
    above always misses for OpenCode and the ceiling would otherwise be
    perpetually ``Provenance.ABSENT`` regardless of whether the transport
    actually reached Ralph's MCP tools. OpenCode's own MCP client dials
    Ralph's tools with the raw wire name ``ralph_<tool>``
    (``ralph/mcp/transport/opencode.py`` grants the ``ralph_*`` permission
    wildcard for exactly this reason); the display-layer normalization that
    strips the prefix (``OpenCodeParser._canonical_tool_name``) runs after
    this check, so this reads ``part.tool`` before it is stripped.

    Returns ``True`` when a ``tool_use``-shaped frame names a Ralph-routed
    tool, ``False`` when a ``tool_use``-shaped frame is found but none
    reaches Ralph (a real tool-activity signal that proves nothing about
    reaching Ralph), or ``None`` when no ``tool_use``-shaped frame is
    present at all (no signal either way).
    """
    found_any = False
    for line in first_lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        obj = cast("dict[str, object]", parsed)
        if obj.get("type") != "tool_use":
            continue
        part = obj.get("part")
        if not isinstance(part, dict):
            continue
        raw_tool = cast("dict[str, object]", part).get("tool")
        if not isinstance(raw_tool, str) or not raw_tool:
            continue
        found_any = True
        if _tool_name_reaches_ralph(raw_tool):
            return True
    return False if found_any else None


def transport_evidence_ceiling(config: AgentConfig, first_lines: list[str]) -> Provenance:
    """Return the maximum Provenance this transport's tools could reach (F3).

    Inspects an ``init``-shaped frame's advertised tool listing (AGY's
    ``init.tools``) for a direct ``ralph_*`` / ``mcp__ralph__*`` tool name or
    a known generic MCP dispatcher (``call_mcp_tool``). A transport that
    advertises no route to Ralph's tools cannot possibly reach
    ``Provenance.WIRE`` no matter what the transcript later claims, because
    the model has no way to produce a genuine ``tools/call`` frame — this is
    the ceiling reported before further turns are spent.

    OpenCode never emits an ``init``-shaped frame (measured; see
    ``_opencode_tool_use_reaches_ralph``), so when the ``init``-frame scan
    finds no signal at all, this falls back to inspecting an
    OpenCode-shaped ``tool_use`` frame's raw (pre-normalization) tool name
    for the same ``ralph_*`` / ``mcp__ralph__*`` route. A transport whose
    transcript carries neither shape stays ``Provenance.ABSENT``.

    Returns ``Provenance.ABSENT`` when no ``init``-shaped frame and no
    ``tool_use``-shaped frame is found in ``first_lines`` (no signal either
    way), ``Provenance.TRANSCRIPT`` when a frame is found but advertises no
    route to Ralph (the measured AGY v1.1.10 shape: 56 tools, 0
    ``ralph_*``; or a native OpenCode tool call like ``read`` / ``bash``),
    or ``Provenance.WIRE`` when a route is advertised. ``config`` is
    accepted for a future transport-specific parsing strategy; the current
    implementation is transport-agnostic NDJSON scanning.
    """
    del config
    for line in first_lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        names = _advertised_tool_names_from_init_frame(cast("dict[str, object]", parsed))
        if names is None:
            continue
        if any(_tool_name_reaches_ralph(name) for name in names):
            return Provenance.WIRE
        return Provenance.TRANSCRIPT
    opencode_signal = _opencode_tool_use_reaches_ralph(first_lines)
    if opencode_signal is True:
        return Provenance.WIRE
    if opencode_signal is False:
        return Provenance.TRANSCRIPT
    return Provenance.ABSENT


def _report_evidence_ceiling_once(config: AgentConfig, lines: list[str]) -> bool:
    """S-2 (Evidence Provenance F3): log the transport's evidence ceiling as
    soon as an ``init``-shaped frame is observed, before further turns are
    spent — not only inside the ``OpenCodeResumableExitError`` multi-turn
    retry branch. Returns ``True`` once an ``init`` frame has been found and
    the ceiling logged, so :func:`_execute_smoke_turns` logs it exactly once
    per run; returns ``False`` when no ``init``-shaped frame is in ``lines``
    yet, so the caller re-checks on the next turn's accumulated lines. This
    is a diagnostic-only signal: it never changes control flow by itself
    (the existing early-break in the retry branch is unchanged).
    """
    ceiling = transport_evidence_ceiling(config, lines)
    if ceiling is Provenance.ABSENT:
        return False
    logger.info(
        "smoke: transport evidence ceiling is {} — init frame's advertised "
        "tools cap the best possible verdict at this grade for this run",
        ceiling.name,
    )
    return True


def _run_smoke_agent(
    params: SmokeRunParams,
    run_id: str = _SMOKE_RUN_ID,
) -> SmokeRunResult:
    """Run the smoke agent and return results.

    S-5: snapshots the display's :class:`CapabilityObservationRecorder`
    between ``_execute_smoke_turns`` returning and ``_detect_smoke_errors``
    being called -- the only ordering that lets the capability-breaks
    detector (S-4) see real observations AND keeps the original ordering
    of the smoke harness's grading pipeline intact. Resolves the
    :class:`AgentSupport` via ``params.support_resolver`` or, by default,
    via ``AgentRegistry.from_config(params.unified_config).catalog.get(name)``
    so the synthesized dynamic-alias support (e.g. for
    ``opencode/minimax/MiniMax-M3``) is what the gate grades, not a stale
    ``builtin_supports()`` lookup that misses the alias.
    """
    all_lines, live_output_lines, current_session_id, final_exception = _execute_smoke_turns(
        params, None, run_id=run_id
    )

    lines = all_lines
    session_id = current_session_id or extract_transport_session_id(tuple(lines))
    secret = _parent_broker_secret()
    artifact_submitted = _is_smoke_artifact_submitted(params.workspace_root, run_id)
    # Authoritative completion is the durable sentinel for every transport.
    # AGY no longer gets a host-synthesized fallback: an agent that never
    # called ``declare_complete`` did not complete, and the gate must say so
    # by failing. See .agent/PRODUCT_CRITERIA.md F7 / DoD 17 / DoD 19.
    explicit_completion_present = _explicit_completion_seen(
        params.workspace_root,
        run_id=run_id,
    )
    completion_evidence = _completion_evidence(
        params.workspace_root,
        run_id,
        present=explicit_completion_present,
        host_synthesized=False,
        secret=secret,
    )
    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    # Tool activity MUST come from authoritative parser / transport events
    # or workspace file-write side effects — never from the agent-authored
    # ``headless_guide_checks`` artifact. See
    # ``_tool_activity_seen_for_errors`` docstring and the regression test
    # ``test_agy_tool_activity_must_not_come_from_artifact``.
    tool_activity_holds = _tool_activity_seen_for_errors(
        params,
        lines,
        tool_activity_seen=None,
        artifact_submitted=artifact_submitted,
    )
    tool_activity_evidence = _tool_activity_evidence(
        params,
        lines,
        run_id=run_id,
        secret=secret,
        tool_activity_holds=tool_activity_holds,
    )
    artifact_evidence = _artifact_submission_evidence(
        params.workspace_root,
        run_id,
        submitted=artifact_submitted,
        secret=secret,
    )
    transport_ceiling = transport_evidence_ceiling(params.config, lines)
    parsed_output_lines = _meaningful_output_lines(params.config, lines) if lines else []
    live_filtered = [line for line in live_output_lines if line.strip()][
        :_MAX_MEANINGFUL_OUTPUT_LINES
    ]
    # Prefer the parser-classified events (with the ``text:`` / ``thinking:`` /
    # ``tool_use:`` type prefix) when the parser produced any events. The
    # parser-classified lines are the canonical ``what did the agent actually
    # emit`` signal and are what the smoke report's "Observed output:" section
    # labels as ``- text: ...`` for the operator. The raw ``live_output_lines``
    # fallback is used when the parser produced no text-classified events
    # (e.g. plain ``GenericParser`` output for a non-AGY agent that does not
    # tag its own lines).
    meaningful_output_lines = parsed_output_lines or live_filtered
    subagent_evidence = _subagent_smoke_evidence(params.config, lines)

    # S-5: snapshot the recorder's observed-capabilities set BEFORE the
    # error-taxonomy runs. A ``ParallelDisplay`` instance is per-run,
    # so the snapshot lives only as long as the dataclass field below.
    if params.display is not None:
        observed_capabilities = params.display.capability_recorder.observed_capabilities()
    else:
        observed_capabilities = frozenset()
    # S-6 (G6 / DoD 20): resolved unconditionally (not gated on
    # ``params.display is not None`` the way ``observed_capabilities`` above
    # is) because ``_detect_smoke_errors`` also reads
    # ``support.session_identifier_observable`` for the session-id check
    # below, independent of whether a display is wired.
    if params.support_resolver is not None:
        resolved_support = params.support_resolver(params.agent_name)
    else:
        # The catalog's dynamic-alias resolver is the authoritative source
        # for the capability contract. Do not suppress a missing catalog:
        # doing so turns a resolver wiring error into an ungraded smoke run.
        from ralph.agents.registry import AgentRegistry as FreshAgentRegistry

        resolved_support = FreshAgentRegistry.from_config(
            params.unified_config,
            catalog=AgentCatalog(),
        ).catalog.get(params.agent_name)

    errors = _detect_smoke_errors(
        params,
        lines,
        live_output_lines,
        session_id,
        final_exception,
        tool_activity_seen=tool_activity_holds,
        artifact_submitted=artifact_submitted,
        run_id=run_id,
        observed_capabilities=observed_capabilities,
        support=resolved_support,
    )

    config = params.config
    transport_name = config.transport.value if config.transport is not None else "generic"
    multimodal_tool_used: Evidence | None = getattr(
        params, "_multimodal_tool_used_evidence", None
    )
    if multimodal_tool_used is None and not params.multimodal_requested:
        # Default to absent(...) so the dataclass always carries a
        # non-None value -- the operator-visible report renders it
        # uniformly across multimodal and non-multimodal runs.
        multimodal_tool_used = absent("multimodal scenario not requested for this run")
    return SmokeRunResult(
        agent_name=params.agent_name,
        transport=transport_name,
        output_file=params.output_file,
        file_created=params.output_file.exists(),
        session_id=session_id,
        explicit_completion_seen=completion_evidence,
        raw_line_count=len([line for line in lines if line.strip()]),
        parsed_event_count=parsed_event_count,
        tool_activity_seen=tool_activity_evidence,
        artifact_submitted=artifact_evidence,
        meaningful_output_lines=meaningful_output_lines,
        errors=errors,
        subagents_requested=params.subagents_requested,
        subagent_dispatch_count=subagent_evidence.dispatch_count,
        subagent_dispatch_seen=subagent_evidence.dispatch_seen,
        subagent_result_seen=subagent_evidence.result_seen,
        post_subagent_activity_seen=subagent_evidence.post_result_activity_seen,
        transport_evidence_ceiling=transport_ceiling,
        observed_capabilities=observed_capabilities,
        multimodal_requested=params.multimodal_requested,
        multimodal_fixture_size=params.multimodal_fixture_size,
        multimodal_tool_used=multimodal_tool_used,
    )


def run_smoke_plumbing(
    *,
    config: UnifiedConfig,
    workspace_root: Path,
    agent_name: str,
    prompt_file: Path,
    output_file: Path | None = None,
    display_context: DisplayContext | None = None,
    pipeline_core: PipelineCore | None = None,
    bridge_factory: BridgeFactory | None = None,
    pipeline_deps: PipelineDeps | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    subagents: bool = False,
    multimodal: bool = False,
) -> SmokeRunResult:
    """Run the interactive smoke test for ``agent_name`` and return the result.

    Callers may supply either the modular ``pipeline_core`` + ``bridge_factory``
    surface or the legacy extended ``pipeline_deps`` bundle. When
    ``pipeline_deps`` is provided it is used for backward compatibility and
    its ``core`` and ``bridge_factory`` are derived automatically. When both
    are omitted, production defaults are built through
    :class:`DefaultPipelineFactory` so the plumbing-direct-call path shares
    the same composition root as the main pipeline; ``pro_hooks`` is forwarded
    so a Pro subclassed factory is honored.

    When ``multimodal`` is True the run writes the deterministic PNG
    fixture (``SMOKE_FIXTURE_RELNAME``) and the ``[media] max_inline_bytes``
    fragment of the harness's ``.agent/mcp.toml`` before the turns start,
    so every harness identity takes the handle-mint path the multimodal
    grader grades. ``multimodal`` defaults to False so every existing
    smoke run is unchanged.
    """
    multimodal_fixture_size: tuple[int, int] | None = None
    if multimodal:
        multimodal_fixture_size = generate_fixture_geometry()
        fixture_path = workspace_root / SMOKE_FIXTURE_RELNAME
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        # filesystem-write-ok: smoke harness materializes the deterministic PNG fixture on every run; the file is regenerated each run with per-run geometry so the SHA is never reused and macOS fseventsd amplification cannot bind to a stable inode.
        fixture_path.write_bytes(
            build_smoke_fixture_png(
                *multimodal_fixture_size,
                perception_secret=secrets.token_hex(16),
            )
        )
        mcp_toml_path = workspace_root / ".agent" / "mcp.toml"
        mcp_toml_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_if_changed(
            DEFAULT_FILE_BACKEND,
            mcp_toml_path,
            smoke_media_config_toml(),
            encoding="utf-8",
            prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(mcp_toml_path.parent, parents=True, exist_ok=True),
        )
    spec = resolve_smoke_harness_spec(agent_name)
    if pipeline_deps is not None:
        if display_context is None:
            display_context = pipeline_deps.display_context
        effective_pipeline_deps = pipeline_deps
        effective_core = pipeline_deps.core
        effective_bridge_factory = pipeline_deps.bridge_factory
    elif pipeline_core is not None:
        if display_context is None:
            display_context = pipeline_core.display_context
        effective_bridge_factory = bridge_factory or build_session_bridge
        effective_pipeline_deps = PipelineDeps(
            core=pipeline_core,
            bridge_factory=effective_bridge_factory,
        )
        effective_core = pipeline_core
    else:
        if display_context is None:
            raise ValueError(
                "display_context is required when pipeline_deps and pipeline_core are not provided"
            )
        effective_pipeline_deps = DefaultPipelineFactory().build(
            config, display_context, pro_hooks=pro_hooks
        )
        display_context = effective_pipeline_deps.display_context
        effective_core = effective_pipeline_deps.core
        effective_bridge_factory = effective_pipeline_deps.bridge_factory

    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise RuntimeError(f"Smoke test agent '{agent_name}' is unavailable in the registry")
    agy_override = _agy_binary_override_env()
    if agy_override:
        if is_mock_agy_override():
            logger.info("mock AGY binary in use: {}", agy_override)
        else:
            logger.info("Using RALPH_AGY_BINARY override: {}", agy_override)
    effective_output_file = output_file if output_file is not None else spec.output_file

    agents_policy = None
    if pipeline_deps is not None and pipeline_deps.policy_bundle is not None:
        agents_policy = pipeline_deps.policy_bundle.agents
    if agents_policy is None:
        workspace_scope = resolve_workspace_scope(workspace_root)
        agents_policy = load_agents_policy_for_workspace_scope(workspace_scope, config=config)

    with with_bridge_lifetime(
        effective_core,
        effective_bridge_factory,
        repo_root=workspace_root,
        drain="development",
        session_id_prefix="smoke",
        agents_policy=agents_policy,
        run_id=spec.run_id,
    ) as bridge:
        if effective_output_file.exists():
            effective_output_file.unlink()
        _clear_smoke_artifact(workspace_root)
        _clear_session_completion_sentinel(workspace_root, spec.run_id)

        # Honor per-agent session ceilings so AGY's longer --print-timeout is not
        # cut off by the legacy 120s default. See _AGENT_SESSION_CEILINGS.
        agent_prefix = agent_name.split("/", maxsplit=1)[0]
        session_ceiling = _AGENT_SESSION_CEILINGS.get(agent_prefix, _SMOKE_MAX_SESSION_SECONDS)
        smoke_general = config.general.model_copy(
            update={
                "agent_idle_timeout_seconds": _SMOKE_IDLE_TIMEOUT_SECONDS,
                "agent_max_session_seconds": session_ceiling,
            }
        )
        smoke_config = config.model_copy(update={"general": smoke_general})

        # S-5: build a fresh ``ParallelDisplay`` per smoke run, owned by
        # the plumbing so its capability recorder is the live one the
        # display path writes to. Tests inject their own ``display`` via
        # ``SmokeRunParams``; production never needs to.
        fresh_display = ParallelDisplay(display_context=display_context)

        results = [
            _run_smoke_agent(
                SmokeRunParams(
                    agent_name=agent_name,
                    config=agent_config,
                    unified_config=smoke_config,
                    workspace_root=workspace_root,
                    prompt_file=prompt_file,
                    output_file=effective_output_file,
                    options=InvokeOptions(),
                    display_context=display_context,
                    bridge=bridge,
                    pipeline_deps=effective_pipeline_deps,
                    subagents_requested=subagents,
                    display=fresh_display,
                    multimodal_requested=multimodal,
                    multimodal_fixture_size=multimodal_fixture_size,
                ),
                run_id=spec.run_id,
            )
        ]

    return results[0]


__all__ = [
    "SmokeHarnessSpec",
    "SmokeRunResult",
    "_build_smoke_prompt",
    "_execute_smoke_turns",
    "_run_smoke_agent",
    "resolve_smoke_harness_spec",
    "run_smoke_plumbing",
]


# Import-time invariant guards. These are RuntimeError (not assert) so they
# survive ``python -O`` and keep the smoke harness within documented bounds.
if _SMOKE_MAX_TURNS < 1:
    raise RuntimeError("_SMOKE_MAX_TURNS must be >= 1")
if _SMOKE_IDLE_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0")
if _AGENT_SESSION_CEILINGS["agy"] <= _SMOKE_IDLE_TIMEOUT_SECONDS:
    raise RuntimeError("_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS")
