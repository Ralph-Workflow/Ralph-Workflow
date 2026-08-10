"""Per-harness multimodal smoke end-to-end suite (S-9 / S-12 / criterion 5).

The plan calls for two distinct end-to-end suites that exercise every
major coding harness against Ralph's multimodal MCP endpoints:

- **S-9** -- the AGY proof. The harness drives a deterministic
  ``mock_multimodal_agent.py`` subprocess through the production
  executor with AGY's ``--print --output-format=stream-json`` frame
  vocabulary and asserts the multimodal grade reaches WIRE on the
  positive case, fires a named break on the no-call and
  ignored-response cases.

- **S-12** -- parameterises the same harness across all six
  transports (``smoke-interactive-claude``,
  ``smoke-headless-claude``, ``smoke-interactive-agy``,
  ``smoke-interactive-nanocoder``, ``smoke-interactive-cursor``,
  ``smoke-interactive-opencode``), each with its redirect seam
  recorded in S-13. The same two-case (positive / ignore-response)
  shape applies on every transport.

Every test in this file is marked ``smoke`` AND ``subprocess_e2e``
so the production test suites (``make verify``, ``make test``, ...)
never run it. To run the suite manually:

    pytest tests/test_smoke_multimodal_end_to_end.py \\
        -m "smoke and subprocess_e2e"

The harness plumbing relies on a healthy
``tests/_support/mock_agy.py`` (and the matching mock fixtures for
the other five transports). In environments where that fixture is
broken (the canonical mock AGY binary is environment-bound), the
test surfaces that as a clear failure rather than silently
downgrading the run to the basic scenario (per S-14 / criterion 5
product criterion).
"""

from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import smoke as smoke_module
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(180),
]

if TYPE_CHECKING:
    from pathlib import Path


# Per-harness redirect seams (S-13). Each entry maps:
#   (transport_prefix, cli_command, default_agent_name, redirect_method)
# ``redirect_method`` is one of:
#   ``"agy_env"``   - via ``RALPH_AGY_BINARY`` env override
#   ``"cursor_env"`` - via ``RALPH_CURSOR_BINARY`` env override
#   ``"cmd_override"`` - via in-place ``AgentConfig.cmd`` rewrite to the
#     stub (the production harness's command builders let
#     ``agents.<name>.cmd`` override the resolved argv, so a rewrite
#     keeps the transport's argv shape intact without needing a PATH
#     shim for ``nanocoder`` / ``opencode``).
_TRANSPORTS: tuple[tuple[str, str, str, str], ...] = (
    ("claude", "smoke-interactive-claude", "claude/haiku", "cmd_override"),
    ("claude-headless", "smoke-headless-claude", "claude-headless/haiku", "cmd_override"),
    ("agy", "smoke-interactive-agy", "agy/gemini-3.6-flash-low", "agy_env"),
    ("nanocoder", "smoke-interactive-nanocoder", "nanocoder", "cmd_override"),
    ("cursor", "smoke-interactive-cursor", "cursor/auto", "cursor_env"),
    ("opencode", "smoke-interactive-opencode", "opencode/minimax/MiniMax-M3", "cmd_override"),
)

_TRANSPORT_IDS: tuple[str, ...] = tuple(t[0] for t in _TRANSPORTS)


def _resolve_transport_entry(transport: str) -> tuple[str, str, str, str]:
    for entry in _TRANSPORTS:
        if entry[0] == transport:
            return entry
    raise AssertionError(f"unknown transport {transport!r}")


def _stub_script_path() -> Path:
    """Return the filesystem path to the multimodal smoke stub agent."""
    from pathlib import Path

    return Path(__file__).resolve().parent / "_support" / "mock_multimodal_agent.py"


def _stub_is_executable() -> bool:
    """Return True iff the stub agent script exists with executable bits set."""
    import stat

    path = _stub_script_path()
    if not path.exists():
        return False
    mode = path.stat().st_mode
    return bool(mode & stat.S_IXUSR or mode & stat.S_IXGRP or mode & stat.S_IXOTH)


