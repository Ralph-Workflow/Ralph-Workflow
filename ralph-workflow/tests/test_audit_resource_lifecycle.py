from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_resource_lifecycle as resource_lifecycle_audit
from ralph.testing.audit_resource_lifecycle import (
    ResourceLifecycleViolation,
    _default_roots,
    audit_resource_lifecycle_directory,
    audit_resource_lifecycle_file,
    main,
    registry_path,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "marker exemption": "the inline marker comment no longer waives a resource-lifecycle finding",
    "structural exemption": "the structural exemption registry now carries the waived call site",
}


def _audit(
    tmp_path: Path,
    src: str,
    *,
    filename: str = "mod.py",
) -> list[ResourceLifecycleViolation]:
    f = tmp_path / filename
    f.write_text(src, encoding="utf-8")
    return audit_resource_lifecycle_file(f)


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
    v = _audit(tmp_path, "import threading as th\nth.Thread(target=lambda: None)\n")
    assert v, "aliased threading.Thread MUST still be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_thread_pool_executor_is_not_flagged(tmp_path: Path) -> None:
    assert not _audit(
        tmp_path,
        "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(max_workers=4)\n",
    )


def test_non_daemon_thread_marker_no_longer_suppresses(tmp_path: Path) -> None:
    src = (
        "import threading\n"
        "threading.Thread(target=lambda: None)  # resource-lifecycle-ok: bounded\n"
    )
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "non_daemon_thread" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_httpx_client_bare_assignment_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx\nclient = httpx.Client()\n")
    assert v, "bare httpx.Client() MUST be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_httpx_client_in_with_is_not_flagged(tmp_path: Path) -> None:
    src = "import httpx\nwith httpx.Client() as client:\n    pass\n"
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
    assert not _audit(tmp_path, "import httpx\nhttpx.get('http://x', timeout=5)\n")


def test_aliased_httpx_client_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import httpx as hx\nc = hx.Client()\n")
    assert v, "aliased httpx.Client() MUST still be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_from_import_httpx_client_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "from httpx import Client\nc = Client()\n")
    assert v, "from-imported httpx.Client() MUST still be flagged"
    assert any(_category(vh) == "bare_http_client" for vh in v)


def test_bare_http_client_marker_no_longer_suppresses(tmp_path: Path) -> None:
    src = "import httpx\nclient = httpx.Client()  # resource-lifecycle-ok: long-lived\n"
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "bare_http_client" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


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


def test_os_open_marker_no_longer_suppresses(tmp_path: Path) -> None:
    src = "import os\nfd = os.open('/tmp/x', os.O_RDONLY)  # resource-lifecycle-ok: short-lived\n"
    violations = _audit(tmp_path, src, filename="not_process.py")
    assert any(_category(item) == "raw_os_fd" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


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
    missing = tmp_path / "does_not_exist_dir_xyz"
    assert main([str(missing)]) == 2


def test_default_roots_cover_required_packages() -> None:
    roots = _default_roots()
    assert any(str(r).endswith("ralph/mcp") for r in roots)
    assert any(str(r).endswith("ralph/agents") for r in roots)
    assert any(str(r).endswith("ralph/executor") for r in roots)
    assert any(str(r).endswith("ralph/process") for r in roots)
    assert any(str(r).endswith("ralph/pipeline") for r in roots)
    assert any(str(r).endswith("ralph/runtime") for r in roots)
    assert any(str(r).endswith("ralph/pro_support") for r in roots)
    assert any(str(r).endswith("ralph/recovery") for r in roots)
    assert any(str(r).endswith("ralph/display") for r in roots)
    assert any(str(r).endswith("ralph/prompts") for r in roots)
    assert any(str(r).endswith("ralph/diagnostics") for r in roots)
    required_expanded_roots = {
        "api",
        "update_check",
        "contrib",
        "git",
        "cli",
        "telemetry",
        "policy",
        "language_detector",
        "workspace",
        "phases",
        "guidelines",
        "checkpoint",
        "config",
        "exit_pause",
        "files",
        "platform",
        "project_policy",
        "skills",
        "interrupt",
    }
    root_names = {root.name for root in roots}
    assert required_expanded_roots <= root_names


def test_unbounded_instance_accumulator_flagged(tmp_path: Path) -> None:
    src = "class C:\n    def __init__(self) -> None:\n        self._x = {}\n"
    v = _audit(tmp_path, src)
    assert v, "self._x = {} in __init__ MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_unbounded_module_level_accumulator_flagged(tmp_path: Path) -> None:
    src = "_x: dict = {}\n"
    v = _audit(tmp_path, src)
    assert v, "module-level '_x: dict = {}' MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_unbounded_deque_without_maxlen_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque()\n"
    )
    v = _audit(tmp_path, src)
    assert v, "self._q = deque() without maxlen MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_maxlen_none_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque(maxlen=None)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "deque(maxlen=None) MUST be flagged -- it defeats the FIFO cap"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_collections_deque_with_maxlen_none_is_flagged(tmp_path: Path) -> None:
    src = (
        "import collections\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = collections.deque(maxlen=None)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "collections.deque(maxlen=None) MUST be flagged"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_maxlen_zero_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque(maxlen=0)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "deque(maxlen=0) MUST be flagged -- non-positive cap"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_variable_maxlen_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "N = 8\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque(maxlen=N)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "deque(maxlen=N) with a non-constant expression MUST be flagged"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_attribute_maxlen_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self.cap = 8\n"
        "        self._q = deque(maxlen=self.cap)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "deque(maxlen=self.cap) MUST be flagged -- non-constant value"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_negative_maxlen_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque(maxlen=-1)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "deque(maxlen=-1) MUST be flagged -- non-positive cap"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_deque_with_maxlen_is_not_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import deque\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = deque(maxlen=8)\n"
    )
    assert not _audit(tmp_path, src), (
        "deque(maxlen=...) MUST NOT be flagged — it is bounded by construction"
    )


