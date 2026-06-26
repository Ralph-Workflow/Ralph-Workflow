"""Tests for ralph.testing.audit_resource_lifecycle.

The resource-lifecycle contract (enforced by ``make verify``): production
code must not spawn:

  (1) ``threading.Thread(...)`` / ``Thread(...)`` calls WITHOUT
      ``daemon=True`` (non-daemon threads can block process exit);
  (2) ``httpx.Client(...)``, ``httpx.AsyncClient(...)``,
      ``requests.Session(...)`` constructed OUTSIDE a ``with``
      statement (bare assignment leaks the underlying HTTP connection
      pool and may not be closed at interpreter exit);
  (3) raw ``os.open(...)``, ``os.openpty(...)``, ``os.pipe(...)`` calls
      OUTSIDE ``ralph/process/`` (these bypass the centralized
      process lifecycle / fd ownership policy).

An inline ``# resource-lifecycle-ok: <reason>`` marker suppresses a
flag for a single call site (the only allowlist mechanism). The audit
is AST-based, so it can only flag literal-name calls; alias resolution
works so ``import threading as th`` and ``from httpx import Client``
cannot evade detection.

These tests pin the detection rules, the inline allowlist escape
hatch, and assert the production tree is clean (zero violations) so
``make verify`` catches a regression in this contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.testing.audit_resource_lifecycle import (
    ResourceLifecycleViolation,
    _default_roots,
    audit_resource_lifecycle_directory,
    audit_resource_lifecycle_file,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


def _audit(
    tmp_path: Path,
    src: str,
    *,
    filename: str = "mod.py",
) -> list[ResourceLifecycleViolation]:
    f = tmp_path / filename
    f.write_text(src, encoding="utf-8")
    return audit_resource_lifecycle_file(f)


# ---------------------------------------------------------------------------
# threading.Thread daemon rule
# ---------------------------------------------------------------------------


def test_non_daemon_thread_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import threading\nthreading.Thread(target=lambda: None)\n")
    assert v, "threading.Thread without daemon=True MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_thread_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=True)\n",
    )


def test_from_import_thread_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "from threading import Thread\nThread(target=lambda: None)\n")
    assert v, "from-imported Thread without daemon=True MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_from_import_thread_with_daemon_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(
        tmp_path,
        "from threading import Thread\nThread(target=lambda: None, daemon=True)\n",
    )


def test_aliased_threading_module_is_flagged(tmp_path: Path) -> None:
    """``import threading as th; th.Thread(...)`` cannot evade detection."""
    v = _audit(tmp_path, "import threading as th\nth.Thread(target=lambda: None)\n")
    assert v, "aliased threading.Thread MUST still be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_thread_pool_executor_is_not_flagged(tmp_path: Path) -> None:
    """ThreadPoolExecutor has its own .shutdown() lifecycle; not the daemon-Thread rule."""
    assert not _audit(
        tmp_path,
        "from concurrent.futures import ThreadPoolExecutor\n"
        "ThreadPoolExecutor(max_workers=4)\n",
    )


def test_non_daemon_thread_marker_suppresses(tmp_path: Path) -> None:
    src = (
        "import threading\n"
        "threading.Thread(target=lambda: None)  # resource-lifecycle-ok:\n"
        "                                       bounded daemon via parent join\n"
    )
    assert not _audit(tmp_path, src)


# ---------------------------------------------------------------------------
# httpx / requests with-context-manager rule
# ---------------------------------------------------------------------------


def test_httpx_client_bare_assignment_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx\nclient = httpx.Client()\n")
    assert v, "bare httpx.Client() MUST be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_httpx_client_in_with_is_not_flagged(tmp_path: Path) -> None:
    src = (
        "import httpx\n"
        "with httpx.Client() as client:\n"
        "    pass\n"
    )
    assert not _audit(tmp_path, src)


def test_httpx_async_client_bare_assignment_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx\nclient = httpx.AsyncClient()\n")
    assert v, "bare httpx.AsyncClient() MUST be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_requests_session_bare_assignment_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import requests\ns = requests.Session()\n")
    assert v, "bare requests.Session() MUST be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_httpx_request_call_is_not_flagged(tmp_path: Path) -> None:
    """httpx.get(...) is a request call, not a Client/Session construction."""
    assert not _audit(tmp_path, "import httpx\nhttpx.get('http://x', timeout=5)\n")


def test_aliased_httpx_client_is_flagged(tmp_path: Path) -> None:
    """``import httpx as hx; hx.Client()`` cannot evade detection."""
    v = _audit(tmp_path, "import httpx as hx\nc = hx.Client()\n")
    assert v, "aliased httpx.Client() MUST still be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_from_import_httpx_client_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "from httpx import Client\nc = Client()\n")
    assert v, "from-imported httpx.Client() MUST still be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_bare_http_client_marker_suppresses(tmp_path: Path) -> None:
    src = (
        "import httpx\n"
        "client = httpx.Client()  # resource-lifecycle-ok: long-lived,\n"
        "                                  explicit close at shutdown\n"
    )
    assert not _audit(tmp_path, src)


# ---------------------------------------------------------------------------
# os fd creation outside ralph/process/
# ---------------------------------------------------------------------------


def test_os_open_outside_process_dir_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import os\nfd = os.open('/tmp/x', os.O_RDONLY)\n",
        filename="not_process.py",
    )
    assert v, "os.open() outside ralph/process/ MUST be flagged"
    assert any(_category(vh) == "raw_os_fd" for vh in v)


def test_os_openpty_outside_process_dir_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import os\nm, s = os.openpty()\n",
        filename="not_process.py",
    )
    assert v, "os.openpty() outside ralph/process/ MUST be flagged"
    assert any(_category(vh) == "raw_os_fd" for vh in v)


def test_os_pipe_outside_process_dir_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import os\nr, w = os.pipe()\n",
        filename="not_process.py",
    )
    assert v, "os.pipe() outside ralph/process/ MUST be flagged"
    assert any(_category(vh) == "raw_os_fd" for vh in v)


def test_os_open_marker_suppresses(tmp_path: Path) -> None:
    src = (
        "import os\n"
        "fd = os.open('/tmp/x', os.O_RDONLY)  # resource-lifecycle-ok:\n"
        "                                      short-lived, closed in scope\n"
    )
    assert not _audit(tmp_path, src, filename="not_process.py")


# ---------------------------------------------------------------------------
# directory scan + main
# ---------------------------------------------------------------------------


def test_audit_directory_aggregates(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None)\n", encoding="utf-8"
    )
    (tmp_path / "b.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None, daemon=True)\n",
        encoding="utf-8",
    )
    violations, checked = audit_resource_lifecycle_directory(tmp_path)
    assert checked == 2
    assert len(violations) == 1


def test_main_returns_nonzero_on_violation(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None)\n", encoding="utf-8"
    )
    assert main([str(tmp_path)]) == 1


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None, daemon=True)\n",
        encoding="utf-8",
    )
    assert main([str(tmp_path)]) == 0


def test_main_root_not_found(tmp_path: Path) -> None:
    """A missing root returns exit 2 (matches audit_mcp_timeout convention)."""
    missing = tmp_path / "does_not_exist_dir_xyz"
    assert main([str(missing)]) == 2


# ---------------------------------------------------------------------------
# Default roots + production tree clean
# ---------------------------------------------------------------------------


def test_default_roots_cover_required_packages() -> None:
    """_default_roots() MUST include every production package the contract covers."""
    roots = _default_roots()
    assert any(str(r).endswith("ralph/mcp") for r in roots)
    assert any(str(r).endswith("ralph/agents") for r in roots)
    assert any(str(r).endswith("ralph/executor") for r in roots)
    assert any(str(r).endswith("ralph/process") for r in roots)
    assert any(str(r).endswith("ralph/pipeline") for r in roots)
    assert any(str(r).endswith("ralph/runtime") for r in roots)
    assert any(str(r).endswith("ralph/pro_support") for r in roots)
    assert any(str(r).endswith("ralph/recovery") for r in roots)


def test_real_production_tree_has_zero_violations() -> None:
    """The real production ``ralph/`` tree MUST be clean (zero violations).

    This is the canonical regression for AC-03: if any future commit
    grows a non-daemon-thread, a bare httpx/requests client, or a
    raw os fd outside ``ralph/process/``, this test fails before
    ``make verify`` runs the production-tree scan. Mirrors the
    style of ``test_real_production_tree_*`` in test_audit_mcp_timeout.
    """
    violations: list[ResourceLifecycleViolation] = []
    for root in _default_roots():
        v, _checked = audit_resource_lifecycle_directory(root)
        violations.extend(v)
    if violations:
        formatted = "\n".join(f"  {v}" for v in violations)
        pytest.fail(
            f"Production tree has {len(violations)} resource-lifecycle violation(s); "
            f"the contract MUST hold today:\n{formatted}"
        )


# ---------------------------------------------------------------------------
# Contract-strictness regressions (analysis-feedback closures)
# ---------------------------------------------------------------------------


def test_daemon_false_is_flagged(tmp_path: Path) -> None:
    """``daemon=False`` MUST be flagged — it is the same lifecycle hazard
    as omitting ``daemon`` entirely (a non-daemon thread that blocks the
    interpreter shutdown atexit join). Mere keyword presence is not enough;
    the contract requires ``daemon=True``.
    """
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=False)\n",
    )
    assert v, "threading.Thread(target=..., daemon=False) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_one_int_is_flagged(tmp_path: Path) -> None:
    """``daemon=1`` MUST be flagged — int is truthy but ``threading.Thread``
    rejects non-bool ``daemon`` at runtime in Python 3.13+ with
    ``TypeError: daemon must be explicitly set to True``. The audit
    must NOT accept arbitrary truthy constants: only the literal
    ``True`` boolean satisfies the contract. Regression for the
    analysis-feedback finding that ``_keyword_truthy()`` previously
    accepted ``daemon=1`` because ``bool(1) is True``.
    """
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=1)\n",
    )
    assert v, "threading.Thread(target=..., daemon=1) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_zero_int_is_flagged(tmp_path: Path) -> None:
    """``daemon=0`` MUST be flagged — falsy non-bool int is the same
    hazard as ``daemon=False`` (and would crash ``Thread.__init__`` on
    Python 3.13+). Pins the boolean-only rule.
    """
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=0)\n",
    )
    assert v, "threading.Thread(target=..., daemon=0) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_string_is_flagged(tmp_path: Path) -> None:
    """``daemon=\"yes\"`` MUST be flagged — string is truthy but is not a
    boolean and would crash ``Thread.__init__``. Only the literal
    ``True`` boolean passes the audit.
    """
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=\"yes\")\n",
    )
    assert v, "threading.Thread(target=..., daemon='yes') MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_none_is_flagged(tmp_path: Path) -> None:
    """``daemon=None`` MUST be flagged — None is not the explicit
    ``True`` boolean the contract requires.
    """
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=None)\n",
    )
    assert v, "threading.Thread(target=..., daemon=None) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_name_is_flagged(tmp_path: Path) -> None:
    """``daemon=some_var`` MUST be flagged — non-constant expression
    cannot be statically resolved; the audit must surface it for
    human review rather than silently accept it.
    """
    v = _audit(
        tmp_path,
        "import threading\n"
        "is_daemon = True\n"
        "threading.Thread(target=lambda: None, daemon=is_daemon)\n",
    )
    assert v, "threading.Thread(target=..., daemon=is_daemon) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_async_with_httpx_async_client_is_not_flagged(tmp_path: Path) -> None:
    """``async with httpx.AsyncClient() as client:`` is a legitimate
    context-manager usage and MUST NOT be flagged. The audit must
    recognize ``ast.AsyncWith`` in addition to ``ast.With`` —
    otherwise legitimate async-client usage is falsely reported as a
    leak and forces spurious ``# resource-lifecycle-ok`` markers.
    """
    src = (
        "import httpx\n"
        "async def f():\n"
        "    async with httpx.AsyncClient() as client:\n"
        "        response = await client.get('http://x')\n"
    )
    assert not _audit(tmp_path, src), (
        "async with httpx.AsyncClient() as client MUST be accepted; "
        "the audit must recognize ast.AsyncWith"
    )


def test_main_audits_every_explicit_root(tmp_path: Path) -> None:
    """``main([clean, bad])`` MUST audit BOTH roots and return non-zero
    when any one of them violates — a single-root short-circuit would
    silently ignore a violating root and produce a false-clean exit.
    """
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "c.py").write_text(
        "import threading\n"
        "threading.Thread(target=lambda: None, daemon=True)\n",
        encoding="utf-8",
    )
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "b.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None)\n",
        encoding="utf-8",
    )
    assert main([str(clean_dir), str(bad_dir)]) == 1, (
        "main() must audit EVERY explicit root and surface violations "
        "from any of them — a single-root short-circuit is a bug"
    )


def test_main_audits_three_explicit_roots(tmp_path: Path) -> None:
    """Three-root regression: ``main([a, b, c])`` audits all three.

    Catches a partial-fix regression where only the last root is
    iterated but a missing-root short-circuit on a middle root masks
    the violating tail.
    """
    roots = []
    for idx, has_violation in enumerate((False, True, False)):
        d = tmp_path / f"root{idx}"
        d.mkdir()
        daemon_kw = "" if has_violation else ", daemon=True"
        src = f"import threading\nthreading.Thread(target=lambda: None{daemon_kw})\n"
        (d / "m.py").write_text(src, encoding="utf-8")
        roots.append(str(d))
    assert main(roots) == 1


def _category(violation: ResourceLifecycleViolation) -> str:
    """Return the violation category; helper for assertions across tests."""
    return violation.category
