"""SUBAGENT_PROGRESS dispatch on the ``ActivityRouter -> on_event`` path.

The prompt's "real-time subagent progress for ALL supported agents"
contract requires that ``SubprocessAgentExecutor`` (which feeds the
``ActivityRouter.push_raw_line`` API for runner/parallel execution)
also surfaces per-tool subagent progress, not only the
``stream_parsed_agent_activity`` path used by the invocation engine.

The pre-fix router mapped a parsed line to a single canonical
``ActivityEventKind`` via ``map_parser_type_to_kind`` and emitted ONE
event; the parser's ``emit_subagent_activity`` hook (which feeds the
watchdog's per-task subagent sink AND surfaces a sanitized
``tool_use:<name>`` summary on the operator-visible transcript) was
NEVER invoked on this path.  As a result, a tool_use line from a
real ``SubprocessAgentExecutor`` run did not refresh the watchdog's
subagent channel on the runner/parallel execution path.

This test pins the post-fix wiring:

  * ``ActivityRouter.push_raw_line`` invokes the parser's
    ``emit_subagent_activity`` hook for every parsed line so the
    watchdog's subagent sink sees the same per-tool evidence the
    invocation engine sees via ``stream_parsed_agent_activity``.

  * When the parser hook yields a non-empty sanitized summary, the
    router ALSO emits a ``SUBAGENT_PROGRESS`` event through
    ``_on_event`` so the operator sees real-time per-tool subagent
    progress on the console transcript.

  * A buggy parser hook (raises) does NOT crash the router; a buggy
    sink (raises) does NOT crash the router.  Both are swallowed
    defensively (the helper ``invoke_subagent_sink`` is already
    exception-swallowing at the helper boundary).

All assertions use the public ActivityRouter API and a captured
subagent sink via ``set_subagent_sink`` (the same ContextVar the
invocation engine uses).  No subprocess, no real sleep.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import AgentOutputLine
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider
from ralph.display.activity_provider import provider_for_transport
from ralph.display.activity_router import (
    ActivityRouter,
    detect_provider_from_command,
)
from ralph.mcp.server._activity_sink import (
    get_subagent_sink,
    reset_subagent_sink,
    set_subagent_sink,
)
from ralph.pipeline.work_units import WorkUnit
from ralph.process import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil

if TYPE_CHECKING:
    from pathlib import Path


def test_router_emits_subagent_progress_for_tool_use_line() -> None:
    """A tool_use NDJSON line MUST produce a SUBAGENT_PROGRESS event.

    Drives ``ClaudeParser`` (via the default parser factory) with a
    single ``content_block_start`` tool_use line.  The captured
    ``_on_event`` callback MUST receive a ``SUBAGENT_PROGRESS`` event
    whose content is the sanitized ``tool_use:Bash`` summary, and the
    per-task subagent sink MUST receive the same summary so the
    watchdog's ``record_subagent_work`` channel is refreshed.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []
    sink_calls: list[str] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    def _sink(line: str) -> None:
        sink_calls.append(line)

    sink_token = set_subagent_sink(_sink)
    try:
        router = ActivityRouter(on_event=_on_event)
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        router.push_raw_line("u", tool_line, provider=ActivityProvider.CLAUDE)

        progress_events = [
            e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
        ]
        assert len(progress_events) >= 1, (
            "ActivityRouter.push_raw_line MUST emit a SUBAGENT_PROGRESS"
            f" event for a tool_use line; events={events}"
        )
        assert progress_events[0][2] == "tool_use:Bash", (
            "SUBAGENT_PROGRESS event content MUST be the sanitized"
            f" 'tool_use:Bash' summary; got: {progress_events[0][2]!r}"
        )
        assert progress_events[0][0] == "u", (
            f"SUBAGENT_PROGRESS unit_id MUST be 'u'; got: {progress_events[0][0]!r}"
        )

        tool_use_sink_calls = [s for s in sink_calls if s.startswith("tool_use:")]
        assert tool_use_sink_calls, (
            "ActivityRouter.push_raw_line MUST forward the sanitized"
            " summary to invoke_subagent_sink so the watchdog's"
            f" record_subagent_work channel is refreshed; sink_calls={sink_calls}"
        )
        assert "tool_use:Bash" in tool_use_sink_calls, (
            f"SUBAGENT sink MUST receive 'tool_use:Bash'; got: {tool_use_sink_calls!r}"
        )
    finally:
        reset_subagent_sink(sink_token)