def test_collections_deque_with_maxlen_is_not_flagged(tmp_path: Path) -> None:
    src = (
        "import collections\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = collections.deque(maxlen=8)\n"
    )
    assert not _audit(tmp_path, src)


def test_bounded_accumulator_marker_no_longer_suppresses(tmp_path: Path) -> None:
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = {}  # bounded-accumulator-ok: per-process, drained\n"
    )
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "unbounded_accumulator" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_resource_lifecycle_marker_no_longer_works_with_accumulator(tmp_path: Path) -> None:
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = {}  # resource-lifecycle-ok: legacy marker, still suppresses\n"
    )
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "unbounded_accumulator" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_single_element_list_sentinel_not_flagged(tmp_path: Path) -> None:
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._counter = [0]\n"
        "        self._flag = [False]\n"
        "        self._last = [None]\n"
    )
    assert not _audit(tmp_path, src), (
        "single-element list literals (mutable-closure idiom) MUST NOT be "
        "flagged as unbounded_accumulator"
    )


def test_static_dispatch_table_with_string_keys_not_flagged(tmp_path: Path) -> None:
    src = (
        "HANDLERS = {\n    'session': handle_session,\n    'message_end': handle_message_end,\n}\n"
    )
    assert not _audit(tmp_path, src)


def test_static_dispatch_table_with_tuple_keys_not_flagged(tmp_path: Path) -> None:
    src = "POLICY_MODES = {(True, False): strict, (False, True): lenient}\n"
    assert not _audit(tmp_path, src)


def test_all_dunder_is_not_flagged(tmp_path: Path) -> None:
    src = '__all__ = ["Foo", "Bar", "Baz"]\n'
    assert not _audit(tmp_path, src)


def test_empty_list_in_non_init_function_not_flagged(tmp_path: Path) -> None:
    src = "def helper():\n    x = []\n    x.append(1)\n"
    assert not _audit(tmp_path, src)


def test_dataclass_field_default_factory_not_flagged(tmp_path: Path) -> None:
    src = (
        "from dataclasses import dataclass, field\n"
        "@dataclass\n"
        "class C:\n"
        "    items: list = field(default_factory=list)\n"
    )
    assert not _audit(tmp_path, src)


def test_ordered_dict_instance_accumulator_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import OrderedDict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = OrderedDict()\n"
    )
    v = _audit(tmp_path, src)
    assert v, "self._x = OrderedDict() in __init__ MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_collections_ordered_dict_instance_accumulator_flagged(tmp_path: Path) -> None:
    src = (
        "import collections\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = collections.OrderedDict()\n"
    )
    v = _audit(tmp_path, src)
    assert v, "self._x = collections.OrderedDict() MUST be flagged"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_ordered_dict_module_level_accumulator_flagged(tmp_path: Path) -> None:
    src = "from collections import OrderedDict\n_CACHE: OrderedDict[str, dict] = OrderedDict()\n"
    v = _audit(tmp_path, src)
    assert v, "module-level OrderedDict() MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_defaultdict_instance_accumulator_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import defaultdict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = defaultdict()\n"
    )
    v = _audit(tmp_path, src)
    assert v, "self._x = defaultdict() in __init__ MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_collections_defaultdict_instance_accumulator_flagged(tmp_path: Path) -> None:
    src = (
        "import collections\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = collections.defaultdict()\n"
    )
    v = _audit(tmp_path, src)
    assert v, "self._x = collections.defaultdict() MUST be flagged"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_defaultdict_with_factory_still_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import defaultdict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = defaultdict(list)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "defaultdict(list) MUST be flagged -- factory does NOT bound the dict"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_ordered_dict_with_marker_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import OrderedDict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = OrderedDict()  # bounded-accumulator-ok: FIFO cap _MAX=32\n"
    )
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "unbounded_accumulator" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_defaultdict_with_marker_is_flagged(tmp_path: Path) -> None:
    src = (
        "from collections import defaultdict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = defaultdict()  # bounded-accumulator-ok: capped by external eviction\n"
    )
    violations = _audit(tmp_path, src)
    assert any(_category(item) == "unbounded_accumulator" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_daemon_false_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=False)\n",
    )
    assert v, "threading.Thread(target=..., daemon=False) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_one_int_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=1)\n",
    )
    assert v, "threading.Thread(target=..., daemon=1) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_zero_int_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=0)\n",
    )
    assert v, "threading.Thread(target=..., daemon=0) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_string_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        'import threading\nthreading.Thread(target=lambda: None, daemon="yes")\n',
    )
    assert v, "threading.Thread(target=..., daemon='yes') MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_none_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import threading\nthreading.Thread(target=lambda: None, daemon=None)\n",
    )
    assert v, "threading.Thread(target=..., daemon=None) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_daemon_name_is_flagged(tmp_path: Path) -> None:
    v = _audit(
        tmp_path,
        "import threading\n"
        "is_daemon = True\n"
        "threading.Thread(target=lambda: None, daemon=is_daemon)\n",
    )
    assert v, "threading.Thread(target=..., daemon=is_daemon) MUST be flagged"
    assert any(_category(vh) == "non_daemon_thread" for vh in v)


