"""WU-2 / E1.6: the lifecycle design note must describe the stores that exist.

The ``lifecycle`` module docstring is the design note for the
make-before-break bridge, and its "E1.6 Concurrency arguments across all 7
state stores" section is a set of factual claims about the real writer of
each store. Those claims used to be wrong: the note asserted that only the
parent bridge appends to the wire ledger and that child processes never
append to it, while the real writer of a dispatched frame is the CHILD
request-dispatch path in ``_mcp_server.py``.

Every assertion below is checked against the real code, not against the
prose: the prose is the thing under test, so a test that only re-read the
docstring would only ever confirm it still says what it said. The two
halves are therefore cross-checked — the note must name the mechanism AND
the mechanism must be the one the code actually implements.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import threading

from ralph.mcp.server import (
    _mcp_health_event,
    _mcp_server,
    _start_stage_tracker,
    _transport_repetition_tracker,
    _wire_ledger,
    lifecycle,
)

_E16_SECTION_HEADING = "E1.6 Concurrency arguments across all 8 state stores:"


def _design_note() -> str:
    """Return the lifecycle module docstring (the design note)."""
    doc = lifecycle.__doc__
    if doc is None:
        raise AssertionError("lifecycle module must carry its design-note docstring")
    return doc


def _e16_section() -> str:
    """Return just the E1.6 store section of the design note."""
    doc = _design_note()
    start = doc.find(_E16_SECTION_HEADING)
    if start < 0:
        raise AssertionError("design note must keep its E1.6 store section")
    end = doc.find("Attempt result classification", start)
    if end < 0:
        end = len(doc)
    return doc[start:end]


def _function_source(module: object, name: str) -> str:
    func: object = getattr(module, name)
    if not callable(func):
        raise AssertionError(f"{name!r} is not callable on {module!r}")
    return inspect.getsource(func)


def _calls_named(func_source: str, callee: str) -> bool:
    """Return True iff ``func_source`` contains a call to ``callee``."""
    tree = ast.parse(textwrap.dedent(func_source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == callee:
            return True
        if isinstance(func, ast.Attribute) and func.attr == callee:
            return True
    return False


# ---------------------------------------------------------------------------
# Ground truth: the code that actually writes each store.
# ---------------------------------------------------------------------------
def test_e16_child_dispatch_is_the_real_wire_ledger_writer() -> None:
    """Ground truth: the CHILD dispatch path appends to the wire ledger."""
    dispatch = _function_source(_mcp_server.McpServer, "_dispatch_request")
    assert _calls_named(dispatch, "_append_wire_record_best_effort"), (
        "child request dispatch must reach the wire ledger; if this ever stops "
        "holding the E1.6 note's writer statement must be revisited again"
    )
    helper = _function_source(_mcp_server.McpServer, "_append_wire_record_best_effort")
    assert _calls_named(helper, "append_wire_record")


def test_e16_wire_ledger_lock_is_advisory_and_reads_chain_head_under_it() -> None:
    """Ground truth: flock is taken before the chain head is read, released after."""
    tree = ast.parse(inspect.getsource(_wire_ledger))
    append_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "append_wire_record"
    )
    calls: dict[str, int] = {}
    for node in ast.walk(append_fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                name: str = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                continue
            calls[name] = calls.get(name, 0) + 1
    assert calls.get("flock", 0) >= 2, "append must take AND release the advisory lock"
    append_src = ast.unparse(append_fn)
    assert "_read_last_hmac" in append_src
    lock_line = append_src.index("fcntl.flock")
    head_line = append_src.index("_read_last_hmac(ledger_path)")
    append_line = append_src.index("'a'")
    assert lock_line < head_line < append_line, (
        "the chain head must be read under the lock, before the append"
    )


def test_e16_durable_sink_is_a_per_bridge_file_guarded_by_a_thread_lock() -> None:
    """Ground truth: the sink lock is a threading.Lock, not a cross-process lock."""
    assert isinstance(_mcp_health_event._DURABLE_SINK_LOCK, type(threading.Lock()))
    source = inspect.getsource(_mcp_health_event.configure_bridge_sink)
    assert "mcp-health-events-" in source


def test_e16_stage_files_are_stage_scoped_and_written_by_the_child() -> None:
    """Ground truth: stage file name carries the per-spawn stage id."""
    tracker_source = inspect.getsource(_start_stage_tracker.stage_file_path)
    assert "spawn_id" in tracker_source
    reset_source = inspect.getsource(_start_stage_tracker.reset_spawn_id)
    assert reset_source.strip() != ""


def test_e16_repetition_sidecar_is_a_locked_read_modify_rewrite() -> None:
    """Ground truth: the E1.7 sidecar is flock-guarded, not append-only JSONL."""
    tracker = _transport_repetition_tracker
    acquire = inspect.getsource(tracker._acquire_state_lock)
    assert "flock" in acquire
    observe = inspect.getsource(tracker.TransportRepetitionTracker._observe_with_carried_state)
    assert ".lock" in observe, "the carried state is guarded by an advisory sidecar"
    write = inspect.getsource(tracker.TransportRepetitionTracker._write_carried_state)
    assert ".open(" in write


# ---------------------------------------------------------------------------
# The design note must agree with that ground truth.
# ---------------------------------------------------------------------------
def test_e16_note_does_not_claim_children_never_append_to_the_ledger() -> None:
    section = _e16_section()
    assert "child processes never append" not in section
    assert "never append" not in section


def test_e16_note_names_the_real_wire_ledger_mechanism() -> None:
    section = _e16_section().lower()
    assert "_wire_ledger" in section, "the note must point at the module that owns the mechanism"
    assert "_mcp_server" in section, "the note must name the real child writer location"
    assert "flock" in section or "advisory" in section
    assert "chain head" in section, "the note must describe the read-under-lock chain head"
    assert "not cached in memory" in section or "never cached" in section


def test_e16_note_corrects_the_durable_sink_isolation_claim() -> None:
    section = _e16_section()
    assert "across all bridge instances by the dedicated" not in section
    assert "_DURABLE_SINK_LOCK" in section
    assert "thread" in section.lower(), "the lock is a threading.Lock, not a cross-process lock"
    assert "per-bridge" in section.lower() or "unique" in section.lower()


def test_e16_note_keeps_the_true_claims() -> None:
    section = _e16_section()
    # Stage scoping really is per-spawn; do not let a correction erase it.
    assert "MCP_STAGE_ID_ENV" in section
    # The session file really is parent-written and child-read.
    assert "session" in section.lower()
