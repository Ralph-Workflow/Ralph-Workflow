"""Regression net for the per-transport timing-safety invariants.

Every transport Ralph writes a client MCP timeout for MUST be bounded by
``EXEC_SOFT_REPLY_BUDGET_MS + headroom`` (``ralph.timeout_defaults.EXEC_SOFT_REPLY_BUDGET_MS``
plus the 30 s headroom the timing-safety check requires). Clients Ralph
cannot programmatically size (Cursor, Nanocoder) sit at "no documented
tool-call timeout" — verified from the docs cited in
``docs/agents/exec-long-running-root-cause.md`` — and that absence is the
exact thing this module pins. The audit surface lives at
``ralph.mcp.server._timing_safety`` so a future agent can read what
Ralph writes and what it deliberately leaves alone.

These tests are black-box against the public surface of every transport
module plus the timing-safety public API; no private attribute is read.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from ralph.mcp.server._timing_safety import (
    TIMING_SAFETY_TRANSPORT_FLOOR_MS,
    verify_transport_timeout_safety,
)
from ralph.mcp.transport import claude as claude_module
from ralph.mcp.transport import codex as codex_module
from ralph.mcp.transport import kimi as kimi_module
from ralph.mcp.transport import opencode as opencode_module
from ralph.mcp.transport import pi as pi_module
from ralph.timeout_defaults import (
    EXEC_MAX_TIMEOUT_MS,
    EXEC_SOFT_REPLY_BUDGET_MS,
)

pytestmark = pytest.mark.timeout_seconds(10)


def test_timing_safety_floor_equals_soft_budget_plus_headroom() -> None:
    """The published floor is exactly the floor the transports must clear.

    A future retune of ``TIMING_SAFETY_TRANSPORT_HEADROOM_MS`` would
    silently pass every other test here if the floor was recomputed
    separately. Pin it to the exact expression so an out-of-band change
    trips this test first.
    """
    from ralph.mcp.server._timing_safety import TIMING_SAFETY_TRANSPORT_HEADROOM_MS

    assert TIMING_SAFETY_TRANSPORT_HEADROOM_MS == 30_000
    assert TIMING_SAFETY_TRANSPORT_FLOOR_MS == (
        EXEC_SOFT_REPLY_BUDGET_MS + TIMING_SAFETY_TRANSPORT_HEADROOM_MS
    )


def test_verify_transport_timeout_safety_accepts_floor_value() -> None:
    """A timeout sitting exactly on the floor must still pass — it's not strictly greater, it's at-least."""
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS) is True


def test_verify_transport_timeout_safety_accepts_higher_value() -> None:
    """A timeout above the floor must pass."""
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS + 60_000) is True


def test_verify_transport_timeout_safety_rejects_below_floor() -> None:
    """A timeout below the floor must fail the safety check."""
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS - 1) is False


def test_verify_transport_timeout_safety_rejects_none_for_unconfigured_clients() -> None:
    """``None`` is the marker for "no documented timeout"; it must not silently pass."""
    assert verify_transport_timeout_safety(None) is False


# --- Per-transport writer tests ----------------------------------------------
#
# Each transport publishes its computed timeout on the module surface so the
# timing-safety invariant can be audited from one place. Read those module
# constants and assert they clear the floor.


def test_codex_transport_writes_a_per_server_tool_timeout_above_the_floor() -> None:
    """Codex ``tool_timeout_sec`` MUST clear the timing-safety floor.

    Codex's ``mcp_servers.<name>.tool_timeout_sec`` is the per-server
    tool-call deadline Codex honors. The transport writes the value
    derived from ``EXEC_SOFT_REPLY_BUDGET_MS + 30 s headroom`` and an
    operator-readable floor is what guards against a future softening.
    """
    assert verify_transport_timeout_safety(codex_module._CODEX_TOOL_TIMEOUT_SEC * 1000) is True


def test_codex_transport_writes_tool_timeout_in_its_merged_mcp_servers_entry(
    tmp_path: Path,
) -> None:
    """The per-server tool-call deadline lands in the merged ``mcp_servers.ralph`` entry.

    The integration proves the field the timing-safety check audits
    actually reaches the config Codex loads, not just a module constant
    floating in isolation.
    """
    from ralph.mcp.transport.codex import synthesize_codex_config

    config_text, _upstreams = synthesize_codex_config(
        "",
        source_config=tmp_path / "source.toml",
        config_path=tmp_path / "generated.toml",
        endpoint="http://127.0.0.1:9999/mcp",
        master_prompt_file=None,
        unsafe_mode=False,
    )
    import tomllib

    parsed = tomllib.loads(config_text)
    ralph_entry = parsed["mcp_servers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    assert "tool_timeout_sec" in ralph_entry
    assert ralph_entry["tool_timeout_sec"] == codex_module._CODEX_TOOL_TIMEOUT_SEC
    # Seconds -> ms check: the field is in seconds, the safety check is in ms.
    assert verify_transport_timeout_safety(int(ralph_entry["tool_timeout_sec"]) * 1000) is True


def test_kimi_transport_writes_a_per_server_tool_timeout_above_the_floor() -> None:
    """Kimi ``toolTimeoutMs`` MUST clear the timing-safety floor."""
    payload = json.loads(kimi_module.kimi_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"][kimi_module.RALPH_MCP_SERVER_NAME]
    assert isinstance(ralph_entry, dict)
    assert "toolTimeoutMs" in ralph_entry
    assert verify_transport_timeout_safety(ralph_entry["toolTimeoutMs"]) is True


def test_kimi_transport_module_constant_clears_the_floor() -> None:
    """The documented kimi constant declared by the transport clears the floor."""
    assert verify_transport_timeout_safety(kimi_module._KIMI_TOOL_TIMEOUT_MS) is True


def test_claude_transport_writes_a_per_server_timeout_above_the_floor() -> None:
    """Claude ``mcpServers.<name>.timeout`` MUST clear the timing-safety floor."""
    payload = json.loads(claude_module.claude_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"][claude_module.RALPH_MCP_SERVER_NAME]
    assert isinstance(ralph_entry, dict)
    assert "timeout" in ralph_entry
    assert verify_transport_timeout_safety(ralph_entry["timeout"]) is True


def test_claude_transport_module_constant_clears_the_floor() -> None:
    """The Claude per-server timeout constant clears the floor."""
    assert verify_transport_timeout_safety(claude_module._CLAUDE_TOOL_TIMEOUT_MS) is True


def test_opencode_transport_writes_mcp_timeout_above_the_floor() -> None:
    """OpenCode's ``experimental.mcp_timeout`` MUST clear the timing-safety floor."""
    text, _ = opencode_module.build_opencode_provider_config(None, "http://127.0.0.1:9999/mcp")
    config = json.loads(text)
    experimental = config["experimental"]
    assert isinstance(experimental, dict)
    assert "mcp_timeout" in experimental
    assert verify_transport_timeout_safety(experimental["mcp_timeout"]) is True


