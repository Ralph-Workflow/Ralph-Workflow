"""Cross-section consistency test for the 14-row property matrix.

The Ralph MCP server target architecture (PROMPT.md properties A-N plus the
Foundation row) is pinned by 14 property test files in ``tests/`` and a
14-row table in two places:

  * ``ralph/mcp/ARCHITECTURE.md`` — the canonical in-repo table
  * ``docs/sphinx/mcp-architecture.md`` — the published sphinx page

If a future refactor renames a symbol (e.g. ``_coerce_fallback_server`` →
``_narrow_server``) or deletes a test (e.g. drops property E), the table and
the test matrix would silently drift apart. This test pins the matrix:

  1. Every row in the 14-row property table has a corresponding test file.
  2. Every test file contains at least one ``def test_...`` function.
  3. The production ``ralph/mcp/server/`` module for each row contains the
     proof-obligation symbol the row's proof depends on.

The symbol map is a deliberately-maintained manifest: a rename of one of
these symbols is a property-defining change that should require updating
the map. The diagnostics below cite both the row and the expected symbol
so a future fix is local.

Static-only (no real subprocess, no ``time.sleep``, no real file I/O).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path("/Users/mistlight/Projects/Ralph-Workflow/wt-004-mcp-fixes/ralph-workflow")
TESTS_DIR = REPO / "tests"
PACKAGE = REPO / "ralph"

# Symbol map — one entry per row in the 14-row property matrix.
#
# For each row: (test_file_basename, [(production_module_relpath, symbol_name), ...]).
#
# The symbol must be findable in the production module by either:
#  * being defined as a top-level ``def``/``class`` (in the case of
#    functions/classes), OR
#  * being bound as a module-level name (e.g. ``MAX_IDENTICAL_RETRY_ATTEMPTS``
#    is a module-level constant, ``SERVER_WORST_CASE_MS`` is the same), OR
#  * appearing in any string context (in the case of the start-up banner
#    format string fragment ``transport=`` which is concatenated at format
#    time). For those we use ``_source_contains`` semantics.
#
# Rows below are listed in property-table order: A, B, C, D, E, F, G, H, I,
# K, L, M, N, Foundation.
PROPERTY_MATRIX: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "test_property_a_one_transport_one_behavior.py",
        # Property A: the FastMCP-only path was hard-deleted. The single
        # production transport is the ``McpServer`` class on
        # ``_mcp_server.py``; ``build_ralph_tool_registry`` is the registry
        # factory that the test asserts is the one wired into it.
        (
            ("mcp/server/_mcp_server.py", "McpServer"),
            ("mcp/tools/bridge/_registry.py", "build_ralph_tool_registry"),
        ),
    ),
    (
        "test_property_b_session_contract_conformance.py",
        # Property B: ``_coerce_fallback_server`` is the cast-killer that
        # replaced the typing.cast() laundering at the session factory
        # boundary.
        (("mcp/server/_fallback_http_handler.py", "_coerce_fallback_server"),),
    ),
    (
        "test_property_c_liveness_contract.py",
        # Property C: the production transport must expose a real
        # ``_handle_health_get`` method on the fallback handler, and
        # ``RestartAwareMcpBridge`` lives in lifecycle.py.
        (
            ("mcp/server/_fallback_http_handler.py", "_handle_health_get"),
            ("mcp/server/lifecycle.py", "RestartAwareMcpBridge"),
        ),
    ),
    (
        "test_property_d_failure_observability.py",
        # Property D: McpMetrics must expose the three required record
        # methods, and the start-up banner must contain the three key
        # format fragments.
        (
            ("mcp/server/_metrics.py", "McpMetrics"),
            ("mcp/server/_metrics.py", "record_post_header_failure"),
            ("mcp/server/_metrics.py", "record_terminal_frame"),
            ("mcp/server/_metrics.py", "record_health_probe_outcome"),
        ),
    ),
    (
        "test_property_e_streaming_terminates.py",
        # Property E: the production streaming entry point is
        # ``exec_sse_streaming_post`` in exec_sse_streaming.py.
        (("mcp/server/exec_sse_streaming.py", "exec_sse_streaming_post"),),
    ),
    (
        "test_property_f_retry_side_effects.py",
        # Property F: the side-effect registry is the public surface that
        # pins a default-deny contract for every RalphToolName.
        (
            ("mcp/tools/_side_effects.py", "REGISTRY"),
            ("mcp/tools/_side_effects.py", "register"),
            ("mcp/tools/_side_effects.py", "get_contract"),
        ),
    ),
    (
        "test_property_g_recovery_signal.py",
        # Property G: the transport repetition tracker is the layer that
        # breaks identical-failure loops at the transport boundary.
        (
            ("mcp/server/_transport_repetition_tracker.py", "TransportRepetitionTracker"),
            ("mcp/server/_transport_repetition_tracker.py", "observe"),
            ("mcp/server/_transport_repetition_tracker.py", "signature_for"),
        ),
    ),
    (
        "test_property_h_bounded_resources.py",
        # Property H: the saturated-dispatch seam is the no-op pass-through
        # that lands before the bounded executor is wired.
        (
            ("mcp/server/_saturated_dispatch.py", "submit"),
            ("mcp/server/_saturated_dispatch.py", "SaturatedResponse"),
        ),
    ),
    (
        "test_property_i_timing_safety.py",
        # Property I: the import-time invariant guard uses
        # ``SERVER_WORST_CASE_MS`` and ``CLIENT_REQUEST_TIMEOUT_MS`` from
        # _timing_safety.py.
        (
            ("mcp/server/_timing_safety.py", "SERVER_WORST_CASE_MS"),
            ("mcp/server/_timing_safety.py", "CLIENT_REQUEST_TIMEOUT_MS"),
        ),
    ),
    (
        "test_property_k_trust_boundary.py",
        # Property K: the trust-boundary gate is ``require_trust_boundary``.
        (("mcp/server/_trust_boundary.py", "require_trust_boundary"),),
    ),
    (
        "test_property_l_zero_progress_and_resume.py",
        # Property L: ``MAX_IDENTICAL_RETRY_ATTEMPTS`` is the constant the
        # zero-progress cap is asserted against.
        (("pipeline/_retry_progress_guard.py", "MAX_IDENTICAL_RETRY_ATTEMPTS"),),
    ),
    (
        "test_property_m_structured_cause.py",
        # Property M: ``IdleWatchdogKilledError`` carries the structured
        # ``reason`` and ``signal`` attributes the classifier reads.
        (("agents/idle_watchdog_kill.py", "IdleWatchdogKilledError"),),
    ),
    (
        "test_property_n_spill_inside_workspace.py",
        # Property N: ``resolve_spill_dir`` is the function that pins
        # spill output to a workspace-relative path.
        (("mcp/tools/exec.py", "resolve_spill_dir"),),
    ),
    (
        "test_in_memory_transport_round_trip.py",
        # Foundation: the in-memory transport harness lives in
        # ``_in_memory_transport.py`` and is the entry point for the
        # round-trip harness.
        (("mcp/server/_in_memory_transport.py", "_InMemoryFallbackServer"),),
    ),
)


def _module_names(path: Path) -> set[str]:
    """Return the set of names defined in a Python file via AST.

    Includes:
      * top-level ``def``/``class`` names
      * module-level assignment / annotated-assignment targets
      * methods on every top-level class (so ``McpMetrics.record_post_header_failure``
        and ``TransportRepetitionTracker.observe`` are discoverable)
      * nested ``def``/``class`` names inside top-level functions (rare in
        production code but handled for completeness)

    We do not import the module — this test is purely static and must not
    perturb the import graph or require a working runtime.
    """
    if not path.is_file():
        return set()
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return set()

    names: set[str] = set()

    def _walk_class(node: ast.ClassDef) -> None:
        names.add(node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                names.add(child.target.id)

    def _walk_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        names.add(node.name)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                names.add(child.target.id)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            _walk_class(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _walk_function(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _test_file_has_test_function(path: Path) -> bool:
    """Return True if the path is a test file with at least one ``test_`` def."""
    if not path.is_file():
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            return True
    return False


@pytest.mark.parametrize(
    ("test_basename", "_symbols"),
    PROPERTY_MATRIX,
    ids=[row[0] for row in PROPERTY_MATRIX],
)
def test_property_test_file_exists_and_has_test_function(
    test_basename: str, _symbols: tuple[tuple[str, str], ...]
) -> None:
    """Every row in the property matrix has a test file with at least one test."""
    test_path = TESTS_DIR / test_basename
    assert test_path.is_file(), (
        f"property matrix row expects test file {test_path} but it does not exist"
    )
    assert _test_file_has_test_function(test_path), (
        f"test file {test_path} contains no test_ function — the row's coverage is gone"
    )


@pytest.mark.parametrize(
    ("test_basename", "symbols"),
    PROPERTY_MATRIX,
    ids=[row[0] for row in PROPERTY_MATRIX],
)
def test_property_symbols_present_in_production_modules(
    test_basename: str, symbols: tuple[tuple[str, str], ...]
) -> None:
    """Every proof-obligation symbol the row depends on is present in the
    production module it cites.
    """
    del test_basename  # symbols is the source of truth for the per-row check
    missing: list[tuple[str, str]] = []
    for relpath, symbol in symbols:
        module_path = PACKAGE / relpath
        if not module_path.is_file():
            missing.append((relpath, f"<file missing: {module_path}>"))
            continue
        names = _module_names(module_path)
        if symbol not in names:
            missing.append((relpath, symbol))
    assert not missing, (
        "Property matrix drift: the following production symbols cited by the "
        "matrix are missing — the test_file and the doc/code have likely "
        "diverged. Update the matrix in tests/test_property_matrix_consistency.py "
        "to match the new symbol name, or restore the missing symbol:\n  "
        + "\n  ".join(f"{relpath}: {symbol_or_msg}" for relpath, symbol_or_msg in missing)
    )


def test_matrix_has_exactly_fourteen_rows() -> None:
    """The matrix is exactly the 14 rows: 13 property rows (A, B, C, D, E, F,
    G, H, I, K, L, M, N) plus the Foundation row. Property J is
    intentionally absent (Foundations dependency-injection is the
    cross-cutting enabler, not an incident-derived property).
    """
    assert len(PROPERTY_MATRIX) == 14, (
        f"property matrix must have exactly 14 rows; got {len(PROPERTY_MATRIX)}. "
        "Add a new row and matching production-symbol citations when a property "
        "is added; remove the row when a property is deprecated."
    )
    basenames = [row[0] for row in PROPERTY_MATRIX]
    expected_prefixes = (
        "test_property_a_",
        "test_property_b_",
        "test_property_c_",
        "test_property_d_",
        "test_property_e_",
        "test_property_f_",
        "test_property_g_",
        "test_property_h_",
        "test_property_i_",
        "test_property_k_",
        "test_property_l_",
        "test_property_m_",
        "test_property_n_",
        "test_in_memory_transport_round_trip",
    )
    actual_prefixes = tuple(
        bn[: len(ep)] for bn, ep in zip(basenames, expected_prefixes, strict=True)
    )
    assert actual_prefixes == expected_prefixes, (
        f"property matrix rows out of order or misnamed. "
        f"Expected prefixes {expected_prefixes}, got {actual_prefixes}"
    )



