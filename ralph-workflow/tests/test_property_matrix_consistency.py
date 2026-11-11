from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO / "tests"
PACKAGE = REPO / "ralph"

PROPERTY_MATRIX: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "test_property_a_one_transport_one_behavior.py",
        (
            ("mcp/server/_mcp_server.py", "McpServer"),
            ("mcp/tools/bridge/_registry.py", "build_ralph_tool_registry"),
        ),
    ),
    (
        "test_property_b_session_contract_conformance.py",
        (("mcp/server/_fallback_http_handler.py", "_coerce_fallback_server"),),
    ),
    (
        "test_property_c_liveness_contract.py",
        (
            ("mcp/server/_fallback_http_handler.py", "_handle_health_get"),
            ("mcp/server/lifecycle.py", "RestartAwareMcpBridge"),
        ),
    ),
    (
        "test_property_d_failure_observability.py",
        (
            ("mcp/server/_metrics.py", "McpMetrics"),
            ("mcp/server/_metrics.py", "record_post_header_failure"),
            ("mcp/server/_metrics.py", "record_terminal_frame"),
            ("mcp/server/_metrics.py", "record_health_probe_outcome"),
        ),
    ),
    (
        "test_property_e_streaming_terminates.py",
        (("mcp/server/exec_sse_streaming.py", "exec_sse_streaming_post"),),
    ),
    (
        "test_property_f_retry_side_effects.py",
        (
            ("mcp/tools/_side_effects.py", "REGISTRY"),
            ("mcp/tools/_side_effects.py", "register"),
            ("mcp/tools/_side_effects.py", "get_contract"),
        ),
    ),
    (
        "test_property_g_recovery_signal.py",
        (
            ("mcp/server/_transport_repetition_tracker.py", "TransportRepetitionTracker"),
            ("mcp/server/_transport_repetition_tracker.py", "observe"),
            ("mcp/server/_transport_repetition_tracker.py", "signature_for"),
        ),
    ),
    (
        "test_property_h_bounded_resources.py",
        (
            ("mcp/server/_saturated_dispatch.py", "submit"),
            ("mcp/server/_saturated_dispatch.py", "SaturatedResponse"),
        ),
    ),
    (
        "test_property_i_timing_safety.py",
        (
            ("mcp/server/_timing_safety.py", "SERVER_WORST_CASE_MS"),
            ("mcp/server/_timing_safety.py", "CLIENT_REQUEST_TIMEOUT_MS"),
        ),
    ),
    (
        "test_property_k_trust_boundary.py",
        (("mcp/server/_trust_boundary.py", "require_trust_boundary"),),
    ),
    (
        "test_property_l_zero_progress_and_resume.py",
        (
            ("recovery/agent_failure_ledger.py", "AgentFailureLedger"),
            ("recovery/retry_prompt.py", "build_retry_error_block"),
        ),
    ),
    (
        "test_property_m_structured_cause.py",
        (("agents/idle_watchdog_kill.py", "IdleWatchdogKilledError"),),
    ),
    (
        "test_property_n_spill_inside_workspace.py",
        (("mcp/tools/exec.py", "resolve_spill_dir"),),
    ),
    (
        "test_in_memory_transport_round_trip.py",
        (("mcp/server/_in_memory_transport.py", "_InMemoryFallbackServer"),),
    ),
)


def _module_names(path: Path) -> set[str]:
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
    del test_basename
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
