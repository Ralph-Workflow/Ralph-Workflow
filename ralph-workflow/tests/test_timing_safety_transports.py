from __future__ import annotations

import concurrent.futures
import importlib
import json
import subprocess
import sys
import textwrap
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
    from ralph.mcp.server._timing_safety import TIMING_SAFETY_TRANSPORT_HEADROOM_MS

    assert TIMING_SAFETY_TRANSPORT_HEADROOM_MS == 30_000
    assert TIMING_SAFETY_TRANSPORT_FLOOR_MS == (
        EXEC_SOFT_REPLY_BUDGET_MS + TIMING_SAFETY_TRANSPORT_HEADROOM_MS
    )


def test_verify_transport_timeout_safety_accepts_floor_value() -> None:
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS) is True


def test_verify_transport_timeout_safety_accepts_higher_value() -> None:
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS + 60_000) is True


def test_verify_transport_timeout_safety_rejects_below_floor() -> None:
    assert verify_transport_timeout_safety(TIMING_SAFETY_TRANSPORT_FLOOR_MS - 1) is False


def test_verify_transport_timeout_safety_rejects_none_for_unconfigured_clients() -> None:
    assert verify_transport_timeout_safety(None) is False


def test_codex_transport_writes_a_per_server_tool_timeout_above_the_floor() -> None:
    assert verify_transport_timeout_safety(codex_module._CODEX_TOOL_TIMEOUT_SEC * 1000) is True


def test_codex_transport_writes_tool_timeout_in_its_merged_mcp_servers_entry(
    tmp_path: Path,
) -> None:
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
    assert verify_transport_timeout_safety(int(ralph_entry["tool_timeout_sec"]) * 1000) is True


