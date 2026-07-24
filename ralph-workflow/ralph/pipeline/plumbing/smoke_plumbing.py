"""Smoke-test plumbing: shared core for the interactive-Claude parity check.

This module is the single owner of the smoke-test agent-invocation loop.
The CLI surface in :mod:`ralph.cli.commands.smoke` stays thin (option
parsing, report rendering, exit codes only).
"""

from __future__ import annotations

import os
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.completion_signals import is_artifact_submitted
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
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    read_smoke_test_result_artifact,
)
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import DefaultPipelineFactory, PipelineCore, PipelineDeps
from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams
from ralph.pipeline.session_bridge import build_session_bridge
from ralph.policy.loader import load_agents_policy_for_workspace_scope
from ralph.workspace.scope import resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.pro_support.hooks import ProPipelineHooks

_SMOKE_RELATIVE_DIR = Path("tmp/interactive-claude-smoke")
_SMOKE_OUTPUT_FILE = _SMOKE_RELATIVE_DIR / "todo-list.js"
_INTERACTIVE_AGENT = "claude/haiku"
_SMOKE_RUN_ID = "interactive-claude-smoke"
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


@dataclass(frozen=True)
class SmokeHarnessSpec:
    """Layout specification for an interactive smoke harness."""

    agent_name: str
    relative_dir: Path
    output_file: Path
    run_id: str


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
        # ``opencode/minimax-coding-plan/MiniMax-M3``) carries BOTH the
        # provider and the model, so one alias selects the full routing
        # target. The command builder strips the leading ``opencode/`` and
        # passes ``<provider>/<model>`` to ``opencode run --model``, which is
        # exactly the ``provider/model`` form the CLI expects. A sanitized
        # run_id keeps two provider/model smoke runs from colliding on
        # completion-sentinel / receipt paths.
        suffix = agent_name.removeprefix("opencode").lstrip("/")
        if not suffix:
            run_id = _OPENCODE_SMOKE_RUN_ID
        else:
            sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", suffix).strip("-")
            run_id = f"{_OPENCODE_SMOKE_RUN_ID}-{sanitized}"
        return SmokeHarnessSpec(
            agent_name=agent_name,
            relative_dir=_OPENCODE_SMOKE_RELATIVE_DIR,
            output_file=_OPENCODE_SMOKE_OUTPUT_FILE,
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
}
_SMOKE_MAX_TURNS = 5
_SMOKE_TRANSCRIPT_MAX_LINES = 400
_MAX_MEANINGFUL_OUTPUT_LINES = 8
_MIN_MEANINGFUL_OUTPUT_LINES = 3
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
_AGY_QUOTA_PATTERN = re.compile(r"RESOURCE_EXHAUSTED \(code 429\)", re.IGNORECASE)
_AGY_MODEL_INVALID_PATTERN = re.compile(
    r"Failed to resolve model flag\s+([^:]+):\s*model\s+(\S+)\s+is not recognized",
    re.IGNORECASE,
)
_AGY_MODEL_NOT_IN_CONFIG_PATTERN = re.compile(
    r"Model ID\s+(\S+)\s+not in local config",
    re.IGNORECASE,
)
_AGY_QUOTA_RESET_PATTERN = re.compile(
    r"Resets in\s+([^\s.]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SmokeRunResult:
    """Observed results from the interactive Claude smoke run."""

    agent_name: str
    transport: str
    output_file: Path
    file_created: bool
    session_id: str | None
    explicit_completion_seen: bool
    raw_line_count: int
    parsed_event_count: int
    tool_activity_seen: bool
    artifact_submitted: bool
    meaningful_output_lines: list[str]
    errors: list[str]
    subagents_requested: bool = False
    subagent_dispatch_count: int = 0
    subagent_dispatch_seen: bool = False
    subagent_result_seen: bool = False
    post_subagent_activity_seen: bool = False


@dataclass(frozen=True)
class SubagentSmokeEvidence:
    """Ordered subagent lifecycle evidence parsed from a smoke transcript."""

    dispatch_count: int = 0
    dispatch_seen: bool = False
    result_seen: bool = False
    post_result_activity_seen: bool = False


type EnvGetter = Callable[[str], str | None]


def _build_smoke_prompt(
    output_relpath: str,
    *,
    submit_artifact_tool_name: str,
    transport: AgentTransport | None = None,
    subagents: bool = False,
    subagent_prompt: str | None = None,
) -> str:
    """Return the prompt used for the parity smoke test."""
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

    completion_requirement = (
        "- The canonical smoke_test_result submission is the authoritative completion "
        "signal. Do NOT print a transcript completion marker; the harness will not trust one.\n"
        if transport == AgentTransport.AGY
        else "- When finished, call declare_complete.\n"
    )

    return (
        "Create a small JavaScript todo list implementation at "
        f"`{output_relpath}`.\n\n"
        "Requirements:\n"
        "- Keep it tiny: one file only.\n"
        "- Export a small in-memory todo list API.\n"
        "- Do not touch files outside tmp/.\n"
        "- Use the headless semantic guide as a rubric: session capture, tool activity, "
        "completion signal, parser events, and tmp artifact creation.\n"
        f"{subagent_requirements}"
        f"- Call `{submit_artifact_tool_name}` with "
        f'artifact_type="{SMOKE_TEST_RESULT_ARTIFACT_TYPE}" '
        "and put this complete Markdown document in the content argument:\n"
        f"```markdown\n{artifact_document}\n```\n"
        "- Submit through the tool; do not write an artifact file directly.\n"
        f"{completion_requirement}"
    )


def _normalized_tool_name(metadata: dict[str, object]) -> str:
    raw_name = metadata.get("tool")
    return raw_name.strip().lower() if isinstance(raw_name, str) else ""


def _tool_use_id(metadata: dict[str, object]) -> str | None:
    for key in ("tool_use_id", "call_id", "toolCallId", "callID", "callId"):
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
    """Return ordered, parser-derived evidence for the subagent smoke scenario."""
    parser = get_parser(_parser_key_for_config(config))
    # Count dispatches by DISTINCT call id, not by raw tool_use events. OpenCode
    # may stream a running state then a completed state for the same call, and a
    # completed tool now surfaces both a dispatch and a result -- both carry the
    # same callID. Counting raw events would see one subagent twice and reject
    # it as "observed 2". Two genuinely distinct dispatches still carry distinct
    # ids and are still rejected. Id-less dispatches (a parser that exposes no
    # id) cannot be de-duplicated, so each is counted, preserving the prior
    # behaviour for those transports.
    distinct_dispatch_ids: set[str] = set()
    idless_dispatch_count = 0
    first_dispatch_seen = False
    first_dispatch_id: str | None = None
    result_seen = False
    post_result_activity_seen = False
    for parsed in parser.parse(iter(lines)):
        metadata = parsed.metadata or {}
        tool_name = _normalized_tool_name(metadata)
        if parsed.type == "tool_use" and tool_name in _SUBAGENT_TOOL_NAMES:
            tool_id = _tool_use_id(metadata)
            if not first_dispatch_seen:
                first_dispatch_seen = True
                first_dispatch_id = tool_id
            if tool_id is None:
                idless_dispatch_count += 1
            else:
                distinct_dispatch_ids.add(tool_id)
            continue
        running_dispatch_total = len(distinct_dispatch_ids) + idless_dispatch_count
        if (
            running_dispatch_total == 1
            and not result_seen
            and parsed.type == "tool_result"
            and tool_name in _SUBAGENT_TOOL_NAMES
        ):
            result_id = _tool_use_id(metadata)
            if (first_dispatch_id is None and result_id is None) or first_dispatch_id == result_id:
                result_seen = True
            continue
        if result_seen and parsed.type in {"text", "thinking", "tool_use"}:
            post_result_activity_seen = True
    dispatch_count = len(distinct_dispatch_ids) + idless_dispatch_count
    return SubagentSmokeEvidence(
        dispatch_count=dispatch_count,
        dispatch_seen=dispatch_count > 0,
        result_seen=result_seen,
        post_result_activity_seen=post_result_activity_seen,
    )


def _subagent_smoke_error(evidence: SubagentSmokeEvidence) -> str | None:
    """Return the first missing ordered subagent signal, if any."""
    if not evidence.dispatch_seen:
        return "subagent dispatch was not observed"
    if evidence.dispatch_count != 1:
        return f"expected exactly one subagent dispatch, observed {evidence.dispatch_count}"
    if not evidence.result_seen:
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
    return False


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

    for _attempt in range(_SMOKE_MAX_TURNS):
        raw_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        rendered_lines: deque[str] = deque(maxlen=_SMOKE_TRANSCRIPT_MAX_LINES)
        observed_session_id: str | None = current_session_id

        def _capture_session_id(session_id: str) -> None:
            nonlocal observed_session_id
            observed_session_id = session_id

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
                bridge=cast("RestartAwareMcpBridge", params.bridge),
                display_context=params.display_context,
                run_id=run_id,
                raw_output_sink=raw_lines,
                rendered_output_sink=rendered_lines,
                set_session_id_cb=_capture_session_id,
                invoke_agent=invoke_agent,
                raise_resumable_exit=True,
            )
            all_lines.extend(raw_lines)
            live_output_lines.extend(rendered_lines)
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
            current_session_id = (
                exc.resumable_session_id
                or observed_session_id
                or extract_transport_session_id(tuple(raw_lines))
            )
            final_exception = exc
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
    lines: list[str],
    workspace_root: Path,
    transport: AgentTransport | None,
    *,
    run_id: str = _SMOKE_RUN_ID,
) -> bool:
    """Return whether the agent emitted an authoritative completion signal.

    The completion signal must be authoritative — not a transcript substring
    the model was told to print, which a misbehaving or partial run can emit
    without truly completing.

    - For non-AGY agents (Claude, etc.): the ``Task declared complete:``
      transcript marker emitted by ``handle_declare_complete`` is the
      authoritative signal, optionally corroborated by the completion
      sentinel at ``.agent/completion_seen_<run_id>.json``.
    - For AGY and Nanocoder: the canonical receipt at
      ``.agent/receipts/<run_id>/smoke_test_result.json`` is the
      authoritative signal. These transports can complete the smoke contract
      by submitting the smoke artifact without emitting Claude's transcript
      marker. Transcript substrings are explicitly NOT accepted for AGY:
      the prompt no longer tells the agent to print a marker, and any
      substring the model emits incidentally is treated as ordinary model
      output.
    """
    if transport in {AgentTransport.AGY, AgentTransport.NANOCODER}:
        return _is_smoke_artifact_submitted(workspace_root, run_id)
    return any("Task declared complete:" in line for line in lines)


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
    log_path = _AGY_CLI_LOG_PATH
    diagnostic = (
        "AGY --print returned empty stdout; "
        "check ~/.gemini/antigravity-cli/cli.log for model-resolution or quota errors"
    )
    if log_path.is_file():
        try:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4096:]
        except OSError:
            log_tail = ""
        if _AGY_QUOTA_PATTERN.search(log_tail):
            reset_match = _AGY_QUOTA_RESET_PATTERN.search(log_tail)
            reset_window = f" (resets in {reset_match.group(1)})" if reset_match else ""
            diagnostic = (
                "AGY --print returned empty stdout: individual API quota exhausted "
                f"(429 RESOURCE_EXHAUSTED){reset_window}. Wait for the quota reset or check "
                "~/.gemini/antigravity-cli/cli.log."
            )
        else:
            model_invalid_match = _AGY_MODEL_INVALID_PATTERN.search(log_tail)
            model_not_in_config_match = _AGY_MODEL_NOT_IN_CONFIG_PATTERN.search(log_tail)
            if model_invalid_match is not None:
                model_id = model_invalid_match.group(2)
                diagnostic = (
                    f"AGY --print returned empty stdout: model ID '{model_id}' "
                    "is not recognized by AGY. Check `agy models` and use the "
                    "exact display name; see ~/.gemini/antigravity-cli/cli.log."
                )
            elif model_not_in_config_match is not None:
                model_id = model_not_in_config_match.group(1)
                diagnostic = (
                    f"AGY --print returned empty stdout: model ID '{model_id}' "
                    "is not in AGY's local config. Check `agy models` and use "
                    "the exact display name; see ~/.gemini/antigravity-cli/cli.log."
                )
    return diagnostic


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
       plus structured tool events from the JSON-aware parsers).
    2. For AGY specifically: a workspace file write. The AGY source of
       truth at ``ralph-workflow/tmp/agy-source-of-truth.txt`` documents
       that AGY ``--print`` mode does not emit structured tool events on
       stdout; tool activity surfaces as side-effect file writes inside
       the workspace. The expected ``tmp/interactive-agy-smoke/todo-list.js``
       file is the canonical authoritative write; its presence proves the
       agent actually performed a tool action rather than only emitting
       text.
    """
    if tool_activity_seen is not None:
        return tool_activity_seen
    if _tool_activity_seen(params.config, lines) if lines else False:
        return True
    if params.config.transport == AgentTransport.NANOCODER and artifact_submitted:
        return True
    # AGY-specific authoritative signal: the expected workspace output
    # file was created (a real file-write side effect, not a model
    # self-report). Per the source-of-truth, AGY --print wires tool
    # activity through file writes rather than structured stdout events.
    return params.config.transport == AgentTransport.AGY and params.output_file.exists()


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
) -> list[str]:
    """Detect errors in smoke run results."""
    errors = _detect_break_indicators(lines)
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
    if session_id is None and params.config.transport not in {
        AgentTransport.AGY,
        AgentTransport.NANOCODER,
    }:
        errors.append("session ID was not observed")

    if not _explicit_completion_seen(
        lines, params.workspace_root, params.config.transport, run_id=run_id
    ):
        errors.append("declare_complete marker was not observed")

    if parser_error := _parser_event_error(params.config, lines):
        errors.append(parser_error)

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

    if params.config.transport == AgentTransport.AGY:
        diagnostic = _agy_upstream_diagnostic(lines, params.workspace_root)
        if diagnostic is not None:
            errors.append(diagnostic)

    visible_output_count = len([line for line in live_output_lines if line.strip()])
    if visible_output_count > _MAX_VISIBLE_OUTPUT_LINES:
        errors.append(
            "interactive output overran into too many visible lines; "
            "semantic output parity is still insufficient"
        )
    return errors


def _agy_tool_activity_seen(workspace_root: Path) -> bool:
    """Deprecated AGY tool-activity fallback. Returns False unconditionally.

    This helper used to read the persisted ``smoke_test_result`` artifact and
    return True when ``headless_guide_checks`` contained ``"tool activity"``,
    which let the smoke run self-certify tool activity from the
    model-authored artifact. That path was removed: tool activity must come
    from authoritative parser / transport events, never from the artifact
    contents. The function is preserved as a no-op stub so external
    imports keep working during the transition; the regression test
    ``tests/test_agy_execution_contract.py::test_agy_tool_activity_must_not_come_from_artifact``
    pins the new contract.
    """
    return False


def _run_smoke_agent(
    params: SmokeRunParams,
    run_id: str = _SMOKE_RUN_ID,
) -> SmokeRunResult:
    """Run the smoke agent and return results."""
    all_lines, live_output_lines, current_session_id, final_exception = _execute_smoke_turns(
        params, None, run_id=run_id
    )

    lines = all_lines
    session_id = current_session_id or extract_transport_session_id(tuple(lines))
    artifact_submitted = _is_smoke_artifact_submitted(params.workspace_root, run_id)
    # Authoritative completion signal — see ``_explicit_completion_seen`` docstring.
    # For AGY and Nanocoder the receipt (==``artifact_submitted``) is the
    # trusted signal; for other transports the ``Task declared complete:``
    # transcript marker from ``handle_declare_complete`` is the trusted
    # signal. We compute the bool here so the SmokeRunResult can surface it
    # without leaking transport-specific knowledge into the report.
    explicit_completion_seen = _explicit_completion_seen(
        lines, params.workspace_root, params.config.transport, run_id=run_id
    )
    parsed_event_count = _count_parsed_events(params.config, lines) if lines else 0
    # Tool activity MUST come from authoritative parser / transport events
    # or workspace file-write side effects — never from the agent-authored
    # ``headless_guide_checks`` artifact. See
    # ``_tool_activity_seen_for_errors`` docstring and the regression test
    # ``test_agy_tool_activity_must_not_come_from_artifact``.
    tool_activity_seen = _tool_activity_seen_for_errors(
        params,
        lines,
        tool_activity_seen=None,
        artifact_submitted=artifact_submitted,
    )
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

    errors = _detect_smoke_errors(
        params,
        lines,
        live_output_lines,
        session_id,
        final_exception,
        tool_activity_seen=tool_activity_seen,
        artifact_submitted=artifact_submitted,
        run_id=run_id,
    )

    config = params.config
    transport_name = config.transport.value if config.transport is not None else "generic"
    return SmokeRunResult(
        agent_name=params.agent_name,
        transport=transport_name,
        output_file=params.output_file,
        file_created=params.output_file.exists(),
        session_id=session_id,
        explicit_completion_seen=explicit_completion_seen,
        raw_line_count=len([line for line in lines if line.strip()]),
        parsed_event_count=parsed_event_count,
        tool_activity_seen=tool_activity_seen,
        artifact_submitted=artifact_submitted,
        meaningful_output_lines=meaningful_output_lines,
        errors=errors,
        subagents_requested=params.subagents_requested,
        subagent_dispatch_count=subagent_evidence.dispatch_count,
        subagent_dispatch_seen=subagent_evidence.dispatch_seen,
        subagent_result_seen=subagent_evidence.result_seen,
        post_subagent_activity_seen=subagent_evidence.post_result_activity_seen,
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
    """
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