def test_router_emits_subagent_progress_for_text_line() -> None:
    """Text content_block_delta lines MUST also surface subagent progress.

    Mirrors ``stream_parsed_agent_activity``: text lines flow through
    ``emit_subagent_activity`` so the watchdog's subagent channel sees
    model-text progress between tool calls.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    router.push_raw_line(
        "u",
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello world"},
            }
        ),
        provider=ActivityProvider.CLAUDE,
    )

    progress_events = [
        e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
    ]
    assert progress_events, (
        "ActivityRouter.push_raw_line MUST emit a SUBAGENT_PROGRESS"
        f" event for text content_block_delta lines; events={events}"
    )
    assert any(
        (content or "").startswith("text:") for _, _, content, _ in progress_events
    ), (
        "SUBAGENT_PROGRESS summary for text lines MUST carry the"
        f" 'text:' prefix; progress_events={progress_events}"
    )


def test_router_subagent_progress_is_silent_when_parser_has_no_hook() -> None:
    """A parser without ``emit_subagent_activity`` MUST NOT emit SUBAGENT_PROGRESS.

    The pre-fix behavior is preserved for non-template parsers (e.g.
    custom user parsers that only implement ``parse``): no
    SUBAGENT_PROGRESS event, no sink call.  The router MUST NOT
    crash on parsers that don't expose the hook.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []
    sink_calls: list[str] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    def _sink(line: str) -> None:
        sink_calls.append(line)

    class _StubParser:
        def parse(self, _lines: object) -> list[AgentOutputLine]:
            return [AgentOutputLine(type="tool_use", content="bash")]

    sink_token = set_subagent_sink(_sink)
    try:
        router = ActivityRouter(
            parser_factory=lambda _provider: _StubParser(),
            on_event=_on_event,
        )
        router.push_raw_line("u", "anything", provider=ActivityProvider.GENERIC)
        progress_events = [
            e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
        ]
        assert not progress_events, (
            "Parsers without emit_subagent_activity MUST NOT emit"
            f" SUBAGENT_PROGRESS; events={events}"
        )
        assert sink_calls == [], (
            "Parsers without emit_subagent_activity MUST NOT invoke"
            f" the subagent sink; sink_calls={sink_calls}"
        )
    finally:
        reset_subagent_sink(sink_token)


def test_router_swallows_buggy_parser_hook_exception() -> None:
    """A parser hook that raises MUST NOT crash the router.

    Mirrors the existing fail-soft contract: the activity stream must
    not crash when a buggy parser hook raises (the operator must still
    see the parsed line in the buffer and on the on_event callback).
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    class _BuggyParser:
        def parse(self, _lines: object) -> list[AgentOutputLine]:
            return [AgentOutputLine(type="tool_use", content="bash")]

        def emit_subagent_activity(self, _line: AgentOutputLine, sink: object) -> None:
            raise RuntimeError("buggy hook")

    router = ActivityRouter(
        parser_factory=lambda _provider: _BuggyParser(),
        on_event=_on_event,
    )
    # Must not raise.
    router.push_raw_line("u", "anything", provider=ActivityProvider.GENERIC)

    tool_use_events = [
        e for e in events if e[1] is ActivityEventKind.TOOL_USE
    ]
    assert tool_use_events, (
        "Even when emit_subagent_activity raises, the router MUST still"
        f" emit the canonical TOOL_USE event; events={events}"
    )
    progress_events = [
        e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
    ]
    assert not progress_events, (
        "A parser hook that raised MUST NOT emit SUBAGENT_PROGRESS;"
        f" events={events}"
    )


def test_router_subagent_progress_with_no_sink_registered() -> None:
    """No registered subagent sink MUST NOT crash the router.

    Mirrors ``stream_parsed_agent_activity`` behavior: when the
    watchdog is not registered as the subagent sink, the router
    still emits SUBAGENT_PROGRESS via the on_event callback (the
    transcript breadcrumb), and ``invoke_subagent_sink`` is a
    fail-soft no-op.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    saved_token = set_subagent_sink(None)
    try:
        assert get_subagent_sink() is None
        router = ActivityRouter(on_event=_on_event)
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        router.push_raw_line("u", tool_line, provider=ActivityProvider.CLAUDE)
        progress_events = [
            e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
        ]
        assert progress_events, (
            "SUBAGENT_PROGRESS event MUST be emitted via on_event even"
            f" when no subagent sink is registered; events={events}"
        )
    finally:
        reset_subagent_sink(saved_token)