def _end_to_end_test_for_harness(
    workspace: Path,
    transport: str,
    *,
    positive: bool,
) -> SmokeRunResult:
    """Drive the multimodal stub through ``run_smoke_plumbing`` for one transport.

    Configures the harness's ``AgentConfig.cmd`` (or ``RALPH_AGY_BINARY`` /
    ``RALPH_CURSOR_BINARY`` env overrides for those two transports) so the
    harness spawns the multimodal stub as the agent. The ``positive=False``
    path sets ``MOCK_MULTIMODAL_IGNORE_RESPONSE=1`` so the stub dials the
    endpoint once and then forges the receipt. The ``MOCK_MULTIMODAL_SKIP_MEDIA=1``
    path is exercised by the dedicated ``test_skip_media_multimodal_run_exits_nonzero``
    case.
    """
    transport_prefix, _cli_cmd, agent_name, redirect_method = _resolve_transport_entry(transport)
    stub_path = _stub_script_path()
    if not stub_path.is_file():
        raise AssertionError(
            f"multimodal smoke stub missing at {stub_path}; "
            "the S-9 / S-12 harness-level proof requires the deterministic "
            "stub to be present"
        )
    if not os.access(stub_path, os.X_OK):
        raise AssertionError(
            f"multimodal smoke stub at {stub_path} is not executable; "
            "run `chmod +x tests/_support/mock_multimodal_agent.py` "
            "before invoking the S-9 / S-12 harness-level proof"
        )

    broker_secret = "multimodal-broker-secret-for-e2e-test"
    os.environ["RALPH_BROKER_SECRET"] = broker_secret
    os.environ["MOCK_MULTIMODAL_WORKSPACE_ROOT"] = str(workspace)
    os.environ["MOCK_MULTIMODAL_TRANSPORT"] = transport_prefix
    if positive:
        os.environ.pop("MOCK_MULTIMODAL_IGNORE_RESPONSE", None)
        os.environ.pop("MOCK_MULTIMODAL_SKIP_MEDIA", None)
    else:
        os.environ["MOCK_MULTIMODAL_IGNORE_RESPONSE"] = "1"
        os.environ.pop("MOCK_MULTIMODAL_SKIP_MEDIA", None)

    workspace_scope = WorkspaceScope(workspace)
    config = load_config(None, {}, workspace_scope=workspace_scope)

    registry = AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        raise AssertionError(
            f"transport {transport!r}: agent {agent_name!r} is not in the registry"
        )

    if redirect_method == "agy_env":
        os.environ["RALPH_AGY_BINARY"] = str(stub_path)
        agent_config = smoke_module._maybe_apply_agy_binary_override(agent_config)
        config = smoke_module._apply_agy_binary_override_to_config(config)
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})
    elif redirect_method == "cursor_env":
        os.environ["RALPH_CURSOR_BINARY"] = str(stub_path)
        agent_config = smoke_module._maybe_apply_cursor_binary_override(agent_config)
        config = smoke_module._apply_cursor_binary_override_to_config(config)
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})
    elif redirect_method == "cmd_override":
        quoted = shlex.quote(str(stub_path))
        new_cmd = f"{quoted} {agent_config.cmd or ''}".strip()
        agent_config = agent_config.model_copy(update={"cmd": new_cmd})
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})
    else:
        raise AssertionError(f"unknown redirect method {redirect_method!r}")

    display_context = make_display_context()
    deps = DefaultPipelineFactory().build(config, display_context)

    spec = resolve_smoke_harness_spec(agent_name)
    prompt_file = workspace / spec.relative_dir / "PROMPT.md"
    output_file = workspace / spec.output_file
    # The stub receives the harness's expected output path so the
    # multimodal token lines land in the file the harness grades.
    os.environ["MOCK_MULTIMODAL_OUTPUT_FILE"] = str(output_file)
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "# Multimodal smoke stub prompt\n"
        "Read smoke-fixture.png via the multimodal MCP endpoint, replay the "
        "server-minted handle, write receipts, and complete.\n",
        encoding="utf-8",
    )

    return run_smoke_plumbing(
        config=config,
        workspace_root=workspace,
        agent_name=agent_name,
        prompt_file=prompt_file,
        output_file=output_file,
        display_context=display_context,
        pipeline_deps=deps,
        multimodal=True,
    )


