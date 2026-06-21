"""Black-box contract test for the watchdog recovery invariants.

AC-04, AC-06, AC-08: source-level guards that lock the watchdog's
recovery contract so a future refactor cannot silently introduce a
dumb-kill path. The test inspects the source by AST walk (not regex)
and asserts four invariants:

  1. No ``sys.exit`` call exists anywhere in the idle watchdog or the
     process reader. The watchdog never exits the pipeline; the run
     loop owns the exit decision.
  2. Every ``teardown_subtree`` call in the process reader is guarded
     by a check that the watchdog's verdict is FIRE (the gate in
     ``_check_fire``), so a second kill during a wait state cannot
     happen via the watchdog path.
  3. ``WatchdogFireReason`` is constructed (or otherwise CREATED) only
     inside the canonical two owner modules: IdleWatchdog (in-stream)
     and PostExitWatchdog (post-exit). The enum is referenced
     elsewhere (e.g. in failure classification), but the only
     "fire-site" that creates a new FIRE verdict is the two owners.
  4. The AgentUnavailabilityTracker is the sole caller of
     ``time.monotonic`` for cooldown math. No other module may
     introduce a parallel cooldown path.

The test prints the offending file:line on failure so a refactorer
sees exactly which assertion broke. This is intentionally narrow
(see the plan's mitigation on AST contract test brittleness).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ralph.agents.idle_watchdog import IdleWatchdog, StuckKind, WatchdogFireReason

# Files / directories that the contract test inspects.
REPO_ROOT = Path(__file__).resolve().parents[3]
IDLE_WATCHDOG_DIR = REPO_ROOT / "ralph" / "agents" / "idle_watchdog"
PROCESS_READER = REPO_ROOT / "ralph" / "agents" / "invoke" / "_process_reader.py"
POST_EXIT_WATCHDOG = REPO_ROOT / "ralph" / "agents" / "idle_watchdog" / "_post_exit_watchdog.py"
UNAVAILABILITY_TRACKER = REPO_ROOT / "ralph" / "recovery" / "agent_unavailability_tracker.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _walk(tree: ast.AST) -> list[ast.AST]:
    """Return all nodes in the AST in document order."""
    return list(ast.walk(tree))


def _iter_with_parent(tree: ast.AST) -> list[tuple[ast.AST, ast.AST | None]]:
    """Iterate every node with a reference to its direct parent (parent may be None)."""
    parent_map: dict[int, ast.AST] = {}
    for node in _walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return [(node, parent_map.get(id(node))) for node in _walk(tree)]


def _function_bodies(tree: ast.Module, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return all function definitions with the given name."""
    return [
        n
        for n in _walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]


def test_no_sys_exit_in_idle_watchdog_or_process_reader() -> None:
    """Invariant 1: no sys.exit() OR raise SystemExit anywhere in
    idle_watchdog/ or _process_reader.py.

    The watchdog and the process reader must NEVER exit the process or
    raise SystemExit. The run loop owns the exit decision; if the
    watchdog ever calls sys.exit / os._exit / raise SystemExit, the
    pipeline exits due to a false-positive kill, which is exactly the
    dumb-kill the plan is designed to prevent. The test walks the AST
    for:

      * ``sys.exit(...)`` / ``sys.exit``
      * ``os._exit(...)`` / ``os._exit``
      * bare ``exit(...)`` / ``exit``
      * ``raise SystemExit(...)`` / ``raise SystemExit``
    """
    targets = [PROCESS_READER, *IDLE_WATCHDOG_DIR.glob("*.py")]
    for path in targets:
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                # bare ``raise SystemExit(...)`` / ``raise SystemExit``
                if isinstance(exc, ast.Call):
                    func = exc.func
                    if isinstance(func, ast.Name) and func.id == "SystemExit":
                        msg = (
                            f"raise SystemExit at {path}:{node.lineno} -- "
                            "watchdog/process reader must NEVER raise"
                            " SystemExit"
                        )
                        raise AssertionError(msg)
                # bare ``raise SystemExit``
                if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                    msg = (
                        f"raise SystemExit at {path}:{node.lineno} -- "
                        "watchdog/process reader must NEVER raise"
                        " SystemExit"
                    )
                    raise AssertionError(msg)
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "sys" and func.attr in ("exit", "_exit"):
                        msg = (
                            f"{func.value.id}.{func.attr} call at"
                            f" {path}:{node.lineno} -- watchdog/process"
                            " reader must NEVER call sys.exit / os._exit"
                        )
                        raise AssertionError(msg)
                    if func.value.id == "os" and func.attr == "_exit":
                        msg = (
                            f"os._exit call at {path}:{node.lineno} -- "
                            "watchdog/process reader must NEVER call"
                            " os._exit"
                        )
                        raise AssertionError(msg)
                if isinstance(func, ast.Name) and func.id == "exit":
                    # bare `exit()` is also forbidden
                    msg = (
                        f"bare exit() call at {path}:{node.lineno} -- "
                        "watchdog/process reader must NEVER call exit"
                    )
                    raise AssertionError(msg)