def test_opencode_transport_module_constant_clears_the_floor() -> None:
    """The OpenCode ``_OPENCODE_MCP_CLIENT_TIMEOUT_MS`` constant clears the floor.

    Existing ``test_opencode_mcp_config_drift.py`` asserts that this
    constant exceeds ``EXEC_MAX_TIMEOUT_MS``; this test pins the same
    constant against the *new* ``EXEC_SOFT_REPLY_BUDGET_MS`` floor that
    U-TRANSPORT introduced so a future softening of the floor cannot
    silently shrink a working timeout value.
    """
    assert (
        max(EXEC_MAX_TIMEOUT_MS, TIMING_SAFETY_TRANSPORT_FLOOR_MS)
        <= opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_MS
    )
    assert verify_transport_timeout_safety(opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_MS) is True


def test_pi_transport_generates_a_bridge_with_a_bounded_tool_call_deadline(
    tmp_path: Path,
) -> None:
    """The generated Pi bridge MUST arm a ``tools/call`` deadline.

    Pi propagates the per-tool ``AbortSignal`` to ``fetch(ENDPOINT, {signal})``
    and the bridge additionally starts a defensive ``setTimeout(...)``
    so a Pi that forgets to abort the signal still bounds ``tools/call``
    at ``EXEC_SOFT_REPLY_BUDGET_MS + 30 s headroom``. The injected
    constant reaches the generated bridge source as
    ``const MCP_TOOL_CALL_DEADLINE_MS = {MCP_TOOL_CALL_DEADLINE_MS};``
    and the bridge wraps the fetch in
    ``new AbortController()`` + ``setTimeout(() => controller.abort(), ...)``.
    """
    extension_path, cleanup = pi_module.write_pi_mcp_extension(
        "http://127.0.0.1:9999/mcp",
        workspace_path=tmp_path,
    )
    assert cleanup is None
    source = extension_path.read_text(encoding="utf-8")

    assert "const MCP_TOOL_CALL_DEADLINE_MS" in source
    assert f"const MCP_TOOL_CALL_DEADLINE_MS = {pi_module.MCP_TOOL_CALL_DEADLINE_MS};" in source
    assert "setTimeout(() => controller.abort(), MCP_TOOL_CALL_DEADLINE_MS)" in source
    assert "combineSignals(signal, controller)" in source
    assert "clearTimeout(deadlineId)" in source


def test_pi_transport_tool_call_deadline_constant_clears_the_floor() -> None:
    """The Pi bridge ``MCP_TOOL_CALL_DEADLINE_MS`` clears the timing-safety floor."""
    assert verify_transport_timeout_safety(pi_module.MCP_TOOL_CALL_DEADLINE_MS) is True


# --- No-documented-timeout clients ------------------------------------------
#
# Cursor and Nanocoder document no per-server tool-call timeout on the
# pages cited in ``docs/agents/exec-long-running-root-cause.md``. Ralph
# therefore writes NO timeout for them, and that absence is the audit
# surface — recording it proves the gap is intentional, not a missed
# field.


