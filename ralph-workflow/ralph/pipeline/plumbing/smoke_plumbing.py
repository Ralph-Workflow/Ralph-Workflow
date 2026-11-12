from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import shlex
import sqlite3
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.agents._agy_upstream_diagnostic import agy_empty_output_reason
from ralph.agents._broker_secret import _parent_broker_secret
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
from ralph.agents.parsers import get_parser, resolve_parser_key
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.display.capability_observation_recorder import infer_surface_for_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.preview_payload import payload_from_tool_event
from ralph.display.raw_overflow import (
    detect_raw_log_breaks,
    raw_log_path_for,
    raw_log_unit_id_for,
)
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.artifacts.completion_receipts import clear_run_receipts
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.mcp.artifacts.state_db import RunStateDB
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
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
    from ralph.agents.support import AgentSupport
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.pro_support.hooks import ProPipelineHooks

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_LEGACY_INTERACTIVE_CLAUDE_MODEL = "haiku"
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
_KIMI_SMOKE_RELATIVE_DIR = Path("tmp/interactive-kimi-smoke")
_KIMI_SMOKE_OUTPUT_FILE = _KIMI_SMOKE_RELATIVE_DIR / "todo-list.js"
_KIMI_SMOKE_RUN_ID = "interactive-kimi-smoke"
_CCS_SMOKE_RELATIVE_DIR = Path("tmp/interactive-ccs-smoke")
_CCS_SMOKE_RUN_ID = "interactive-ccs-smoke"


@dataclass(frozen=True)
class SmokeHarnessSpec:
    agent_name: str
    relative_dir: Path
    output_file: Path
    run_id: str


def _resolve_nanocoder_spec(agent_name: str) -> SmokeHarnessSpec | None:
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
    suffix = agent_name.removeprefix("claude-headless").lstrip("/")
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


def _resolve_interactive_claude_spec(agent_name: str) -> SmokeHarnessSpec:
    suffix = agent_name.removeprefix("claude").lstrip("/")
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
    run_id = (
        _SMOKE_RUN_ID
        if sanitized in {"", _LEGACY_INTERACTIVE_CLAUDE_MODEL}
        else f"{_SMOKE_RUN_ID}-{sanitized}"
    )
    return SmokeHarnessSpec(
        agent_name=agent_name,
        relative_dir=_SMOKE_RELATIVE_DIR,
        output_file=_SMOKE_OUTPUT_FILE,
        run_id=run_id,
    )


def resolve_smoke_harness_spec(agent_name: str) -> SmokeHarnessSpec:
    if agent_name == "claude-headless" or agent_name.startswith("claude-headless/"):
        return _resolve_headless_claude_spec(agent_name)
    if agent_name == "claude" or agent_name.startswith("claude/"):
        return _resolve_interactive_claude_spec(agent_name)
    if agent_name == "agy" or agent_name.startswith("agy/"):
        model = agent_name.removeprefix("agy").lstrip("/")
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model).strip("-")
        run_id = "interactive-agy-smoke" if not sanitized else f"interactive-agy-smoke-{sanitized}"
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
        suffix = agent_name.removeprefix("opencode").lstrip("/")
        if not suffix:
            run_id = _OPENCODE_SMOKE_RUN_ID
            relative_dir = _OPENCODE_SMOKE_RELATIVE_DIR
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_OPENCODE_SMOKE_RUN_ID}-{sanitized}"
            relative_dir = _OPENCODE_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=run_id,
        )
    if agent_name == "codex" or agent_name.startswith("codex/"):
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
    if agent_name == "kimi" or agent_name.startswith("kimi/"):
        suffix = agent_name.removeprefix("kimi").lstrip("/")
        if not suffix:
            run_id = _KIMI_SMOKE_RUN_ID
            relative_dir = _KIMI_SMOKE_RELATIVE_DIR
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_KIMI_SMOKE_RUN_ID}-{sanitized}"
            relative_dir = _KIMI_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=run_id,
        )
    if agent_name.startswith("ccs/"):
        suffix = agent_name.removeprefix("ccs/")
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
        if not sanitized:
            raise ValueError(f"No smoke harness spec defined for agent '{agent_name}'")
        relative_dir = _CCS_SMOKE_RELATIVE_DIR / sanitized
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=relative_dir,
            output_file=relative_dir / "todo-list.js",
            run_id=f"{_CCS_SMOKE_RUN_ID}-{sanitized}",
        )
    raise ValueError(f"No smoke harness spec defined for agent '{agent_name}'")