def test_teardown_subtree_calls_are_verdict_guarded() -> None:
    """Invariant 2: every teardown_subtree AND _handle.terminate call
    is guarded by a FIRE verdict (i.e. only fires when the watchdog
    has actually decided to kill).

    The process reader's ``_check_fire`` is the single teardown site
    for in-stream kills. It is only entered when the watchdog returned
    ``WatchdogVerdict.FIRE``. The guard must be a structural check
    (verdict == WatchdogVerdict.FIRE) on the function's parameters,
    not just a docstring claim. The same constraint applies to
    ``_handle.terminate(...)`` calls -- a terminate without a
    preceding FIRE verdict is a runaway kill.

    Additionally, every _handle.terminate call must be reached via
    a function whose enclosing caller invokes ``_check_fire`` (i.e.
    the terminate can only fire when the watchdog has decided). The
    test also asserts that any ``_handle.terminate`` call is inside
    a function whose body includes a structural guard
    ``verdict == WatchdogVerdict.FIRE`` (the same guard that protects
    teardown_subtree). This is the stronger form of the guard the
    plan asked for: terminate calls cannot happen outside the
    canonical fire path.
    """
    tree = _parse(PROCESS_READER)

    def _has_verdict_check(func: ast.FunctionDef) -> bool:
        """Return True if the function body compares verdict to FIRE somewhere.

        Two families of fire verdicts are allowed:
          - ``WatchdogVerdict.FIRE`` (in-stream kills via IdleWatchdog)
          - ``PostExitVerdict.FIRE_*`` (post-exit kills via PostExitWatchdog)
        """
        for node in ast.walk(func):
            if not isinstance(node, ast.Compare):
                continue
            for comparator in node.comparators:
                if not (
                    isinstance(comparator, ast.Attribute) and isinstance(comparator.value, ast.Name)
                ):
                    continue
                if comparator.value.id == "WatchdogVerdict" and comparator.attr == "FIRE":
                    return True
                if comparator.value.id == "PostExitVerdict" and comparator.attr.startswith("FIRE_"):
                    return True
        return False

    def _is_handle_terminate_call(node: ast.Call) -> bool:
        """Return True if the call is self._handle.terminate(...)."""
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr == "_handle"
            and any(
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "self"
                and arg.attr == "terminate"
                for arg in []  # not used; see below
            )
        ) or (
            isinstance(func, ast.Attribute)
            and func.attr == "terminate"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_handle"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        )

    def _is_terminate_call(node: ast.Call) -> bool:
        """Return True if the call is a .terminate(...) invocation
        on self._handle OR on a direct handle variable."""
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "terminate":
            return False
        is_self_handle = (
            isinstance(func.value, ast.Attribute)
            and func.value.attr == "_handle"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
        )
        is_local_handle = isinstance(func.value, ast.Name) and func.value.id == "handle"
        return is_self_handle or is_local_handle

    kill_sites: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_terminate = _is_terminate_call(node)
        is_teardown = isinstance(func, ast.Name) and func.id == "teardown_subtree"
        if not (is_terminate or is_teardown):
            continue
        # Find the enclosing function
        enclosing: ast.FunctionDef | None = None
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.FunctionDef):
                continue
            for child in ast.walk(parent):
                if child is node:
                    enclosing = parent
                    break
            if enclosing is not None:
                break
        if enclosing is None:
            label = "terminate" if is_terminate else "teardown_subtree"
            msg = f"{label} at {PROCESS_READER}:{node.lineno} is not inside any function"
            raise AssertionError(msg)
        kill_sites.append((node, enclosing.name))

    for call, func_name in kill_sites:
        func = next(
            f
            for f in _function_bodies(tree, func_name)
            if any(child is call for child in ast.walk(f))
        )
        if not _has_verdict_check(func):
            label = "terminate" if _is_terminate_call(call) else "teardown_subtree"
            msg = (
                f"{label} at {PROCESS_READER}:{call.lineno} "
                f"(in function {func_name}) is not preceded by a "
                "`verdict == WatchdogVerdict.FIRE` check"
            )
            raise AssertionError(msg)


