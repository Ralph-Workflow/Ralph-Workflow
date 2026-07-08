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
        "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(max_workers=4)\n",
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
    # wt-024 memory-perf AC-02: display holds per-unit accumulators
    # drained by drop_unit (parallel coordinator finally block) and
    # prompts holds the template registry caches (bounded by the
    # packaged-template file set). Both MUST be in default roots so a
    # future leak in either fails make verify before it ships.
    assert any(str(r).endswith("ralph/display") for r in roots)
    assert any(str(r).endswith("ralph/prompts") for r in roots)


@pytest.mark.timeout_seconds(10)
@pytest.mark.subprocess_e2e
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
# Resource accumulator contract (wt-024 memory-perf AC-04)
# ---------------------------------------------------------------------------
#
# The 4th contract flags long-lived mutable accumulators (list / dict /
# set / deque) without a FIFO/size cap or a bounded-accumulator-ok
# marker. Detection scope:
#   - module-level names (Name targets);
#   - instance attributes (self.X in __init__ bodies).
#
# Excluded by design:
#   - ``__all__`` (Python re-export convention);
#   - dataclass field defaults (``field(default_factory=...)``);
#   - local variables inside non-__init__ functions;
#   - single-element list literals ``[X]`` (Python's mutable-closure
#     idiom for counter / flag / None sentinels);
#   - dict / set literals with all-static keys (dispatch tables).


def test_unbounded_instance_accumulator_flagged(tmp_path: Path) -> None:
    """``self._x = {}`` in __init__ MUST be flagged (instance attr accumulator)."""
    src = "class C:\n    def __init__(self) -> None:\n        self._x = {}\n"
    v = _audit(tmp_path, src)
    assert v, "self._x = {} in __init__ MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_unbounded_module_level_accumulator_flagged(tmp_path: Path) -> None:
    """Module-level ``_x: dict = {}`` MUST be flagged (module-level accumulator)."""
    src = "_x: dict = {}\n"
    v = _audit(tmp_path, src)
    assert v, "module-level '_x: dict = {}' MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_unbounded_deque_without_maxlen_flagged(tmp_path: Path) -> None:
    """``self._q = deque()`` (no maxlen) MUST be flagged — a deque without
    maxlen is unbounded by construction."""
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
    """``deque(maxlen=None)`` MUST be flagged -- maxlen=None is effectively unbounded.

    Regression for the analysis-feedback finding that ``_keyword_present``
    previously accepted ``deque(maxlen=None)`` because it only checked
    keyword PRESENCE, not the value. The new ``_keyword_value_is_positive_int``
    helper rejects None, 0, -1, and non-constant expressions.
    """
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
    """``collections.deque(maxlen=None)`` MUST be flagged -- same reasoning."""
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
    """``deque(maxlen=0)`` MUST be flagged -- non-positive cap (degenerate FIFO)."""
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
    """``deque(maxlen=N)`` (non-constant) MUST be flagged -- cannot be statically resolved.

    Real production bounded deques carry a ``# bounded-accumulator-ok: <reason>``
    marker so the audit passes; the bare form is surfaced for human review.
    """
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
    """``deque(maxlen=self.cap)`` MUST be flagged (non-constant value)."""
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
    """``deque(maxlen=-1)`` MUST be flagged -- ``collections.deque`` rejects non-positive maxlen."""
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
    """``self._q = deque(maxlen=8)`` is bounded by construction — NOT flagged."""
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
    """``self._q = collections.deque(maxlen=8)`` is bounded — NOT flagged.
    Verifies the import-alias resolution works for the accumulator contract
    (matches the daemon-thread / http-client alias-resolution test pattern).
    """
    src = (
        "import collections\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._q = collections.deque(maxlen=8)\n"
    )
    assert not _audit(tmp_path, src)


def test_bounded_accumulator_marker_suppresses(tmp_path: Path) -> None:
    """``# bounded-accumulator-ok: <reason>`` MUST suppress the violation."""
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = {}  # bounded-accumulator-ok: per-process, drained\n"
    )
    assert not _audit(tmp_path, src)


def test_resource_lifecycle_marker_still_works_with_accumulator(tmp_path: Path) -> None:
    """The original ``# resource-lifecycle-ok`` marker also suppresses
    (backward compatibility: both markers are part of the marker SET,
    so existing markers on accumulator-bearing lines keep working).
    """
    src = (
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = {}  # resource-lifecycle-ok: legacy marker, still suppresses\n"
    )
    assert not _audit(tmp_path, src)


def test_single_element_list_sentinel_not_flagged(tmp_path: Path) -> None:
    """``[X]`` (single-element list literal) is the Python mutable-closure
    idiom for a counter / flag / None sentinel — NOT an accumulator.
    Skipped to avoid false positives on common streaming-reader patterns.
    """
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
    """Dict literal with all-string keys is a static — skipped."""
    src = (
        "HANDLERS = {\n    'session': handle_session,\n    'message_end': handle_message_end,\n}\n"
    )
    assert not _audit(tmp_path, src)