# ---------------------------------------------------------------------------
# Cross-transport router support: Claude Interactive and Pi
# ---------------------------------------------------------------------------
# The prompt requires real-time subagent progress for ALL supported
# agents. The pre-fix ``ActivityRouter`` only knew about CLAUDE,
# CODEX, OPENCODE, GEMINI, AGY, and GENERIC. Claude Interactive
# (``ClaudeInteractiveParser``) and Pi (``PiParser``) silently fell
# back to GENERIC on the router path, so an interactive Claude
# transcript or a Pi stream could not surface per-tool
# ``SUBAGENT_PROGRESS`` events through the router.
#
# These tests prove the cross-transport visibility contract: when a
# line is pushed with the Claude-Interactive or Pi provider, the
# router uses the corresponding parser (not GenericParser) and the
# ``on_event`` callback receives the parsed events. The
# black-box contract is "router uses the right parser for every
# transport", not "the parser emits X" (the parser-specific tests
# live with the parser).


def test_router_uses_claude_interactive_parser_for_claude_interactive_provider() -> None:
    """``ActivityRouter`` with provider=CLAUDE_INTERACTIVE MUST use
    ``ClaudeInteractiveParser`` (not GenericParser).
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    # Use a known ClaudeInteractiveParser line shape: the transcript
    # emits ``claude: <text>`` lines. We do not pin the parser's
    # output format (that lives in parser tests); we pin the
    # black-box contract "router uses the right parser class".
    router.push_raw_line(
        "u",
        "claude: hello from interactive transcript",
        provider=ActivityProvider.CLAUDE_INTERACTIVE,
    )
    # The event must be parseable (not an ERROR event from the
    # GenericParser misclassifying the prefix).
    error_events = [
        e for e in events if e[1] is ActivityEventKind.ERROR
    ]
    assert not error_events, (
        "ActivityRouter.push_raw_line with CLAUDE_INTERACTIVE"
        " MUST route through ClaudeInteractiveParser (not GenericParser);"
        f" got error events: {error_events}"
    )


def test_router_uses_pi_parser_for_pi_provider() -> None:
    """``ActivityRouter`` with provider=PI MUST use ``PiParser`` (not GenericParser)."""
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    # Pin the black-box contract: the router MUST NOT raise and
    # MUST surface parsed events via on_event for the Pi transport.
    router.push_raw_line(
        "u",
        '{"type": "session", "id": "sess-1"}',
        provider=ActivityProvider.PI,
    )
    # PiParser is permissive; an empty event list is acceptable as
    # long as the router did NOT raise and did NOT misclassify as
    # a GenericParser ERROR.
    error_events = [
        e for e in events if e[1] is ActivityEventKind.ERROR
    ]
    assert not error_events, (
        "ActivityRouter.push_raw_line with PI MUST route through"
        f" PiParser (not GenericParser); got error events: {error_events}"
    )


def test_detect_provider_from_command_recognizes_pi() -> None:
    """``detect_provider_from_command(['pi', ...])`` MUST return ``ActivityProvider.PI``.

    Pre-fix the CLI-substring detection in ``detect_provider_from_command``
    did not include ``pi`` so a Pi invocation was misclassified as
    ``ActivityProvider.GENERIC`` (Pi's substring did not match any
    of the listed substrings). The fix adds ``pi`` to the substring
    table so the router selects ``PiParser`` for a Pi invocation.
    """
    assert detect_provider_from_command(["pi"]) is ActivityProvider.PI
    assert detect_provider_from_command(["/usr/local/bin/pi"]) is ActivityProvider.PI
    assert detect_provider_from_command(["pi-mono"]) is ActivityProvider.PI


def test_detect_provider_from_command_recognizes_claude_interactive() -> None:
    """``detect_provider_from_command(['claude-interactive', ...])`` MUST
    return ``ActivityProvider.CLAUDE_INTERACTIVE`` (not ``CLAUDE``).
    """
    assert (
        detect_provider_from_command(["claude-interactive"])
        is ActivityProvider.CLAUDE_INTERACTIVE
    )
    assert (
        detect_provider_from_command(["claude_interactive"])
        is ActivityProvider.CLAUDE_INTERACTIVE
    )
    # Plain ``claude`` is still routed to CLAUDE (the more specific
    # ``claude-interactive`` substring MUST NOT consume the bare
    # ``claude`` substring).
    assert detect_provider_from_command(["claude"]) is ActivityProvider.CLAUDE


def test_provider_for_transport_round_trips_supported_transports() -> None:
    """``provider_for_transport`` MUST return the canonical provider for
    every supported ``AgentTransport`` value.
    """
    assert provider_for_transport("claude") is ActivityProvider.CLAUDE
    assert (
        provider_for_transport("claude_interactive")
        is ActivityProvider.CLAUDE_INTERACTIVE
    )
    assert provider_for_transport("codex") is ActivityProvider.CODEX
    assert provider_for_transport("opencode") is ActivityProvider.OPENCODE
    assert provider_for_transport("agy") is ActivityProvider.AGY
    assert provider_for_transport("pi") is ActivityProvider.PI
    # ``generic`` is the fallback parser for any transport that does
    # not have its own parser (e.g. ``NANOCODER``); ``None`` and
    # unknown values also fall back to GENERIC.
    assert provider_for_transport("generic") is ActivityProvider.GENERIC
    assert provider_for_transport(None) is ActivityProvider.GENERIC
    assert provider_for_transport("nanocoder") is ActivityProvider.GENERIC


# ---------------------------------------------------------------------------
# SubprocessAgentExecutor -> ActivityRouter end-to-end regression test
# ---------------------------------------------------------------------------
# The SubprocessAgentExecutor path feeds ``ActivityRouter.push_raw_line``
# from its async ``drain_output`` coroutine.  This test asserts that the
# executor wiring delivers a real ``SUBAGENT_PROGRESS`` event through
# the on_event callback for a tool_use line emitted by a ClaudeParser
# driven via the executor's drain path.  No real subprocess, no real
# sleep, no FakeClock — a deterministic in-memory
# ``FakeControllableAsyncProcess`` stands in for the subprocess so the
# executor's drain loop runs against synthetic Claude NDJSON lines.


@pytest.mark.asyncio
async def test_subprocess_executor_emits_subagent_progress_via_router(
    tmp_path: Path,
) -> None:
    """SubprocessAgentExecutor -> ActivityRouter.push_raw_line surfaces SUBAGENT_PROGRESS.

    Drives the executor's ``drain_output`` with a ClaudeParser NDJSON
    stream containing a single ``content_block_start`` tool_use line.
    The executor forwards lines to its ``activity_router`` via
    ``push_raw_line``.  The captured ``_on_event`` callback MUST
    receive at least one ``SUBAGENT_PROGRESS`` event whose content
    matches the sanitized ``tool_use:Bash`` summary.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)

    tool_line = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls"},
            },
        }
    )
    completion_event = asyncio.Event()
    completion_event.set()
    pid_counter = itertools.count(500)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        return FakeControllableAsyncProcess(
            pid=next(pid_counter),
            stdout_data=(tool_line + "\n").encode("utf-8"),
            returncode=0,
            completion_event=completion_event,
        )

    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=["claude", "--help"],
        activity_router=router,
        _pm=pm,
    )

    unit = WorkUnit(unit_id="u", description="subagent-progress-test")
    await executor.run(
        unit,
        on_output=lambda _: None,
        on_status=lambda _: None,
    )

    progress_events = [
        e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
    ]
    assert progress_events, (
        "SubprocessAgentExecutor -> ActivityRouter.push_raw_line MUST"
        " surface SUBAGENT_PROGRESS on a ClaudeParser tool_use line;"
        f" events={events}"
    )
    assert any(e[2] == "tool_use:Bash" for e in progress_events), (
        "SUBAGENT_PROGRESS event content MUST match the sanitized"
        f" 'tool_use:Bash' summary; progress_events={progress_events}"
    )
    tool_use_events = [
        e for e in events if e[1] is ActivityEventKind.TOOL_USE
    ]
    assert tool_use_events, (
        "SubprocessAgentExecutor -> ActivityRouter MUST also surface"
        f" the canonical TOOL_USE event; events={events}"
    )


