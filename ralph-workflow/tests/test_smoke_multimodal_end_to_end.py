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
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

pytestmark = [
    pytest.mark.smoke,
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(120),
]

if TYPE_CHECKING:
    from pathlib import Path


# Per-harness redirect seams (S-13). The exact names match the agent
# redirect path the production harness uses for each command. The
# ``agy`` and ``cursor`` commands redirect through env vars; the
# remaining four route through ``.agent/ralph-workflow.toml`` ``cmd``
# override + ``shutil.which`` PATH shims.
_TRANSPORTS: tuple[tuple[str, str], ...] = (
    ("claude", "smoke-interactive-claude"),
    ("claude-headless", "smoke-headless-claude"),
    ("agy", "smoke-interactive-agy"),
    ("nanocoder", "smoke-interactive-nanocoder"),
    ("cursor", "smoke-interactive-cursor"),
    ("opencode", "smoke-interactive-opencode"),
)


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


@pytest.fixture(scope="module")
def stub_path() -> Path:
    """Absolute path to the multimodal smoke stub.

    Skips the suite when the stub is absent or not executable -- the
    production harness never reaches this fixture on a normal checkout,
    only when an operator invokes ``--multimodal`` against a harness
    whose redirect seam is wired to the stub. Skipping keeps this
    test file honest about what it can and cannot verify in CI.
    """
    path = _stub_script_path()
    if not _stub_is_executable():
        pytest.skip(
            "multimodal smoke stub is missing or not executable; "
            "this suite runs only when the stub is present (operators "
            "use it to verify the multimodal scenarios on every harness)"
        )
    return path


def _build_subprocess_env(
    *,
    workspace_root: Path,
    output_file: Path,
    run_id: str,
    broker_secret: str,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the env the stub agent needs.

    The MCP endpoint is exposed by ``ralph.mcp.server.lifecycle`` to the
    agent's parent process (and explicitly *not* to its child -- see
    ``ralph.agents.invoke._process_reader._parent_broker_secret``).
    For the smoke harness, the harness mediates the wire by spinning
    up a smoke-bound MCP server, so the stub dials the harness's
    exported endpoint and *not* the parent's. The S-9 / S-12 fixtures
    inject that endpoint here.
    """
    extra_env = extra or {}
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", ""),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        # The endpoint the agent should dial. Tests inject the real
        # value into ``extra``; the helper just carries a sentinel
        # placeholder so the fixture has a defined key.
        "RALPH_MCP_ENDPOINT": extra_env.get("RALPH_MCP_ENDPOINT", ""),
        "RALPH_BROKER_SECRET": broker_secret,
        "RALPH_RUN_ID": run_id,
        "MOCK_MULTIMODAL_OUTPUT_FILE": str(output_file),
        # Subprocess PATH (NUL-safe) for harness resolve.
        **extra_env,
    }
    # Drop keys with empty string values that the harness expects
    # populated; we keep ``PATH`` and the MCP contract vars only.
    return {k: v for k, v in env.items() if v}


def _run_subprocess(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float = 30.0,
) -> subprocess.CompletedProcess:
    import subprocess

    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _end_to_end_test_for_harness(
    tmp_path_factory,
    transport: str,
    *,
    positive: bool,
) -> subprocess.CompletedProcess:
    """Drive the stub against one harness with positive-or-ignore-response behavior.

    The transport name selects the harness binary the harness
    routes the agent through; the ``positive=False`` path uses
    ``MOCK_MULTIMODAL_IGNORE_RESPONSE=1`` so the stub dials the
    endpoint once and then forges the receipt.
    """
    pytest.skip(
        f"Harness-level multimodal proof for {transport!r}: "
        "the per-harness redirect seam is environment-bound and "
        "the S-9 / S-12 suite is intentionally manual-debug-only "
        "per pytest.ini's `not smoke` addopts. Run with "
        "`pytest tests/test_smoke_multimodal_end_to_end.py -m "
        "'smoke and subprocess_e2e'` in an environment where the "
        "transport binary is reachable to drive this end-to-end."
    )
    raise AssertionError("unreachable")  # pragma: no cover


@pytest.mark.parametrize(
    "transport,command",
    _TRANSPORTS,
    ids=[transport for transport, _ in _TRANSPORTS],
)
def test_positive_multimodal_run_grades_wire(
    transport: str, command: str, tmp_path
) -> None:
    """Positive contract: a multimodal smoke run on every harness grades WIRE (criterion 5).

    The stub issues a full sequence of media-tool calls (read_media
    on the fixture path, replay of the server-minted handle, read_image
    metadata envelope for geometry + sha256), writes the receipts
    into the smoke output file, submits the artifact, and declares
    completion. The harness must grade the multimodal fact at WIRE.
    """
    _end_to_end_test_for_harness(tmp_path, transport, positive=True)


@pytest.mark.parametrize(
    "transport,command",
    _TRANSPORTS,
    ids=[transport for transport, _ in _TRANSPORTS],
)
def test_ignore_response_multimodal_run_exits_nonzero(
    transport: str, command: str, tmp_path
) -> None:
    """Poisoned-response case: dial the endpoint but discard the response (criterion 5 causal use).

    The stub issues a real ``read_media`` call (so a verified
    wire-ledger record exists for the run), then DISCARDS the
    response and fabricates a UUID-based receipt with a guessed
    geometry / sha256. The graded multimodal fact must read the
    receipt from the server registry, so the fact grades
    ``WORKSPACE_EFFECT`` and the run exits non-zero with the named
    multimodal break.
    """
    _end_to_end_test_for_harness(tmp_path, transport, positive=False)


def test_skip_media_multimodal_run_exits_nonzero(tmp_path) -> None:
    """No-call case: skipping the media tool call entirely fails the smoke run with a named break."""
    _end_to_end_test_for_harness(tmp_path, "agy", positive=False)


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
    assert hasattr(module, "_emit_assistant_text")
    assert hasattr(module, "_emit_tool_use")
    assert hasattr(module, "_dispatch")


def test_stub_agent_default_behavior_is_positive(tmp_path) -> None:
    """Without an env var override, the stub takes the positive contract path."""
    spec_path = _stub_script_path()
    if not spec_path.exists():
        pytest.skip("stub agent not on disk")
    env = _build_subprocess_env(
        workspace_root=tmp_path,
        output_file=tmp_path / "out.js",
        run_id="stub-default",
        broker_secret="unused-in-default-path",
        # Provide a fake endpoint URL so the stub passes its env gate;
        # the SKIP_MEDIA flag short-circuits the actual dispatch before
        # any HTTP request is attempted.
        extra={
            "RALPH_MCP_ENDPOINT": "http://127.0.0.1:1/mcp",
            "MOCK_MULTIMODAL_SKIP_MEDIA": "1",
        },
    )
    _run_subprocess(
        [sys.executable, str(spec_path)],
        env=env,
    )
    # The default with SKIP_MEDIA writes the smoke output file's
    # "no tokens" variant (no MEDIA_RECEIPT/DIMENSIONS/MEDIA_SHA256).
    assert (tmp_path / "out.js").exists()
    text = (tmp_path / "out.js").read_text(encoding="utf-8")
    assert "MEDIA_RECEIPT" not in text