_SMOKE_IDLE_TIMEOUT_SECONDS = 30.0
_SMOKE_MAX_SESSION_SECONDS = 120.0
_AGENT_SESSION_CEILINGS = {
    "claude": 120.0,
    "agy": 360.0,
    "opencode": 360.0,
    "kimi": 360.0,
}
_SMOKE_MAX_TURNS = 5
_SMOKE_TRANSCRIPT_MAX_LINES = 400
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
_MAX_VISIBLE_OUTPUT_LINES_BY_AGENT: dict[str, int] = {
    "claude-headless": 250,
}
_MAX_VISIBLE_OUTPUT_LINES = 80
_STREAM_JSON_OUTPUT_FLAG: Final = "--output-format=stream-json"
_SUBAGENT_TOOL_NAMES = frozenset({"agent", "delegate", "spawn_agent", "subagent", "task"})
_DEFAULT_SUBAGENT_PROMPT = (
    "Inspect the requested todo-list API and return two concise edge cases "
    "the main agent should account for. Do not modify files."
)

_CRASH_PATTERNS = (
    re.compile(r"^Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"^thread .* panicked at", re.IGNORECASE),
    re.compile(
        r"segmentation fault \(core dumped\)|SIGSEGV|Aborted \(core dumped\)",
        re.IGNORECASE,
    ),
    re.compile(r"^fatal:\s", re.IGNORECASE),
)

_AGY_CLI_LOG_PATH: Path = Path.home() / ".gemini" / "antigravity-cli" / "cli.log"


@dataclass(frozen=True)
class SmokeRunResult:
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
    transport_evidence_ceiling: Provenance = Provenance.ABSENT
    observed_capabilities: frozenset[DisplayCapability] = frozenset()
    multimodal_requested: bool = False
    multimodal_fixture_size: tuple[int, int] | None = None
    multimodal_tool_used: Evidence | None = None


@dataclass(frozen=True)
class SubagentSmokeEvidence:
    dispatch_count: int = 0
    dispatch_seen: bool = False
    result_seen: bool = False
    post_result_activity_seen: bool = False


type EnvGetter = Callable[[str], str | None]


CONFORMANCE_MATRIX_TRANSPORT_ORDER: Final[tuple[str, ...]] = (
    "claude",
    "codex",
    "agy",
    "pi",
    "nanocoder",
    "cursor",
    "opencode",
    "kimi",
)

CONFORMANCE_MATRIX_FACTS: Final[tuple[str, ...]] = (
    "artifact_submitted",
    "explicit_completion_seen",
    "tool_activity_seen",
)

CANONICAL_CAPABILITY_AGENT_ORDER: Final[tuple[str, ...]] = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "agy",
    "pi",
    "cursor",
    "kimi",
)

_CONFORMANCE_MATRIX_JSON_RELPATH: Final[str] = ".agent/artifacts/smoke_conformance_matrix.json"
_CONFORMANCE_MATRIX_MD_RELPATH: Final[str] = ".agent/artifacts/smoke_conformance_matrix.md"


def conformance_matrix_paths(workspace_root: Path) -> tuple[Path, Path]:
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
    updated = {key: dict(row) for key, row in matrix.items()}
    updated[transport] = dict(evidence)
    return updated


def _ordered_transports(matrix: ConformanceMatrix) -> list[str]:
    canonical = [t for t in CONFORMANCE_MATRIX_TRANSPORT_ORDER if t in matrix]
    extra = sorted(t for t in matrix if t not in CONFORMANCE_MATRIX_TRANSPORT_ORDER)
    return canonical + extra