def test_async_with_httpx_async_client_is_not_flagged(tmp_path: Path) -> None:
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
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "c.py").write_text(
        "import threading\nthreading.Thread(target=lambda: None, daemon=True)\n",
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
    return violation.category


def test_direct_subprocess_spawn_is_flagged(tmp_path: Path) -> None:
    v = _audit(tmp_path, "import subprocess\nsubprocess.run(['x'])\n")
    assert any(_category(item) == "direct_child_spawn" for item in v)


def test_aliased_and_from_import_direct_subprocess_spawns_are_flagged(tmp_path: Path) -> None:
    aliased = _audit(tmp_path, "import subprocess as sp\nsp.run(['x'])\n")
    imported = _audit(tmp_path, "from subprocess import run\nrun(['x'])\n")
    assert any(_category(item) == "direct_child_spawn" for item in aliased)
    assert any(_category(item) == "direct_child_spawn" for item in imported)


def test_direct_subprocess_spawn_under_process_is_clean(tmp_path: Path) -> None:
    del tmp_path
    process_manager = __file__.replace(
        "tests/test_audit_resource_lifecycle.py", "ralph/process/manager/__init__.py"
    )
    violations = audit_resource_lifecycle_file(Path(process_manager))
    assert not any(_category(item) == "direct_child_spawn" for item in violations)


def test_direct_subprocess_spawn_marker_no_longer_suppresses(tmp_path: Path) -> None:
    violations = _audit(
        tmp_path,
        "import subprocess\nsubprocess.run(['x'])  # resource-lifecycle-ok: bounded fixture\n",
    )
    assert any(_category(item) == "direct_child_spawn" for item in violations), (
        "an inline marker comment MUST NOT silence the resource-lifecycle audit"
    )


def test_process_manager_spawn_is_clean(tmp_path: Path) -> None:
    assert not _audit(tmp_path, "get_process_manager().spawn(['x'])\n")


def test_from_os_import_spawn_forms_are_flagged(tmp_path: Path) -> None:
    for source in (
        "from os import system\nsystem('x')\n",
        "from os import fork\nfork()\n",
        "from os import posix_spawn\nposix_spawn('x', [], {})\n",
    ):
        violations = _audit(tmp_path, source)
        assert any(_category(item) == "direct_child_spawn" for item in violations)


def _register(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    scope: str,
    category: str,
) -> None:
    key = (registry_path(path), scope, category)
    monkeypatch.setattr(
        resource_lifecycle_audit,
        "EXEMPT_CALL_SITES",
        frozenset({key}),
    )


def test_registered_call_site_is_exempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "mod.py"
    path.write_text(
        "import threading\nthreading.Thread(target=lambda: None)\n",
        encoding="utf-8",
    )
    _register(monkeypatch, path, "<module>", "non_daemon_thread")
    assert not audit_resource_lifecycle_file(path)


def test_registered_scope_does_not_exempt_another_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(
        "class C:\n    def __init__(self) -> None:\n        self._x = {}\n",
        encoding="utf-8",
    )
    _register(monkeypatch, path, "<module>", "unbounded_accumulator")
    violations = audit_resource_lifecycle_file(path)
    assert any(_category(item) == "unbounded_accumulator" for item in violations)


def test_registered_category_does_not_exempt_another_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(
        "import threading\nthreading.Thread(target=lambda: None)\n",
        encoding="utf-8",
    )
    _register(monkeypatch, path, "<module>", "bare_http_client")
    violations = audit_resource_lifecycle_file(path)
    assert any(_category(item) == "non_daemon_thread" for item in violations)


def test_registered_call_site_suppresses_only_its_own_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "mod.py"
    path.write_text(
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = {}\n"
        "class D:\n"
        "    def __init__(self) -> None:\n"
        "        self._y = {}\n",
        encoding="utf-8",
    )
    _register(monkeypatch, path, "C.__init__", "unbounded_accumulator")
    violations = audit_resource_lifecycle_file(path)
    assert len([item for item in violations if _category(item) == "unbounded_accumulator"]) == 1