@pytest.mark.parametrize(
    "transport",
    _TRANSPORT_IDS,
)
def test_positive_multimodal_run_grades_wire(
    transport: str,
    tmp_path: Path,
) -> None:
    """Positive contract: a multimodal smoke run on every harness grades WIRE (criterion 5).

    The stub issues a full sequence of media-tool calls (read_media
    on the fixture path, replay of the server-minted handle, read_image
    metadata envelope for geometry + sha256), writes the receipts
    into the smoke output file, submits the artifact, and declares
    completion. The harness must grade the multimodal fact at WIRE.
    """
    result = _end_to_end_test_for_harness(tmp_path, transport, positive=True)
    assert result.multimodal_tool_used is not None
    assert result.multimodal_tool_used.provenance is result.multimodal_tool_used.provenance.WIRE, (
        f"transport {transport!r}: multimodal fact graded "
        f"{result.multimodal_tool_used.provenance.name!r} (expected WIRE) "
        f"-- detail: {result.multimodal_tool_used.detail}"
    )


@pytest.mark.parametrize(
    "transport",
    _TRANSPORT_IDS,
)
def test_ignore_response_multimodal_run_exits_nonzero(
    transport: str,
    tmp_path: Path,
) -> None:
    """Poisoned-response case: dial the endpoint but discard the response (criterion 5 causal use).

    The stub issues a real ``read_media`` call (so a verified
    wire-ledger record exists for the run), then DISCARDS the
    response and fabricates a UUID-based receipt with a guessed
    geometry / sha256. The graded multimodal fact must read the
    receipt from the server registry, so the fact grades
    ``WORKSPACE_EFFECT`` and the run fails the multimodal contract.
    """
    result = _end_to_end_test_for_harness(tmp_path, transport, positive=False)
    assert result.multimodal_tool_used is not None
    assert result.multimodal_tool_used.provenance is not result.multimodal_tool_used.provenance.WIRE, (
        f"transport {transport!r}: ignore-response stub dials the endpoint once "
        f"and discards the response; the multimodal fact must NOT grade WIRE "
        f"(got {result.multimodal_tool_used.provenance.name!r}; "
        f"detail: {result.multimodal_tool_used.detail})"
    )
    assert any("multimodal break" in err.lower() for err in result.errors), (
        f"transport {transport!r}: expected a multimodal break in errors, "
        f"got: {result.errors!r}"
    )


def test_skip_media_multimodal_run_exits_nonzero(tmp_path: Path) -> None:
    """No-call case: skipping the media tool call entirely fails the smoke run with a named break."""
    os.environ.pop("MOCK_MULTIMODAL_IGNORE_RESPONSE", None)
    os.environ["MOCK_MULTIMODAL_SKIP_MEDIA"] = "1"
    try:
        result = _end_to_end_test_for_harness(tmp_path, "agy", positive=False)
    finally:
        os.environ.pop("MOCK_MULTIMODAL_SKIP_MEDIA", None)
    assert result.multimodal_tool_used is not None
    assert result.multimodal_tool_used.provenance is not result.multimodal_tool_used.provenance.WIRE, (
        f"skip-media stub never makes the media call; the multimodal fact "
        f"must NOT grade WIRE (got {result.multimodal_tool_used.provenance.name!r}; "
        f"detail: {result.multimodal_tool_used.detail})"
    )
    assert any("multimodal break" in err.lower() for err in result.errors), (
        f"expected a multimodal break in errors, got: {result.errors!r}"
    )


# ---------------------------------------------------------------------------
# Stub-agent shape regression -- pinned locally so S-9 / S-12 can rely on the
# stub without re-implementing the transport-vocabulary each time.
# ---------------------------------------------------------------------------


def test_stub_agent_module_imports_cleanly() -> None:
    """The multimodal stub imports cleanly and exposes a runnable ``main``."""
    spec_path = _stub_script_path()
    if not spec_path.exists():
        pytest.skip("stub agent not on disk")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mock_multimodal_agent", spec_path
    )
    if spec is None or spec.loader is None:
        pytest.skip("stub agent spec not loadable")
    module = importlib.util.module_from_spec(spec)
    assert module is not None
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    assert hasattr(module, "_make_emit_functions")
    assert hasattr(module, "_resolve_transport")
    assert hasattr(module, "_dispatch")
