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
    _default_roots,
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


# --- explicit timeout=None loophole -----------------------------------------
#
# Regression tests for the bounded-timeout policy: an explicit ``timeout=None``
# keyword (or an explicit ``None`` positional to ``.wait()``) is treated as
# UNBOUNDED because the underlying CPython call honors the documented "no
# timeout" semantics when ``timeout is None``. A future refactor that restores
# keyword-presence-only checking would silently re-open this loophole and let
# explicitly-unbounded calls pass ``make verify``. The samples here are the
# canonical regressions called out by the development-analysis feedback.


def test_subprocess_run_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import subprocess\nsubprocess.run(['x'], timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "subprocess_run"


def test_subprocess_check_output_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import subprocess\nsubprocess.check_output(['x'], timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "subprocess_run"


def test_subprocess_call_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import subprocess\nsubprocess.call(['x'], timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "subprocess_run"


def test_aliased_subprocess_run_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import subprocess as sp\nsp.run(['x'], timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "subprocess_run"


def test_wait_with_timeout_none_keyword_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.wait(timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_wait_with_none_positional_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.wait(None)\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_communicate_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.communicate(timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_communicate_and_cleanup_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.communicate_and_cleanup(timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_httpx_get_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx\nhttpx.get('http://x', timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "network"


def test_httpx_post_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx\nhttpx.post('http://x', timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "network"


def test_aliased_httpx_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx as hx\nhx.get('http://x', timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "network"


def test_requests_session_get_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import requests\nrequests.get('http://x', timeout=None)\n")
    assert len(v) == 1
    assert v[0].category == "network"


def test_marker_suppresses_timeout_none(tmp_path: Path) -> None:
    # An explicit ``# mcp-timeout-ok`` marker still wins, even when the
    # ``timeout=None`` keyword is present (escape hatch for genuinely unbounded
    # callers, e.g. ``socket.create_connection`` in some startup paths).
    src = (
        "proc.wait(timeout=None)  # mcp-timeout-ok: bounded by outer "
        "watchdog with explicit kill switch\n"
    )
    assert not _audit(tmp_path, src)


def test_variable_timeout_is_out_of_scope(tmp_path: Path) -> None:
    # A variable that COULD be None at runtime is out of scope (dataflow
    # tracking would be required to prove the variable resolves to ``None``).
    # The audit accepts this as bounded by default.
    assert not _audit(tmp_path, "timeout = compute_timeout()\nproc.wait(timeout=timeout)\n")


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


# --- pro_support regression coverage -----------------------------------------


def test_audit_flags_unbounded_httpx_in_pro_support(tmp_path: Path) -> None:
    """An unbounded ``httpx.post`` under ralph/pro_support must be flagged.

    Mirrors the production layout (``ralph/pro_support/`` is one of the
    default audit roots) and proves the new root is wired correctly:
    a regression that would let an unbounded heartbeat escape the audit
    is caught by this test.
    """
    pro_dir = tmp_path / "pro_support"
    pro_dir.mkdir()
    (pro_dir / "heartbeat.py").write_text(
        "import httpx\nhttpx.post('http://localhost/api/heartbeat', json={})\n",
        encoding="utf-8",
    )
    violations, _checked = audit_mcp_directory(pro_dir)
    network_violations = [v for v in violations if v.category == "network"]
    assert network_violations, "expected at least one network violation in pro_support/heartbeat.py"


def test_default_roots_includes_pro_support() -> None:
    """``_default_roots()`` MUST include the pro_support package.

    Regression test for a future refactor that drops the pro_support
    entry: that would silently disable audit coverage of the Pro
    heartbeat client and let an unbounded httpx call escape.
    """
    roots = _default_roots()
    assert any(str(r).endswith("ralph/pro_support") for r in roots), (
        f"_default_roots() must include ralph/pro_support; got {roots}"
    )


def test_default_roots_cover_executor_agents_process() -> None:
    """``_default_roots()`` MUST cover ralph/executor, ralph/agents, and ralph/process.

    Step-5 regression: the bounded-subprocess audit was extended to cover
    the executor/agents/process trees so an unbounded blocking call in any
    of them fails ``make verify``. A future refactor that drops any of
    these entries would silently re-open the unbounded-call loophole this
    audit was created to close.

    Mirrors the style of ``test_default_roots_includes_pro_support``.
    """
    roots = _default_roots()
    assert any(str(r).endswith("ralph/executor") for r in roots), (
        f"_default_roots() must include ralph/executor; got {roots}"
    )
    assert any(str(r).endswith("ralph/agents") for r in roots), (
        f"_default_roots() must include ralph/agents; got {roots}"
    )
    assert any(str(r).endswith("ralph/process") for r in roots), (
        f"_default_roots() must include ralph/process (broadened from "
        f"ralph/process/manager); got {roots}"
    )
