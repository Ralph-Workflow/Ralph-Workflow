"""Tests for ralph.testing.audit_mcp_timeout.

The MCP timeout contract: no operation under ``ralph/mcp/`` may perform blocking
I/O without a bounded, fail-closed timeout. This audit (AST-based, mirroring
``audit_test_policy``) enforces it in ``make verify``. These tests pin the
detection rules and the inline-allowlist escape hatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_mcp_timeout import (
    McpTimeoutViolation,
    audit_mcp_directory,
    audit_mcp_file,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


def _audit(tmp_path: Path, src: str) -> list[McpTimeoutViolation]:
    f = tmp_path / "mod.py"
    f.write_text(src, encoding="utf-8")
    return audit_mcp_file(f)


# --- subprocess.run ---------------------------------------------------------


def test_subprocess_run_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import subprocess\nsubprocess.run(['git', 'status'])\n")


def test_subprocess_run_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess\nsubprocess.run(['x'], timeout=5)\n")


# --- .communicate() ---------------------------------------------------------


def test_communicate_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "stdout, stderr = proc.communicate()\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_communicate_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.communicate(timeout=30)\n")


def test_communicate_positional_input_is_still_flagged(tmp_path: Path) -> None:
    # First positional to communicate() is ``input``, NOT a timeout.
    assert _audit(tmp_path, "proc.communicate(b'data')\n")


# --- .communicate_and_cleanup() (the real exec call) ------------------------


def test_communicate_and_cleanup_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.communicate_and_cleanup()\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_communicate_and_cleanup_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.communicate_and_cleanup(timeout=30)\n")


# --- aliased imports must not evade detection -------------------------------


def test_aliased_subprocess_module_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import subprocess as sp\nsp.run(['x'])\n")


def test_from_import_subprocess_run_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "from subprocess import run\nrun(['x'])\n")


def test_from_import_subprocess_run_aliased_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "from subprocess import run as r\nr(['x'])\n")


def test_aliased_httpx_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import httpx as hx\nhx.get('http://x')\n")


def test_aliased_subprocess_run_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess as sp\nsp.run(['x'], timeout=5)\n")


# --- additional unbounded subprocess primitives -----------------------------


def test_os_system_is_flagged(tmp_path: Path) -> None:
    # os.system takes no timeout — always unbounded.
    assert _audit(tmp_path, "import os\nos.system('sleep 9')\n")


def test_subprocess_check_output_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import subprocess\nsubprocess.check_output(['x'])\n")


def test_subprocess_check_output_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess\nsubprocess.check_output(['x'], timeout=5)\n")


# --- .wait() ----------------------------------------------------------------


def test_wait_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.wait()\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_wait_with_keyword_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.wait(timeout=5)\n")


def test_wait_with_positional_timeout_is_allowed(tmp_path: Path) -> None:
    # wait()'s first positional IS the timeout.
    assert not _audit(tmp_path, "proc.wait(5)\n")


# --- blocking stdout/stderr iteration ---------------------------------------


def test_blocking_for_in_stdout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "for line in proc.stdout:\n    pass\n")
    assert len(v) == 1
    assert v[0].category == "blocking_stream_iter"


def test_blocking_for_in_stderr_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "for line in proc.stderr:\n    pass\n")


def test_iterating_tuple_of_streams_is_not_flagged(tmp_path: Path) -> None:
    # Iterating a tuple of pipe objects (e.g. to close them) is not a blocking read.
    assert not _audit(tmp_path, "for s in (proc.stdout, proc.stderr):\n    s.close()\n")


def test_iterating_splitlines_is_not_flagged(tmp_path: Path) -> None:
    # .splitlines() yields from an in-memory string, not a live stream.
    assert not _audit(tmp_path, "for line in result.stdout.splitlines():\n    pass\n")


def test_blocking_stream_iter_marker_suppresses(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "for line in proc.stdout:  # mcp-timeout-ok: x\n    pass\n")


# --- network ----------------------------------------------------------------


def test_httpx_get_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import httpx\nhttpx.get('http://x')\n")


def test_httpx_get_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import httpx\nhttpx.get('http://x', timeout=5)\n")


def test_urlopen_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import urllib.request\nurllib.request.urlopen(req)\n")


def test_socket_create_connection_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import socket\nsocket.create_connection(addr)\n")


# --- must NOT flag (Python semantics) --------------------------------------


def test_popen_construction_is_not_flagged(tmp_path: Path) -> None:
    # Popen takes no timeout= kwarg; the timeout lives on communicate()/wait().
    assert not _audit(tmp_path, "import subprocess\nsubprocess.Popen(['x'])\n")


def test_socket_socket_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import socket\nsocket.socket(socket.AF_INET)\n")


# --- inline allowlist marker ------------------------------------------------


def test_inline_marker_suppresses_violation(tmp_path: Path) -> None:
    src = "proc.communicate()  # mcp-timeout-ok: managed-process implements the timeout\n"
    assert not _audit(tmp_path, src)


# --- directory scan + main --------------------------------------------------


def test_audit_directory_aggregates(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("proc.communicate()\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("proc.wait(timeout=1)\n", encoding="utf-8")
    violations, checked = audit_mcp_directory(tmp_path)
    assert checked == 2
    assert len(violations) == 1


def test_main_returns_nonzero_on_violation(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text("proc.communicate()\n", encoding="utf-8")
    assert main([str(tmp_path)]) == 1


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text("proc.communicate(timeout=5)\n", encoding="utf-8")
    assert main([str(tmp_path)]) == 0