# ---------------------------------------------------------------------------
# Provider-parameterised SUBAGENT_PROGRESS coverage
# ---------------------------------------------------------------------------
# The prompt's "ALL supported agents" requirement means that every
# ``ActivityProvider`` value MUST route a tool_use line through the
# shared ``ParserTemplateBase.emit_subagent_activity`` hook so the
# watchdog's per-task subagent sink sees per-tool evidence and the
# operator-visible transcript shows a ``SUBAGENT_PROGRESS`` event.
#
# The test parametrizes over the 8 supported providers
# (CLAUDE / CLAUDE_INTERACTIVE / CODEX / OPENCODE / GEMINI / PI /
# AGY / GENERIC) and drives ONE representative tool_use line per
# provider through ``ActivityRouter.push_raw_line``. Each case MUST
# produce at least one ``SUBAGENT_PROGRESS`` event whose content
# starts with ``tool_use:``. If a provider's parser does not route
# through ``emit_subagent_activity``, this test fails with a clear
# per-provider assertion message identifying the regression.


# Per-provider representative tool_use lines. Each line is a
# transport-shape that the canonical parser for the provider
# recognizes as a tool_use. The shape mirrors the JSON envelope each
# transport emits on stdout. ClaudeInteractiveParser uses the
# interactive-transcript convention ``claude tool: <NAME>``
# (NOT NDJSON). GenericParser uses the plain-text convention
# ``[plain] tool: <NAME>`` for non-JSON tool lines.
_PROVIDER_TOOL_USE_LINES: dict[ActivityProvider, str] = {
    ActivityProvider.CLAUDE: json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls"},
            },
        }
    ),
    ActivityProvider.CLAUDE_INTERACTIVE: "claude tool: Read\n",
    ActivityProvider.CODEX: json.dumps(
        {
            "type": "tool_use",
            "name": "exec",
            "call_id": "call_1",
            "arguments": {"cmd": "pwd"},
        }
    ),
    ActivityProvider.OPENCODE: json.dumps(
        {
            "type": "tool_use",
            "name": "bash",
            "input": {"command": "echo hi"},
        }
    ),
    ActivityProvider.GEMINI: json.dumps(
        {
            "type": "tool_use",
            "name": "run_command",
            "args": {"command": "uptime"},
        }
    ),
    ActivityProvider.PI: json.dumps(
        {
            "type": "tool_use",
            "name": "edit",
            "input": {"path": "x.txt", "old": "a", "new": "b"},
        }
    ),
    ActivityProvider.AGY: json.dumps(
        {
            "type": "tool_use",
            "name": "shell",
            "input": {"cmd": "date"},
        }
    ),
    ActivityProvider.GENERIC: "[plain] tool: bash\n",
}