def test_kimi_transport_writes_a_per_server_tool_timeout_above_the_floor() -> None:
    payload = json.loads(kimi_module.kimi_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"][kimi_module.RALPH_MCP_SERVER_NAME]
    assert isinstance(ralph_entry, dict)
    assert "toolTimeoutMs" in ralph_entry
    assert verify_transport_timeout_safety(ralph_entry["toolTimeoutMs"]) is True


def test_kimi_transport_module_constant_clears_the_floor() -> None:
    assert verify_transport_timeout_safety(kimi_module._KIMI_TOOL_TIMEOUT_MS) is True


def test_claude_transport_writes_a_per_server_timeout_above_the_floor() -> None:
    payload = json.loads(claude_module.claude_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"][claude_module.RALPH_MCP_SERVER_NAME]
    assert isinstance(ralph_entry, dict)
    assert "timeout" in ralph_entry
    assert verify_transport_timeout_safety(ralph_entry["timeout"]) is True


def test_claude_transport_module_constant_clears_the_floor() -> None:
    assert verify_transport_timeout_safety(claude_module._CLAUDE_TOOL_TIMEOUT_MS) is True


def test_opencode_transport_writes_mcp_timeout_above_the_floor() -> None:
    text, _ = opencode_module.build_opencode_provider_config(None, "http://127.0.0.1:9999/mcp")
    config = json.loads(text)
    experimental = config["experimental"]
    assert isinstance(experimental, dict)
    assert "mcp_timeout" in experimental
    assert verify_transport_timeout_safety(experimental["mcp_timeout"]) is True


def test_opencode_transport_module_constant_clears_the_floor() -> None:
    assert (
        max(EXEC_MAX_TIMEOUT_MS, TIMING_SAFETY_TRANSPORT_FLOOR_MS)
        <= opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_MS
    )
    assert verify_transport_timeout_safety(opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_MS) is True


def test_pi_transport_generates_a_bridge_with_a_bounded_tool_call_deadline(
    tmp_path: Path,
) -> None:
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
    assert verify_transport_timeout_safety(pi_module.MCP_TOOL_CALL_DEADLINE_MS) is True


def test_cursor_transport_writes_no_tool_call_timeout_field() -> None:
    from ralph.mcp.transport.cursor import cursor_mcp_config

    payload = json.loads(cursor_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    assert "timeout" not in ralph_entry
    assert "toolTimeoutMs" not in ralph_entry
    assert "tool_timeout_sec" not in ralph_entry
    assert set(ralph_entry.keys()) == {"url"}


def test_nanocoder_transport_writes_no_tool_call_timeout_field() -> None:
    from ralph.mcp.transport.nanocoder import build_nanocoder_mcp_config

    text, _upstreams = build_nanocoder_mcp_config(None, "http://127.0.0.1:9999/mcp")
    payload = json.loads(text)
    ralph_entry = payload["mcpServers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    assert "timeout" not in ralph_entry
    assert "toolTimeoutMs" not in ralph_entry
    assert "tool_timeout_sec" not in ralph_entry
    assert set(ralph_entry.keys()) == {"transport", "url"}


def test_antigravity_transport_writes_no_tool_call_timeout_field() -> None:
    from ralph.mcp.transport.agy import agy_mcp_config

    payload = json.loads(agy_mcp_config("http://127.0.0.1:9999/mcp"))
    ralph_entry = payload["mcpServers"]["ralph"]
    assert isinstance(ralph_entry, dict)
    assert "timeout" not in ralph_entry
    assert "toolTimeoutMs" not in ralph_entry
    assert "tool_timeout_sec" not in ralph_entry
    assert set(ralph_entry.keys()) == {"serverUrl"}


_TIMING_SAFETY_RELATIVE_PATH = (
    Path(__file__).parent.parent / "ralph" / "mcp" / "server" / "_timing_safety.py"
)


@pytest.fixture(scope="module")
def _timing_safety_subprocess_runs() -> dict[bool, subprocess.CompletedProcess[str]]:
    original = _TIMING_SAFETY_RELATIVE_PATH.read_text(encoding="utf-8")
    anchor = "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000"
    if anchor not in original:
        raise AssertionError(
            "timing-safety headroom anchor missing; _timing_safety.py was reformatted."
        )

    script = textwrap.dedent(
        """\
        import importlib
        import importlib.util
        import io
        import sys
        import tempfile
        import traceback
        from contextlib import redirect_stderr
        from pathlib import Path

        # --- Part 1: Headroom invariant check ---
        path = Path(__PATH__)
        marker = "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = -1"
        patched = path.read_text(encoding="utf-8").replace(
            "TIMING_SAFETY_TRANSPORT_HEADROOM_MS: int = 30_000",
            marker,
            1,
        )
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        try:
            tmp.write(patched)
            tmp.flush()
            tmp_path = tmp.name
        finally:
            tmp.close()

        err = io.StringIO()
        try:
            with redirect_stderr(err):
                spec = importlib.util.spec_from_file_location("timing_safety_case", tmp_path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["timing_safety_case"] = mod
                spec.loader.exec_module(mod)
            headroom_ok = True
            headroom_stderr = err.getvalue()
        except Exception:
            headroom_ok = False
            headroom_stderr = err.getvalue() + traceback.format_exc()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        print("HEADROOM_OK:" + str(headroom_ok))
        print("HEADROOM_STDERR:" + headroom_stderr)

        # --- Part 2: Concrete transport timeouts below floor ---
        transports = [
            ("ralph.mcp.transport.codex", "_CODEX_TOOL_TIMEOUT_SEC", 10, "codex"),
            ("ralph.mcp.transport.kimi", "_KIMI_TOOL_TIMEOUT_MS", 10000, "kimi"),
            ("ralph.mcp.transport.claude", "_CLAUDE_TOOL_TIMEOUT_MS", 10000, "claude"),
            ("ralph.mcp.transport.opencode", "_OPENCODE_MCP_CLIENT_TIMEOUT_MS", 10000, "opencode"),
            ("ralph.mcp.transport.pi", "MCP_TOOL_CALL_DEADLINE_MS", 10000, "pi"),
        ]

        for mod_name, attr, bad_val, name in transports:
            mod = importlib.import_module(mod_name)
            orig = getattr(mod, attr)
            setattr(mod, attr, bad_val)
            sys.modules.pop("ralph.mcp.server._timing_safety", None)
            try:
                importlib.import_module("ralph.mcp.server._timing_safety")
                print(f"FAILED:{name}")
                sys.exit(1)
            except RuntimeError as exc:
                if f"transport {name} timeout" not in str(exc):
                    print(f"UNEXPECTED_ERROR:{name}:{exc}")
                    sys.exit(2)
            finally:
                setattr(mod, attr, orig)
                sys.modules.pop("ralph.mcp.server._timing_safety", None)

        print("ALL_TRANSPORTS_CHECKED")
        """
    ).replace("__PATH__", repr(str(_TIMING_SAFETY_RELATIVE_PATH)))

    def _run_subproc(minus_o: bool) -> tuple[bool, subprocess.CompletedProcess[str]]:
        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", script])
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return minus_o, completed

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        return dict(executor.map(_run_subproc, (False, True)))


def test_timing_safety_import_time_invariant_survives_python_minus_o(
    _timing_safety_subprocess_runs: dict[bool, subprocess.CompletedProcess[str]],
) -> None:
    for minus_o, completed in _timing_safety_subprocess_runs.items():
        assert "HEADROOM_OK:False" in completed.stdout, (
            f"expected RuntimeError on import (minus_o={minus_o}); "
            f"got stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        assert "RuntimeError" in completed.stdout, (
            f"expected RuntimeError text (minus_o={minus_o}); got stdout={completed.stdout!r}"
        )
        assert "TIMING_SAFETY_TRANSPORT_HEADROOM_MS" in completed.stdout, (
            f"expected the invariant name in stderr (minus_o={minus_o}); "
            f"got stdout={completed.stdout!r}"
        )


def test_transport_timeout_import_time_invariant_survives_python_minus_o(
    _timing_safety_subprocess_runs: dict[bool, subprocess.CompletedProcess[str]],
) -> None:
    for minus_o, completed in _timing_safety_subprocess_runs.items():
        assert completed.returncode == 0, (
            f"expected exit code 0 (minus_o={minus_o}); "
            f"got returncode={completed.returncode} stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        assert "ALL_TRANSPORTS_CHECKED" in completed.stdout


def test_every_sized_transport_clears_the_timing_safety_floor() -> None:
    constants: dict[str, int] = {
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
    from ralph.mcp.server._timing_safety import TIMING_SAFETY_TRANSPORT_HEADROOM_MS

    expected = TIMING_SAFETY_TRANSPORT_HEADROOM_MS
    assert expected == codex_module._CODEX_TOOL_TIMEOUT_HEADROOM_SECONDS * 1000
    assert expected == kimi_module._KIMI_TOOL_TIMEOUT_HEADROOM_MS
    assert expected == claude_module._CLAUDE_TOOL_TIMEOUT_HEADROOM_MS
    assert expected == opencode_module._OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS
    assert expected == pi_module.MCP_TOOL_CALL_HEADROOM_MS


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