def test_all_dunder_is_not_flagged(tmp_path: Path) -> None:
    """``__all__`` (Python re-export convention) is excluded from the
    accumulator contract — it is a static list of exported symbol names,
    never mutated across a session.
    """
    src = '__all__ = ["Foo", "Bar", "Baz"]\n'
    assert not _audit(tmp_path, src)


def test_empty_list_in_non_init_function_not_flagged(tmp_path: Path) -> None:
    """Local ``x = []`` inside a non-__init__ function is out of scope
    (higher false-positive rate; the BudgetState.failures leak class
    was closed by dropping the field + the tracemalloc test, not by this AST contract).
    """
    src = "def helper():\n    x = []\n    x.append(1)\n"
    assert not _audit(tmp_path, src)


def test_dataclass_field_default_factory_not_flagged(tmp_path: Path) -> None:
    """``field(default_factory=list)`` in a dataclass is excluded."""
    src = (
        "from dataclasses import dataclass, field\n"
        "@dataclass\n"
        "class C:\n"
        "    items: list = field(default_factory=list)\n"
    )
    assert not _audit(tmp_path, src)


# ---------------------------------------------------------------------------
# OrderedDict / defaultdict detection (wt-024 memory-perf AC-01)
# ---------------------------------------------------------------------------
#
# OrderedDict and defaultdict have NO ``maxlen`` kwarg (unlike ``deque``);
# the FIFO-cap escape hatch is a manual ``popitem(last=False)`` /
# ``len(...) > cap`` eviction policy in the code itself. The audit MUST
# flag these as unbounded accumulators so the only escape is an honest
# ``# bounded-accumulator-ok: <cap>`` marker naming the real cap / drain.


def test_ordered_dict_instance_accumulator_flagged(tmp_path: Path) -> None:
    """``self._x = OrderedDict()`` in __init__ MUST be flagged.

    OrderedDict has no ``maxlen`` kwarg, so the FIFO escape hatch is a
    manual ``popitem(last=False)`` eviction policy in the code itself.
    The audit MUST flag the bare assignment; the only escape is an
    honest ``# bounded-accumulator-ok: <cap>`` marker naming the cap.
    """
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
    """``self._x = collections.OrderedDict()`` MUST be flagged.

    Mirrors the ``collections.deque`` alias-resolution test pattern.
    """
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
    """Module-level ``_CACHE: OrderedDict = OrderedDict()`` MUST be flagged."""
    src = "from collections import OrderedDict\n_CACHE: OrderedDict[str, dict] = OrderedDict()\n"
    v = _audit(tmp_path, src)
    assert v, "module-level OrderedDict() MUST be flagged as unbounded_accumulator"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_defaultdict_instance_accumulator_flagged(tmp_path: Path) -> None:
    """``self._x = defaultdict()`` MUST be flagged (no maxlen escape hatch).

    Mirrors the OrderedDict test pattern. defaultdict has no size cap;
    the audit MUST flag the bare assignment.
    """
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
    """``self._x = collections.defaultdict()`` MUST be flagged."""
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
    """``self._x = defaultdict(list)`` MUST still be flagged.

    The factory argument does NOT bound the dict (it only controls the
    default value for missing keys). The audit treats defaultdict() as
    unbounded regardless of the factory argument, matching the rule for
    ``set``/``dict``/``list`` constructors.
    """
    src = (
        "from collections import defaultdict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = defaultdict(list)\n"
    )
    v = _audit(tmp_path, src)
    assert v, "defaultdict(list) MUST be flagged -- factory does NOT bound the dict"
    assert any(_category(vh) == "unbounded_accumulator" for vh in v)


def test_ordered_dict_with_marker_not_flagged(tmp_path: Path) -> None:
    """``# bounded-accumulator-ok: <cap>`` MUST suppress OrderedDict violations.

    Real production sites that have a manual FIFO cap
    (``OrderedDict`` + ``popitem(last=False)`` eviction policy in the
    code) carry an inline marker naming the cap constant. The marker is
    the only escape for OrderedDict / defaultdict because these types
    have no built-in ``maxlen`` kwarg.
    """
    src = (
        "from collections import OrderedDict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = OrderedDict()  # bounded-accumulator-ok: FIFO cap _MAX=32\n"
    )
    assert not _audit(tmp_path, src), (
        "OrderedDict() with # bounded-accumulator-ok marker MUST NOT be flagged"
    )


def test_defaultdict_with_marker_not_flagged(tmp_path: Path) -> None:
    """``# bounded-accumulator-ok: <cap>`` MUST suppress defaultdict violations."""
    src = (
        "from collections import defaultdict\n"
        "class C:\n"
        "    def __init__(self) -> None:\n"
        "        self._x = defaultdict()  # bounded-accumulator-ok: capped by external eviction\n"
    )
    assert not _audit(tmp_path, src), (
        "defaultdict() with # bounded-accumulator-ok marker MUST NOT be flagged"
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
        'import threading\nthreading.Thread(target=lambda: None, daemon="yes")\n',
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