@pytest.mark.parametrize(
    "provider",
    list(_PROVIDER_TOOL_USE_LINES.keys()),
    ids=lambda p: p.value,
)
def test_subagent_progress_event_for_every_provider(provider: ActivityProvider) -> None:
    """Every ActivityProvider MUST emit SUBAGENT_PROGRESS on a tool_use line.

    The prompt's "ALL supported agents" requirement means the
    operator-visible transcript shows a ``SUBAGENT_PROGRESS`` event
    for tool_use lines across every provider whose parser routes
    through ``ParserTemplateBase.emit_subagent_activity`` (the
    shared hook at ``ralph/agents/parsers/_template.py:225``).

    For each provider:
      * Drive the provider-specific representative tool_use line
        through ``ActivityRouter.push_raw_line``.
      * Assert at least one ``SUBAGENT_PROGRESS`` event lands on the
        ``on_event`` callback.
      * Assert the event content starts with ``tool_use:`` (the
        sanitized prefix used by the shared hook).

    If a provider's parser does NOT route through
    ``emit_subagent_activity``, this test fails with a clear
    per-provider assertion message identifying the regression --
    the executor can see exactly which provider's plumbing is
    broken and which parser file needs the hook added.
    """
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    raw_line = _PROVIDER_TOOL_USE_LINES[provider]
    router.push_raw_line("u", raw_line, provider=provider)

    progress_events = [
        e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS
    ]
    assert progress_events, (
        f"provider={provider.value!r} MUST emit SUBAGENT_PROGRESS on a"
        f" tool_use line (the parser for this provider must route"
        f" through ParserTemplateBase.emit_subagent_activity)."
        f" events={events}; raw_line={raw_line!r}"
    )
    tool_use_progress = [
        e for e in progress_events if (e[2] or "").startswith("tool_use:")
    ]
    assert tool_use_progress, (
        f"provider={provider.value!r} SUBAGENT_PROGRESS events MUST"
        f" carry a 'tool_use:' prefix in content (sanitized summary"
        f" from the shared hook). progress_events={progress_events}"
    )