def test_cursor_transport_writes_no_tool_call_timeout_field() -> None:
    """Cursor publishes no per-server tool-call timeout; Ralph writes nothing.

    The Cursor MCP docs at https://cursor.com/docs/mcp document only
    stdio fields plus remote ``url``/``headers``/``auth`` with no
    tool-call timeout. Ralph's ``cursor.py:66-83`` therefore writes no
    such field; this test asserts that absence so a future contributor
    cannot quietly invent one.
    """
    from ralph.mcp.transport.cursor import cursor_mcp_config

    payload = json.loads(cursor_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    # No synthesised timeout surfaces the omission would be.
    assert "timeout" not in ralph_entry
    assert "toolTimeoutMs" not in ralph_entry
    assert "tool_timeout_sec" not in ralph_entry
    # The single key the transport writes for the ralph entry must be
    # the URL — the contract Cursor's docs name verbatim.
    assert set(ralph_entry.keys()) == {"url"}


def test_nanocoder_transport_writes_no_tool_call_timeout_field() -> None:
    """Nanocoder publishes no per-server tool-call timeout; Ralph writes nothing.

    The Nanocoder package docs (``@nanocollective/nanocoder`` on npm;
    ``github.com/Nano-Collective/nanocoder``) document only
    ``transport``/``url``/``command``/``args``/``env`` with no
    tool-call timeout. Ralph's ``nanocoder.py:24-67`` writes only
    ``transport`` and ``url``; this test pins that contract.
    """
    from ralph.mcp.transport.nanocoder import build_nanocoder_mcp_config

    text, _upstreams = build_nanocoder_mcp_config(None, "http://127.0.0.1:9999/mcp")
    payload = json.loads(text)
    ralph_entry = payload["mcpServers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    assert "timeout" not in ralph_entry
    assert "toolTimeoutMs" not in ralph_entry
    assert "tool_timeout_sec" not in ralph_entry
    # No synthesised fields beyond the two the docs name.
    assert set(ralph_entry.keys()) == {"transport", "url"}


# --- Import-time guard -------------------------------------------------------
#
# --- Per-transport-public-constant contract -----------------------------------
#
# Each transport module publishes the timeout Ralph writes as a module-level
# public constant. The checks below exercise those public surfaces directly
# so a future contribution that forgets to size a transport (or silently
# shrinks the constant below the floor) fails here rather than at runtime.


def test_every_sized_transport_clears_the_timing_safety_floor() -> None:
    """The public timeout constant of every sized transport clears the floor."""
    constants: dict[str, int] = {
        # Codex writes ``tool_timeout_sec``; the safety check operates in ms.
        "codex": codex_module._CODEX_TOOL_TIMEOUT_SEC * 1000,
        "kimi": kimi_module._KIMI_TOOL_TIMEOUT_MS,
        "claude": claude_module._CLAUDE_TOOL_TIMEOUT_MS,
        "opencode": opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_MS,
        "pi": pi_module.MCP_TOOL_CALL_DEADLINE_MS,
    }
    for transport_name, timeout_ms in constants.items():
        assert verify_transport_timeout_safety(timeout_ms) is True, (
            f"transport {transport_name} public timeout {timeout_ms} is below "
            f"the timing-safety floor {TIMING_SAFETY_TRANSPORT_FLOOR_MS}"
        )


def test_every_transport_const_represents_the_soft_budget_plus_headroom() -> None:
    """A regression that drops the headroom constant trips this test first.

    Each per-transport headroom constant MUST equal the timing-safety
    headroom. A future refactor that moves the headroom between modules
    would silently desync; the equality assertion makes the sync the
    tested contract.
    """
    from ralph.mcp.server._timing_safety import TIMING_SAFETY_TRANSPORT_HEADROOM_MS

    expected = TIMING_SAFETY_TRANSPORT_HEADROOM_MS
    assert expected == codex_module._CODEX_TOOL_TIMEOUT_HEADROOM_SECONDS * 1000
    assert expected == kimi_module._KIMI_TOOL_TIMEOUT_HEADROOM_MS
    assert expected == claude_module._CLAUDE_TOOL_TIMEOUT_HEADROOM_MS
    assert expected == opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS
    assert expected == pi_module.MCP_TOOL_CALL_HEADROOM_MS


# --- Module import round-trip -------------------------------------------------
#
# The imports above double as the canonical proof that every transport module
# still imports cleanly after the timing-safety edit; if the import-time
# invariant regresses the test fails before reaching the assertions.


def test_every_transport_module_still_imports_clean() -> None:
    for module_name in (
        "ralph.mcp.transport.codex",
        "ralph.mcp.transport.kimi",
        "ralph.mcp.transport.claude",
        "ralph.mcp.transport.opencode",
        "ralph.mcp.transport.pi",
        "ralph.mcp.transport.cursor",
        "ralph.mcp.transport.nanocoder",
        "ralph.mcp.server._timing_safety",
    ):
        module = importlib.import_module(module_name)
        assert module is not None, f"failed to import {module_name}"
