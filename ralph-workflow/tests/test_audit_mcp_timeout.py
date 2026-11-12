from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_mcp_timeout import (
    STRUCTURAL_EXEMPTIONS,
    McpTimeoutViolation,
    _default_roots,
    audit_mcp_directory,
    audit_mcp_file,
    main,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_every_registered_exemption_covers_exactly_one_call_site": (
        "the per-entry audit pair was split into one parametrized case per registered "
        "exemption so each case fits the pinned 1.0 CPU-second per-test budget; "
        "assertions unchanged"
    ),
}

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "ralph"

MARKER = "# mcp-timeout-ok: still reported"

_EXEMPTION_KEYS: tuple[tuple[str, str, str, int], ...] = tuple(STRUCTURAL_EXEMPTIONS)

_EXEMPTION_IDS: tuple[str, ...] = tuple(
    f"{module}-{qualname}-{category}-{index}"
    for module, qualname, category, index in _EXEMPTION_KEYS
)


def _audit(tmp_path: Path, src: str) -> list[McpTimeoutViolation]:
    f = tmp_path / "mod.py"
    f.write_text(src, encoding="utf-8")
    return audit_mcp_file(f)


def _audit_exempted(
    tmp_path: Path,
    src: str,
    exemptions: dict[tuple[str, str, str, int], str],
) -> list[McpTimeoutViolation]:
    f = tmp_path / "mod.py"
    f.write_text(src, encoding="utf-8")
    return audit_mcp_file(f, exemptions=exemptions)


def _module_file(identity: str) -> Path:
    parts = identity.split(".")
    return PACKAGE_DIR.joinpath(*parts[1:]).with_suffix(".py")


def test_subprocess_run_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import subprocess\nsubprocess.run(['git', 'status'])\n")


def test_subprocess_run_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess\nsubprocess.run(['x'], timeout=5)\n")


def test_communicate_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "stdout, stderr = proc.communicate()\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_communicate_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.communicate(timeout=30)\n")


def test_communicate_positional_input_is_still_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "proc.communicate(b'data')\n")


def test_communicate_and_cleanup_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.communicate_and_cleanup()\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_communicate_and_cleanup_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.communicate_and_cleanup(timeout=30)\n")


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


def test_os_system_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import os\nos.system('sleep 9')\n")


def test_subprocess_check_output_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import subprocess\nsubprocess.check_output(['x'])\n")


def test_subprocess_check_output_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess\nsubprocess.check_output(['x'], timeout=5)\n")


def test_wait_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "proc.wait()\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_wait_with_keyword_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.wait(timeout=5)\n")


def test_wait_with_positional_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "proc.wait(5)\n")


def test_blocking_for_in_stdout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "for line in proc.stdout:\n    pass\n")
    assert len(v) == 1
    assert v[0].category == "blocking_stream_iter"


def test_blocking_for_in_stderr_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "for line in proc.stderr:\n    pass\n")


def test_iterating_tuple_of_streams_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "for s in (proc.stdout, proc.stderr):\n    s.close()\n")


def test_iterating_splitlines_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "for line in result.stdout.splitlines():\n    pass\n")


def test_marker_comment_does_not_silence_blocking_stream_iter(tmp_path: Path) -> None:
    v = _audit(tmp_path, f"for line in proc.stdout:  {MARKER}\n    pass\n")
    assert len(v) == 1
    assert v[0].category == "blocking_stream_iter"


def test_marker_comment_does_not_silence_wait(tmp_path: Path) -> None:
    v = _audit(tmp_path, f"proc.wait()  {MARKER}\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_marker_comment_does_not_silence_communicate(tmp_path: Path) -> None:
    v = _audit(tmp_path, f"proc.communicate()  {MARKER}\n")
    assert len(v) == 1
    assert v[0].category == "communicate"


def test_marker_comment_does_not_silence_wait_with_timeout_none(tmp_path: Path) -> None:
    v = _audit(tmp_path, f"proc.wait(timeout=None)  {MARKER}\n")
    assert len(v) == 1
    assert v[0].category == "wait"


def test_marker_comment_does_not_silence_submit_future_result(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        f"def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()  {MARKER}\n",
    )
    assert len(v) == 1
    assert v[0].category == "future_result"


def test_marker_comment_does_not_silence_network_call(tmp_path: Path) -> None:
    v = _audit(tmp_path, f"import httpx\nhttpx.get('http://x')  {MARKER}\n")
    assert len(v) == 1
    assert v[0].category == "network"


def test_httpx_get_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import httpx\nhttpx.get('http://x')\n")


def test_httpx_get_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import httpx\nhttpx.get('http://x', timeout=5)\n")


def test_urlopen_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import urllib.request\nurllib.request.urlopen(req)\n")


def test_socket_create_connection_without_timeout_is_flagged(tmp_path: Path) -> None:
    assert _audit(tmp_path, "import socket\nsocket.create_connection(addr)\n")


def test_popen_construction_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import subprocess\nsubprocess.Popen(['x'])\n")


def test_socket_socket_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "import socket\nsocket.socket(socket.AF_INET)\n")