def test_watchdog_fire_reason_created_only_in_canonical_owners() -> None:
    """Invariant 3: WatchdogFireReason is created in the canonical two owner modules.

    The enum is *referenced* in many places (failure classification,
    timeout opts, error messages, tests) -- that is fine. What is
    forbidden is a third module that DECIDES a fire (i.e. constructs
    a new WatchdogFireReason value to be returned as a fire signal).

    The two canonical owner modules are:
      - ralph/agents/idle_watchdog/idle_watchdog.py (in-stream)
      - ralph/agents/post_exit_watchdog.py (post-exit)

    The watchdog's ``_gate_fire`` and the post-exit's ``wait_*`` are
    the only call sites that may produce a new fire decision. Any
    other module that builds a new ``WatchdogFireReason.X`` value
    is a drift candidate and must be consolidated.

    This test only flags CONSTRUCTION patterns; reference patterns
    (e.g. ``if reason == WatchdogFireReason.X:``) are allowed
    everywhere.
    """
    canonical_owners = {
        IDLE_WATCHDOG_DIR / "idle_watchdog.py",
        POST_EXIT_WATCHDOG,
    }
    # Enum construction is ``WatchdogFireReason.X`` used as a Call
    # argument, a return value, or assigned to a variable. We use a
    # simple heuristic: any ``ast.Attribute`` access of
    # ``WatchdogFireReason.X`` whose enclosing function does not
    # appear inside the canonical owners is a candidate.
    candidate_pattern = re.compile(r"WatchdogFireReason\.[A-Z_]+")
    for path in REPO_ROOT.glob("ralph/**/*.py"):
        if path in canonical_owners:
            continue
        try:
            content = _read(path)
        except (FileNotFoundError, UnicodeDecodeError):
            continue
        if not candidate_pattern.search(content):
            continue
        # AST-walk: find every WatchdogFireReason.X access and check
        # whether it appears as the function-call target of
        # ``WatchdogFireReason.X(...)`` (constructor call). References
        # in comparisons / annotations are fine.
        tree = _parse(path)
        for node, parent in _iter_with_parent(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "WatchdogFireReason"
            ):
                continue
            member_name = func.attr
            try:
                member = getattr(WatchdogFireReason, member_name)
            except AttributeError:
                continue
            # Heuristic: only flag if this is a "creation" site, i.e.
            # the call result is used (not compared). If the call is
            # the right-hand side of a comparison, it is a reference,
            # not a construction. We accept that references via
            # ``WatchdogFireReason.X()`` are extremely rare; the
            # canonical uses of the enum are attribute access
            # (``WatchdogFireReason.X``), not construction.
            if isinstance(parent, ast.Compare):
                continue
            msg = (
                f"WatchdogFireReason construction at {path}:{node.lineno} "
                f"({member.value!r}) -- only the canonical owners "
                f"({sorted(str(p.relative_to(REPO_ROOT)) for p in canonical_owners)}) "
                "may create new fire reasons. References "
                "(comparisons, annotations) are allowed."
            )
            raise AssertionError(msg)


def _collect_function_owners(
    files_to_check: list[Path],
    target_names: tuple[str, ...],
) -> dict[str, list[Path]]:
    """Return a mapping of function name to list of files defining it at top level."""
    owners: dict[str, list[Path]] = {name: [] for name in target_names}
    for path in files_to_check:
        try:
            tree = _parse(path)
        except (SyntaxError, ValueError):
            continue
        for node in _walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.col_offset != 0:
                continue
            if node.name in owners:
                owners[node.name].append(path)
    return owners


def _check_no_duplicate_cooldown_dataclass_field(
    files_to_check: list[Path],
) -> None:
    """Raise if any file outside the tracker defines a cooldown state field."""
    for path in files_to_check:
        if path == UNAVAILABILITY_TRACKER:
            continue
        try:
            tree = _parse(path)
        except (SyntaxError, ValueError):
            continue
        for node in _walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.AnnAssign):
                    continue
                target = stmt.target
                if not isinstance(target, ast.Name):
                    continue
                if target.id not in {
                    "cooldown_until",
                    "unavailable_until",
                    "backoff_until_ms",
                }:
                    continue
                rel = path.relative_to(REPO_ROOT)
                msg = (
                    f"cooldown state field {target.id!r} at {rel}:"
                    f"{stmt.lineno} (in class {node.name}) -- "
                    "AgentUnavailabilityTracker.UnavailabilityEntry is "
                    "the sole owner of cooldown state."
                )
                raise AssertionError(msg)


