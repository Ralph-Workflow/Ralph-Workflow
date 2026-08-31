"""Recovery and cross-process safety for the user-global MCP config overlay.

Ralph OVERWRITES config files it does not own so an agent CLI picks up the
run-scoped ``ralph`` MCP entry: ``~/.cursor/mcp.json`` (cursor),
``$KIMI_CODE_HOME/mcp.json`` (kimi) and the two
``~/.gemini/**/mcp_config.json`` paths (agy). In restricted mode the
published payload is Ralph-ONLY, so while the run is live the operator's
own MCP servers are absent from their own user-global file.

The bug these tests pin: the restore was a plain ``finally``. A SIGKILL,
an OOM kill, a reboot or a power cut skips it, and the operator is left
with a config file containing one Ralph entry that points at a dead
localhost port -- permanently, because the NEXT run would then snapshot
that corpse as "the original" and faithfully restore it forever.

Simulating the abnormal exit: no process is killed. The killed-run
on-disk state (overlay published, restore never executed) is built by
running the real overlay context manager with its ``restore_config_overlay``
seam patched to a no-op, which is exactly what the operating system does
to a process it kills between the write and the restore.

Regression tests follow ``<area>_regression_<bug_description>`` per
docs/ralph-workflow-policy/testing-policy.md.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.transport.agy import agy_workspace_mcp_endpoint
from ralph.mcp.transport.config_overlay import (
    McpConfigOverlayLockTimeoutError,
    mcp_config_lock_path,
    mcp_config_overlay_lock,
)
from ralph.mcp.transport.cursor import cursor_workspace_mcp_endpoint
from ralph.mcp.transport.kimi import kimi_workspace_mcp_endpoint

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_ENDPOINT = "http://127.0.0.1:56349/mcp"
_OPERATOR_CONFIG = json.dumps(
    {
        "mcpServers": {
            "operator-notes": {"url": "http://127.0.0.1:7777/mcp"},
            "operator-db": {"url": "http://127.0.0.1:7778/mcp"},
        }
    },
    indent=2,
)


def _write(path: Path, text: str) -> None:
    """Create ``path``'s parents and write ``text`` (test fixture setup)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@contextmanager
def _killed_run(monkeypatch: pytest.MonkeyPatch, transport_module: str) -> Iterator[None]:
    """Run the overlay inside this block with its restore step neutered.

    The overlay is still published to disk and the advisory lock is still
    released when the block ends (a killed process releases its ``flock``
    the same way), so what remains on disk afterwards is exactly the state
    a killed run leaves behind: overlay published, restore never executed.
    """
    with monkeypatch.context() as killed:
        killed.setattr(
            f"ralph.mcp.transport.{transport_module}.restore_config_overlay",
            lambda config_path, original_bytes: None,
        )
        yield


def _server_names(path: Path) -> list[str]:
    """Return the ``mcpServers`` keys currently published at ``path``."""
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    servers = parsed["mcpServers"]
    assert isinstance(servers, dict)
    return sorted(str(name) for name in servers)


@pytest.fixture
def cursor_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Cursor's user-global MCP config into ``tmp_path``."""
    config_path = tmp_path / "home" / ".cursor" / "mcp.json"
    monkeypatch.setattr(
        "ralph.mcp.transport.cursor._cursor_global_config_path", lambda: config_path
    )
    return config_path


@pytest.fixture
def kimi_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Kimi Code's user-global MCP config into ``tmp_path``."""
    config_path = tmp_path / "home" / ".kimi-code" / "mcp.json"
    monkeypatch.setattr("ralph.mcp.transport.kimi._kimi_global_config_path", lambda: config_path)
    return config_path


@pytest.fixture
def agy_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect both AGY global MCP config paths into ``tmp_path``."""
    config_path = tmp_path / "home" / ".gemini" / "antigravity-cli" / "mcp_config.json"
    secondary_path = tmp_path / "home" / ".gemini" / "config" / "mcp_config.json"
    monkeypatch.setattr("ralph.mcp.transport.agy._agy_global_config_path", lambda: config_path)
    monkeypatch.setattr(
        "ralph.mcp.transport.agy._agy_secondary_config_path", lambda: secondary_path
    )
    return config_path


def test_cursor_transport_regression_killed_run_restores_operator_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor_global_config: Path
) -> None:
    """A killed Cursor run self-heals ``~/.cursor/mcp.json`` on the next run.

    The restore was a bare ``finally``, so an abnormal exit left the
    operator's user-global MCP config permanently replaced by a Ralph-only
    entry pointing at a dead port.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(cursor_global_config, _OPERATOR_CONFIG)

    with _killed_run(monkeypatch, "cursor"), cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(cursor_global_config) == ["ralph"]
    # What the killed run left behind: the operator's servers are gone.
    assert _server_names(cursor_global_config) == ["ralph"]

    with cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        # The reclaim runs before the snapshot, so this run overlays the
        # operator's real config -- not the corpse the killed run left.
        assert _server_names(cursor_global_config) == ["ralph"]

    assert cursor_global_config.read_text(encoding="utf-8") == _OPERATOR_CONFIG