def render_conformance_matrix_markdown(matrix: ConformanceMatrix) -> str:
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
    from ralph.agents.builtin import (
        builtin_supports,
    )
    from ralph.agents.display_capabilities import (
        all_display_capabilities,
    )

    capabilities = tuple(all_display_capabilities())
    rows = (
        capability_rows
        if capability_rows is not None
        else {support.name: support.display_capabilities for support in builtin_supports()}
    )
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
    opencode_tool_naming_requirement = (
        "- This transport exposes Ralph's tools with an explicit `ralph_` "
        "prefix (for example `ralph_read_file`, `ralph_write_file`, "
        "`ralph_edit_file`, `ralph_create_directory`). There is no bare "
        "`read_file` / `write_file` / `edit_file` tool -- always call the "
        "`ralph_`-prefixed name exactly as listed in your available tools.\n"
        if transport is AgentTransport.OPENCODE
        else ""
    )
    is_agy = transport is AgentTransport.AGY
    from ralph.prompts._mcp_dispatcher_hint import agy_dispatcher_hint_text

    submit_call_instruction = (
        (
            agy_dispatcher_hint_text(submit_artifact_tool_name)
            + f'\n- Pass `artifact_type="{SMOKE_TEST_RESULT_ARTIFACT_TYPE}"` and '
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
    completion_requirement = (
        "- After the artifact tool returns a valid receipt, call `declare_complete` "
        "as the mandatory final action. The receipt is not phase completion; do not "
        "stop until the completion call succeeds.\n"
    )

    agy_write_requirement = (
        "- Create that file only through `call_mcp_tool` targeting Ralph's "
        "`write_file`; do not use AGY's native `write_to_file` tool, which "
        "accepts only its private artifact directory.\n"
        if is_agy
        else ""
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
        "- For any Ralph workspace write tool, pass the requested workspace-relative path "
        "exactly (for example `tmp/interactive-agy-smoke/todo-list.js`), never an "
        "absolute path.\n"
        f"{agy_write_requirement}"
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
            correlate_by_name = tool_name in _SUBAGENT_TOOL_NAMES
            correlate_by_id = result_id is not None and result_id in dispatch_ids
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
        cast("AgentTransport", config.transport),
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
                f"declared capability {target.name} never rendered despite {tools} tool call"
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


def _update_smoke_turn_state(
    raw_lines: deque[str],
    rendered_lines: deque[str],
    all_lines: deque[str],
    live_output_lines: deque[str],
    config: AgentConfig,
    highest_latched_ceiling: Provenance,
) -> Provenance:
    all_lines.extend(raw_lines)
    live_output_lines.extend(rendered_lines)
    cur_c = transport_evidence_ceiling(config, list(raw_lines))
    return max(highest_latched_ceiling, cur_c)


def _execute_smoke_turns(
    params: SmokeRunParams,
    current_session_id: str | None,
    run_id: str = _SMOKE_RUN_ID,
) -> tuple[list[str], list[str], str | None, AgentInvocationError | None, Provenance]:
    all_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    live_output_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
    final_exception: AgentInvocationError | None = None
    workspace_scope = resolve_workspace_scope(params.workspace_root)
    ceiling_reported = False
    highest_latched_ceiling: Provenance = Provenance.ABSENT

    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        rendered_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        observed_session_id: str | None = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id

        def _observe_raw_line(line: str, *, _turn_raw_lines: deque[str] = raw_lines) -> None:
            nonlocal ceiling_reported, highest_latched_ceiling
            cur_c = transport_evidence_ceiling(params.config, [line])
            highest_latched_ceiling = max(highest_latched_ceiling, cur_c)
            if ceiling_reported:
                return
            ceiling_reported = _report_evidence_ceiling_once(params.config, list(_turn_raw_lines))

        effect = InvokeAgentEffect(
            agent_name=params.agent_name,
            phase="development",
            prompt_file=str(params.prompt_file),
            drain="development",
        )

        def _capture_invocation_error(exc: Exception) -> None:
            nonlocal final_exception
            if isinstance(exc, AgentInvocationError):
                final_exception = exc

        pipeline_deps = params.pipeline_deps
        if pipeline_deps is None:
            raise RuntimeError("SmokeRunParams.pipeline_deps is required")
        try:
            event = execute_agent_effect(
                effect,
                params.unified_config,
                pipeline_deps,
                workspace_scope,
                bridge=cast("RestartAwareMcpBridge", params.bridge),
                display_context=params.display_context,
                display=params.display,
                run_id=run_id,
                raw_output_sink=raw_lines,
                rendered_output_sink=rendered_lines,
                raw_line_sink=_observe_raw_line,
                set_session_id_cb=_capture_session_id,
                invoke_agent=invoke_agent,
                invocation_options=params.options,
                raise_resumable_exit=True,
                agent_invocation_error_sink=_capture_invocation_error,
            )
            highest_latched_ceiling = _update_smoke_turn_state(
                raw_lines,
                rendered_lines,
                all_lines,
                live_output_lines,
                params.config,
                highest_latched_ceiling,
            )
            if not ceiling_reported:
                ceiling_reported = _report_evidence_ceiling_once(params.config, list(all_lines))
            current_session_id = observed_session_id or extract_transport_session_id(
                tuple(raw_lines)
            )
            if event == PipelineEvent.AGENT_SUCCESS:
                final_exception = None
            break
        except OpenCodeResumableExitError as exc:
            highest_latched_ceiling = _update_smoke_turn_state(
                raw_lines,
                rendered_lines,
                all_lines,
                live_output_lines,
                params.config,
                highest_latched_ceiling,
            )
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
            highest_latched_ceiling = _update_smoke_turn_state(
                raw_lines,
                rendered_lines,
                all_lines,
                live_output_lines,
                params.config,
                highest_latched_ceiling,
            )
            merged_output = list(raw_lines)
            for line in exc.parsed_output:
                if line not in merged_output:
                    merged_output.append(line)
            if merged_output:
                exc.parsed_output = merged_output
            final_exception = exc
            break

    return (
        list(all_lines),
        list(live_output_lines),
        current_session_id,
        final_exception,
        highest_latched_ceiling,
    )


def _clear_smoke_artifact(workspace_root: Path) -> None:
    artifact_path = (
        workspace_root / ".agent" / "artifacts" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.md"
    )
    artifact_path.unlink(missing_ok=True)
    from ralph.mcp.server._wire_ledger import WIRE_LEDGER_RELPATH

    ledger_path = workspace_root / WIRE_LEDGER_RELPATH
    ledger_path.unlink(missing_ok=True)


def _mock_negative_selector(env_getter: EnvGetter | None = None) -> str | None:
    if not is_mock_agy_override():
        return None
    getter = env_getter if env_getter is not None else os.getenv
    selector = getter("MOCK_AGY_BEHAVIOR") or "normal"
    if selector in {"missing_artifact", "missing_completion"}:
        return selector
    return None


def _clear_mock_broken_signals(workspace_root: Path, run_id: str) -> None:
    selector = _mock_negative_selector()
    if selector is None:
        return
    try:
        db = RunStateDB(workspace_root)
    except (OSError, RuntimeError, sqlite3.Error):
        db = None
    if db is not None:
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            if selector == "missing_artifact":
                db.clear_run_receipts(run_id)
            else:
                db.delete_completion_sentinel(run_id)
        with contextlib.suppress(OSError, RuntimeError, sqlite3.Error):
            db.close()
    clear_run_receipts(workspace_root, run_id)
    if selector == "missing_artifact":
        fallback = workspace_root / ".agent" / "tmp" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.md"
        fallback.unlink(missing_ok=True)
        _clear_smoke_artifact(workspace_root)
    else:
        sentinel_path = workspace_root / f".agent/completion_seen_{run_id}.json"
        sentinel_path.unlink(missing_ok=True)


def _is_smoke_artifact_submitted(workspace_root: Path, run_id: str = _SMOKE_RUN_ID) -> bool:
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
    return _check_completion_sentinel(
        workspace_root,
        run_id,
        sentinel_secret=_parent_broker_secret(),
    )


def _parser_event_error(
    config: AgentConfig,
    lines: list[str],
) -> str | None:
    parsed_event_count = _count_parsed_events(config, lines) if lines else 0
    if parsed_event_count == 0:
        return "no parser events were observed"
    return None


def _meaningful_output_error(
    config: AgentConfig,
    live_output_lines: list[str],
    lines: list[str],
) -> str | None:
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
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_AGY_BINARY")


def _cursor_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_CURSOR_BINARY")


def _kimi_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    getter = env_getter if env_getter is not None else os.environ.get
    return getter("RALPH_KIMI_BINARY")


def _opencode_binary_override_env(env_getter: EnvGetter | None = None) -> str | None:
    from ralph.config.agent_detection import (
        opencode_binary_override,
    )

    return opencode_binary_override(env_getter)


def is_mock_agy_override() -> bool:
    override = _agy_binary_override_env()
    if not override:
        return False
    basename = Path(override).name
    return basename.startswith("mock_agy") or basename == "mock_agy"


def _agy_upstream_diagnostic(
    lines: list[str],
    workspace_root: Path,
    *,
    env_getter: EnvGetter | None = None,
) -> str | None:
    if lines:
        return None
    if read_smoke_test_result_artifact(workspace_root) is not None:
        return None
    if is_mock_agy_override():
        getter = env_getter if env_getter is not None else os.getenv
        behavior = getter("MOCK_AGY_BEHAVIOR") or "normal"
        if behavior in {"no_output", "malformed_stream"}:
            return (
                "AGY --print returned empty stdout; "
                "the mock produced no usable stream output "
                f"(MOCK_AGY_BEHAVIOR={behavior})"
            )
        if behavior not in {"quota_exhausted", "auth_failure", "unknown_failure"}:
            return (
                "mock AGY produced empty stdout by design "
                f"(MOCK_AGY_BEHAVIOR={behavior}) — harness captured this correctly"
            )
    reason = agy_empty_output_reason(lines, cli_log_path=_AGY_CLI_LOG_PATH)
    if reason is not None:
        return reason
    return (
        "AGY --print returned empty stdout; "
        "check ~/.gemini/antigravity-cli/cli.log for model-resolution or quota errors"
    )


def _failed_result_frame_error(lines: list[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            raw_frame: object = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(raw_frame, dict):
            continue
        frame = cast("dict[str, object]", raw_frame)
        result = frame.get("result")
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        if isinstance(status, str) and status.upper() not in {"SUCCESS", "SUCCEEDED", "OK"}:
            error_text = result.get("error")
            if isinstance(error_text, str) and error_text.strip() == "timeout waiting for response":
                continue
            return f"result frame reported status={status.upper()}"
    return None


def _parser_diagnostics(config: AgentConfig, lines: list[str]) -> list[str]:
    diagnostics: list[str] = []
    if parser_error := _parser_event_error(config, lines):
        diagnostics.append(parser_error)
    if failed_result_error := _failed_result_frame_error(lines):
        diagnostics.append(failed_result_error)
    if empty_opencode_error := _opencode_empty_transcript_error(config, lines):
        diagnostics.append(empty_opencode_error)
    return diagnostics


def _opencode_empty_transcript_error(
    config: AgentConfig,
    lines: list[str],
) -> str | None:
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
    del artifact_submitted
    if tool_activity_seen is not None:
        return tool_activity_seen
    return bool(_tool_activity_seen(params.config, lines)) if lines else False


def _detect_multimodal_break(
    params: SmokeRunParams,
    run_id: str,
    errors: list[str],
) -> None:
    fixture_size = params.multimodal_fixture_size if params.multimodal_fixture_size else (0, 0)
    mult_evidence = grade_multimodal_evidence(
        params.workspace_root,
        run_id,
        output_file=params.output_file,
        fixture_relpath=SMOKE_FIXTURE_RELNAME,
        fixture_size=fixture_size,
        secret=_parent_broker_secret(),
        require_perception_secret=params.require_multimodal_perception_secret,
    )
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


def _detect_smoke_errors(
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
    errors = _detect_break_indicators(lines)
    errors.extend(_raw_transcript_corruption_errors(params.workspace_root, params.config))
    if support is not None and params.agent_name:
        errors.extend(
            _detect_capability_breaks(support, observed_capabilities, params.config, lines)
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
    unit_id = raw_log_unit_id_for(config)
    if not unit_id:
        return []
    raw_path = raw_log_path_for(workspace_root, unit_id, model=config.model)
    return [
        f"raw transcript corrupted: {br.detail}"
        for br in detect_raw_log_breaks(raw_path, transport=config.transport)
    ]


def _visible_output_overrun_error(config: AgentConfig, visible_output_count: int) -> str | None:
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
    tokens = _command_tokens(config)
    if _STREAM_JSON_OUTPUT_FLAG in tokens:
        return "claude-headless"
    return tokens[0].lower() if tokens else ""


def _command_tokens(config: AgentConfig) -> list[str]:
    try:
        tokens = shlex.split(config.cmd)
    except ValueError:
        return []
    return tokens + (config.output_flag or "").split()


def _artifact_submission_evidence(
    workspace_root: Path,
    run_id: str,
    *,
    submitted: bool,
    secret: str | None,
) -> Evidence:
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
    lowered = name.lower()
    return (
        lowered.startswith("ralph_")
        or lowered.startswith("mcp__ralph__")
        or lowered in _RALPH_DISPATCHER_TOOL_NAMES
    )


def _advertised_tool_names_from_init_frame(obj: dict[str, object]) -> list[str] | None:
    if obj.get("event") == "init":
        init_info = obj.get("init")
        if not isinstance(init_info, dict):
            return None
        raw_tools = cast("dict[str, object]", init_info).get("tools")
    elif obj.get("type") == "system" and obj.get("subtype") == "init":
        raw_tools = obj.get("tools")
    else:
        return None

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
    ceiling = transport_evidence_ceiling(config, lines)
    if ceiling is Provenance.ABSENT:
        return False
    logger.info(
        "smoke: transport evidence ceiling is {} — init frame's advertised "
        "tools cap the best possible verdict at this grade for this run",
        ceiling.name,
    )
    return True


def _skip_multimodal_workspace_monitor(
    _workspace_root: Path,
    _classifier: WorkspaceChangeClassifier | None,
) -> WorkspaceMonitor | None:
    return None


def _run_smoke_agent(
    params: SmokeRunParams,
    run_id: str = _SMOKE_RUN_ID,
) -> SmokeRunResult:
    (
        all_lines,
        live_output_lines,
        current_session_id,
        final_exception,
        latched_ceiling,
    ) = _execute_smoke_turns(params, None, run_id=run_id)

    lines = all_lines
    session_id = current_session_id or extract_transport_session_id(tuple(lines))
    _clear_mock_broken_signals(params.workspace_root, run_id)
    secret = _parent_broker_secret()
    artifact_submitted = _is_smoke_artifact_submitted(params.workspace_root, run_id)
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
    transport_ceiling = max(latched_ceiling, transport_evidence_ceiling(params.config, lines))
    parsed_output_lines = _meaningful_output_lines(params.config, lines) if lines else []
    live_filtered = [line for line in live_output_lines if line.strip()][
        :_MAX_MEANINGFUL_OUTPUT_LINES
    ]
    meaningful_output_lines = parsed_output_lines or live_filtered
    subagent_evidence = _subagent_smoke_evidence(params.config, lines)

    if params.display is not None:
        observed_capabilities = params.display.capability_recorder.observed_capabilities()
    else:
        observed_capabilities = frozenset()
    if params.support_resolver is not None:
        resolved_support = params.support_resolver(params.agent_name)
    else:
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
    multimodal_tool_used: Evidence | None = getattr(params, "_multimodal_tool_used_evidence", None)
    if multimodal_tool_used is None and not params.multimodal_requested:
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
    multimodal_fixture_size: tuple[int, int] | None = None
    if multimodal:
        multimodal_fixture_size = generate_fixture_geometry()
        fixture_path = workspace_root / SMOKE_FIXTURE_RELNAME
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
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
            prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(
                mcp_toml_path.parent, parents=True, exist_ok=True
            ),
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
        transport=agent_config.transport,
    ) as bridge:
        if effective_output_file.exists():
            effective_output_file.unlink()
        _clear_smoke_artifact(workspace_root)
        _clear_session_completion_sentinel(workspace_root, spec.run_id)

        agent_prefix = agent_name.split("/", maxsplit=1)[0]
        session_ceiling = _AGENT_SESSION_CEILINGS.get(agent_prefix, _SMOKE_MAX_SESSION_SECONDS)
        smoke_general = config.general.model_copy(
            update={
                "agent_idle_timeout_seconds": _SMOKE_IDLE_TIMEOUT_SECONDS,
                "agent_max_session_seconds": session_ceiling,
            }
        )
        smoke_config = config.model_copy(update={"general": smoke_general})

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
                    options=InvokeOptions(
                        workspace_monitor_factory=(
                            _skip_multimodal_workspace_monitor if multimodal else None
                        )
                    ),
                    display_context=display_context,
                    bridge=bridge,
                    pipeline_deps=effective_pipeline_deps,
                    subagents_requested=subagents,
                    display=fresh_display,
                    multimodal_requested=multimodal,
                    multimodal_fixture_size=multimodal_fixture_size,
                    require_multimodal_perception_secret=not agent_name.startswith("ccs/"),
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


if _SMOKE_MAX_TURNS < 1:
    raise RuntimeError("_SMOKE_MAX_TURNS must be >= 1")
if _SMOKE_IDLE_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0")
if _AGENT_SESSION_CEILINGS["agy"] <= _SMOKE_IDLE_TIMEOUT_SECONDS:
    raise RuntimeError("_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS")