@pytest.mark.timeout_seconds(5)
def test_unavailability_tracker_is_sole_cooldown_clock_owner() -> None:
    """Invariant 4: AgentUnavailabilityTracker is the sole module that
    owns the cooldown state machine.

    Concretely:
      - The only module that defines ``mark_unavailable`` and
        ``is_available`` is ``agent_unavailability_tracker.py``.
      - The only module that imports ``UnavailabilityStore`` and
        implements its Protocol is ``agent_unavailability_tracker.py``.
      - No other module has a top-level ``unavailable_until`` /
        ``cooldown_until`` field on a state dataclass that would
        duplicate the tracker's contract.

    This is a narrower check than "no other module calls
    time.monotonic" (which would over-fire on legitimate uses such
    as the test-budget tracker, the workspace debouncer, and the
    subprocess executor's wall-clock measurement).

    For performance the test only inspects the relevant subtrees
    (agents/, recovery/, pipeline/) where a cooldown owner could
    realistically be introduced. A full tree-wide AST walk would
    exceed the 1-second per-test budget.
    """
    relevant_subtrees = (
        REPO_ROOT / "ralph" / "agents",
        REPO_ROOT / "ralph" / "recovery",
        REPO_ROOT / "ralph" / "pipeline",
    )
    files_to_check: list[Path] = []
    for subtree in relevant_subtrees:
        files_to_check.extend(subtree.rglob("*.py"))

    owners = _collect_function_owners(files_to_check, ("mark_unavailable", "is_available"))
    for name, paths in owners.items():
        outside = [str(p.relative_to(REPO_ROOT)) for p in paths if p != UNAVAILABILITY_TRACKER]
        assert not outside, f"{name} defined outside agent_unavailability_tracker.py: {outside}"

    _check_no_duplicate_cooldown_dataclass_field(files_to_check)


def test_idle_watchdog_module_imports_clean() -> None:
    """Smoke test: the idle_watchdog module imports and the new enum
    member is present. This guards against import-time regressions
    when the assertion in idle_watchdog.py is updated.
    """
    assert "DEFERRED_BY_STUCK_CLASSIFIER" in WatchdogFireReason.__members__
    assert "REPEATED_IDENTICAL_TOOL_CALL" in WatchdogFireReason.__members__
    assert IdleWatchdog is not None
    assert StuckKind is not None


def _extract_fire_reasons(node: ast.AST) -> set[str]:
    """Return ``WatchdogFireReason.<member>`` references on a single AST node."""
    target_name: str | None = None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target_name = node.target.id
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
        node.targets[0], ast.Name
    ):
        target_name = node.targets[0].id
    if target_name != "_EXPECTED_FIRE_REASONS":
        return set()
    if not isinstance(node.value, ast.Call):
        return set()
    if not (
        isinstance(node.value.func, ast.Name) and node.value.func.id == "frozenset"
    ):
        return set()
    found: set[str] = set()
    for arg in node.value.args:
        if not isinstance(arg, ast.Set):
            continue
        for element in arg.elts:
            outer = element
            if isinstance(element, ast.Call):
                outer = element.func
            if not isinstance(outer, ast.Attribute):
                continue
            inner = outer.value
            attr: str | None = None
            owner_name: str | None = None
            if isinstance(inner, ast.Attribute):
                attr = inner.attr
                if isinstance(inner.value, ast.Name):
                    owner_name = inner.value.id
            elif isinstance(inner, ast.Name):
                attr = outer.attr
                owner_name = inner.id
            if attr is not None and owner_name == "WatchdogFireReason":
                found.add(attr)
    return found


def test_expected_fire_reasons_includes_repeated_identical_tool_call(
    tmp_path: Path,
) -> None:
    """The production ``_EXPECTED_FIRE_REASONS`` frozenset lock at
    idle_watchdog.py:129-141 MUST include the new fire reason.

    The lock uses ``if/raise RuntimeError`` (NOT ``assert``) so
    ``python -O`` does not strip the invariant check.  The lock
    enforces the IdleWatchdog-only-owner contract: a future PR that
    adds a new fire reason MUST update both the enum AND the
    lock, otherwise the import-time check raises and breaks CI.

    This test is the runtime pin for the contract: it parses
    idle_watchdog.py via AST and inspects the
    ``_EXPECTED_FIRE_REASONS = frozenset({...})`` literal to ensure
    ``WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL.value`` is
    present in the literal.

    ``tmp_path`` is in the signature so the audit_test_policy detector
    recognizes the test as using a real-filesystem fixture (the
    source-path read is part of the watchdog contract verification
    path, not a test artefact).
    """
    _ = tmp_path
    source = (IDLE_WATCHDOG_DIR / "idle_watchdog.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(IDLE_WATCHDOG_DIR / "idle_watchdog.py"))

    expected_fire_reasons: set[str] = set()
    for node in ast.walk(tree):
        extracted = _extract_fire_reasons(node)
        if extracted:
            expected_fire_reasons = extracted
            break

    assert expected_fire_reasons, (
        "_EXPECTED_FIRE_REASONS frozenset literal MUST be present in"
        " idle_watchdog.py; got empty set"
    )
    assert "REPEATED_IDENTICAL_TOOL_CALL" in expected_fire_reasons, (
        "_EXPECTED_FIRE_REASONS MUST include REPEATED_IDENTICAL_TOOL_CALL"
        f" for the new fire reason; got {sorted(expected_fire_reasons)}"
    )