def test_submit_future_result_without_timeout_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()\n")
    assert len(v) == 1
    assert v[0].category == "future_result"


def test_submit_future_result_with_timeout_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result(timeout=None)\n",
    )
    assert len(v) == 1
    assert v[0].category == "future_result"


def test_submit_future_result_with_timeout_is_allowed(tmp_path: Path) -> None:
    assert not _audit(
        tmp_path,
        "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result(timeout=5.0)\n",
    )


def test_result_on_non_submit_name_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "def run(value):\n    return value.result()\n")


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


def test_variable_timeout_is_out_of_scope(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "timeout = compute_timeout()\nproc.wait(timeout=timeout)\n")


def test_structural_exemption_suppresses_the_named_call_site(tmp_path: Path) -> None:
    exemptions = {("mod", "run", "future_result", 0): "bounded by the outer deadline"}
    src = "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()\n"
    assert not _audit_exempted(tmp_path, src, exemptions)


def test_structural_exemption_without_the_entry_still_flags(tmp_path: Path) -> None:
    src = "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()\n"
    v = _audit_exempted(tmp_path, src, {})
    assert len(v) == 1
    assert v[0].category == "future_result"


def test_structural_exemption_does_not_cover_a_different_callable(tmp_path: Path) -> None:
    exemptions = {("mod", "other", "future_result", 0): "bounded by the outer deadline"}
    src = "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()\n"
    assert _audit_exempted(tmp_path, src, exemptions)


def test_structural_exemption_does_not_cover_a_different_category(tmp_path: Path) -> None:
    exemptions = {("mod", "run", "wait", 0): "bounded by the outer deadline"}
    src = "def run(ex, cb):\n    f = ex.submit(cb)\n    f.result()\n"
    assert _audit_exempted(tmp_path, src, exemptions)


def test_structural_exemption_covers_only_the_named_occurrence(tmp_path: Path) -> None:
    exemptions = {("mod", "run", "wait", 0): "first wait is bounded"}
    src = "def run(p):\n    p.wait()\n    p.wait()\n"
    v = _audit_exempted(tmp_path, src, exemptions)
    assert len(v) == 1
    assert v[0].line == 3


def test_structural_exemption_applies_to_the_matching_module_only(tmp_path: Path) -> None:
    other = tmp_path / "other.py"
    other.write_text("proc.wait()\n", encoding="utf-8")
    exemptions = {("mod", "", "wait", 0): "bounded elsewhere"}
    assert audit_mcp_file(other, exemptions=exemptions)


def test_stale_structural_exemption_is_reported(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("proc.wait(timeout=5)\n", encoding="utf-8")
    exemptions = {("mod", "gone", "wait", 0): "the call site no longer exists"}
    violations, checked = audit_mcp_directory(tmp_path, exemptions=exemptions)
    assert checked == 1
    stale = [v for v in violations if v.category == "stale_exemption"]
    assert len(stale) == 1


@pytest.mark.parametrize("key", _EXEMPTION_KEYS, ids=_EXEMPTION_IDS)
def test_every_registered_exemption_covers_exactly_one_call_site(
    key: tuple[str, str, str, int],
) -> None:
    target = _module_file(key[0])
    assert target.is_file(), f"exemption target missing: {target}"
    without = {k: v for k, v in STRUCTURAL_EXEMPTIONS.items() if k != key}
    violations = audit_mcp_file(target, exemptions=without)
    assert len(violations) == 1, f"{key} must exempt exactly one call site"
    assert violations[0].category == key[2]
    assert not audit_mcp_file(target), f"{key} must leave its file clean"


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


def test_audit_flags_unbounded_httpx_in_pro_support(tmp_path: Path) -> None:
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
    roots = _default_roots()
    assert any(str(r).endswith("ralph/pro_support") for r in roots), (
        f"_default_roots() must include ralph/pro_support; got {roots}"
    )


def test_default_roots_cover_api_update_check_and_contrib() -> None:
    root_names = {root.name for root in _default_roots()}
    assert {"api", "update_check", "contrib"} <= root_names


def test_default_roots_cover_executor_agents_process() -> None:
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