def test_kimi_transport_regression_killed_run_restores_operator_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kimi_global_config: Path
) -> None:
    """A killed Kimi run self-heals ``$KIMI_CODE_HOME/mcp.json`` on the next run."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(kimi_global_config, _OPERATOR_CONFIG)

    with _killed_run(monkeypatch, "kimi"), kimi_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(kimi_global_config) == ["ralph"]
    assert _server_names(kimi_global_config) == ["ralph"]

    with kimi_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(kimi_global_config) == ["ralph"]

    assert kimi_global_config.read_text(encoding="utf-8") == _OPERATOR_CONFIG


def test_agy_transport_regression_killed_run_restores_operator_global_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agy_global_config: Path
) -> None:
    """A killed AGY run self-heals ``~/.gemini/**/mcp_config.json`` on the next run."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(agy_global_config, _OPERATOR_CONFIG)

    with _killed_run(monkeypatch, "agy"), agy_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(agy_global_config) == ["ralph"]
    assert _server_names(agy_global_config) == ["ralph"]

    with agy_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(agy_global_config) == ["ralph"]

    assert agy_global_config.read_text(encoding="utf-8") == _OPERATOR_CONFIG


def test_cursor_transport_regression_killed_run_removes_config_it_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor_global_config: Path
) -> None:
    """A killed run that CREATED the config leaves nothing behind after the next run.

    The recovery record has to remember "there was no file here", not only
    "here are the previous bytes".
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert not cursor_global_config.exists()

    with _killed_run(monkeypatch, "cursor"), cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        pass
    assert cursor_global_config.is_file()

    with cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        pass

    assert not cursor_global_config.exists()


def test_cursor_transport_regression_reclaim_keeps_operator_rewrite_after_killed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor_global_config: Path
) -> None:
    """A newer operator edit outranks a stale recovery record.

    Staleness is decided by content, not by age: the backup is reclaimed
    ONLY while the file on disk is still byte-identical to the overlay
    Ralph published. Once the operator has rewritten the file, the record
    is superseded and must be dropped without touching their content.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(cursor_global_config, _OPERATOR_CONFIG)

    with _killed_run(monkeypatch, "cursor"), cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        pass

    # The operator noticed the damage and rebuilt the file by hand.
    operator_rewrite = json.dumps(
        {"mcpServers": {"operator-rebuilt": {"url": "http://127.0.0.1:7779/mcp"}}}, indent=2
    )
    _write(cursor_global_config, operator_rewrite)

    with cursor_workspace_mcp_endpoint(workspace, _ENDPOINT):
        assert _server_names(cursor_global_config) == ["ralph"]

    assert cursor_global_config.read_text(encoding="utf-8") == operator_rewrite


def test_cursor_transport_regression_concurrent_overlay_holder_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cursor_global_config: Path
) -> None:
    """Cursor refuses to overlay a config another process has locked.

    ``cursor.py`` guarded the overlay with a ``threading.Lock`` only, which
    is invisible to a second Ralph PROCESS: both would snapshot, both would
    write, and whichever restored last would put back the wrong bytes. The
    advisory lock held here is the same ``flock`` primitive AGY proves
    cross-process in ``tests/test_agy_config_overlay_cross_process_e2e.py``;
    ``flock`` is per open file description, so a second handle taken here
    contends exactly as another process would.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(cursor_global_config, _OPERATOR_CONFIG)
    monkeypatch.setattr("ralph.mcp.transport.cursor._CURSOR_CONFIG_LOCK_TIMEOUT_SECONDS", 0.05)

    with (
        mcp_config_overlay_lock(mcp_config_lock_path(cursor_global_config)),
        pytest.raises(McpConfigOverlayLockTimeoutError),
        cursor_workspace_mcp_endpoint(workspace, _ENDPOINT),
    ):
        pass

    assert cursor_global_config.read_text(encoding="utf-8") == _OPERATOR_CONFIG


def test_kimi_transport_regression_concurrent_overlay_holder_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kimi_global_config: Path
) -> None:
    """Kimi refuses to overlay a config another process has locked."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write(kimi_global_config, _OPERATOR_CONFIG)
    monkeypatch.setattr("ralph.mcp.transport.kimi._KIMI_CONFIG_LOCK_TIMEOUT_SECONDS", 0.05)

    with (
        mcp_config_overlay_lock(mcp_config_lock_path(kimi_global_config)),
        pytest.raises(McpConfigOverlayLockTimeoutError),
        kimi_workspace_mcp_endpoint(workspace, _ENDPOINT),
    ):
        pass

    assert kimi_global_config.read_text(encoding="utf-8") == _OPERATOR_CONFIG
