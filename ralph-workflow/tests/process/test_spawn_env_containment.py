"""Containment coverage for spawn-capable process entry points."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[2]
RALPH_ROOT = PACKAGE_ROOT / "ralph"

# These guards either delegate to a wired composition root or deliberately do
# not reach a process spawn seam. New guarded modules must be classified here
# or discovered as a wired entry point below.
_GUARD_ALLOWLIST: dict[str, str] = {
    "ralph/__main__.py": "delegates to ralph.cli.main.main",
    "ralph/main.py": "delegates to ralph.cli.main.app",
    "ralph/mcp/server/__main__.py": "delegates to ralph.mcp.server.runtime.main",
    "ralph/contrib/cla.py": "urllib-only contributor helper",
    "ralph/git/hardening.py": "pragma-no-cover smoke runner",
    "ralph/mcp/explore/reindex_bench.py": (
        "in-process benchmark CLI; reindex runs inside the current "
        "process (no subprocess spawn), so the guard is safe to keep."
    ),
    "ralph/mcp/explore/bench.py": (
        "S-1 product-baseline CLI; representative flows run in-process "
        "through real MCP handlers (no subprocess spawn), so the guard "
        "is safe to keep."
    ),
    **dict.fromkeys(
        (
            "ralph/testing/audit_activity_aware_watchdog.py",
            "ralph/testing/audit_agent_internal_paths.py",
            "ralph/testing/audit_agent_module_state.py",
            "ralph/testing/audit_agent_registry_sync.py",
            "ralph/testing/audit_artifact_submission_canonical_path.py",
            "ralph/testing/audit_cast_policy.py",
            "ralph/testing/audit_canonical_session_text.py",
            "ralph/testing/audit_di_seam.py",
            "ralph/testing/audit_fenced_artifact_examples.py",
            "ralph/testing/audit_filesystem_polling_invocation.py",
            "ralph/testing/audit_filesystem_read_consolidation.py",
            "ralph/testing/audit_filesystem_write_consolidation.py",
            "ralph/testing/audit_fsevents_watch_consolidation.py",
            "ralph/testing/audit_filesystem_polling_invocation.py",
            "ralph/testing/audit_lint_bypass.py",
            "ralph/testing/audit_log_sink_buffering.py",
            "ralph/testing/audit_mcp_timeout.py",
            "ralph/testing/audit_parallelization_dormant.py",
            "ralph/testing/audit_public_docstrings.py",
            "ralph/testing/audit_prompt_single_sourcing.py",
            "ralph/testing/audit_repo_structure.py",
            "ralph/testing/audit_regression_test_elimination.py",
            "ralph/testing/audit_resource_lifecycle.py",
            "ralph/testing/audit_skill_auto_commit.py",
            "ralph/testing/audit_template_render_integrity.py",
            "ralph/testing/audit_terminal_escape_containment.py",
            "ralph/testing/audit_test_policy.py",
            "ralph/testing/audit_typecheck_bypass.py",
            "ralph/testing/audit_watchdog_drift.py",
            "ralph/testing/audit_workspace_resource_inventory.py",
            "ralph/testing/audit_appearance_assertion_prohibition.py",
        ),
        "AST-only audit CLI; no process spawn seam",
    ),
}


def _module_path(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).with_suffix("").as_posix().replace("/", ".")


def _parsed_modules() -> dict[str, ast.Module]:
    script_paths = {module.replace(".", "/") + ".py" for module in _script_module_names()}
    modules: dict[str, ast.Module] = {}
    main_guard_markers = (
        b'if __name__ == "__main__":',
        b"if __name__ == '__main__':",
    )
    for path in RALPH_ROOT.rglob("*.py"):
        relative_path = path.relative_to(PACKAGE_ROOT).as_posix()
        raw = path.read_bytes()
        if relative_path not in script_paths and not any(
            marker in raw for marker in main_guard_markers
        ):
            continue
        modules[relative_path] = ast.parse(raw.decode("utf-8"))
    return modules


def _is_main_guard(node: ast.If) -> bool:
    return (
        isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and len(node.test.ops) == len(node.test.comparators) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _has_main_guard(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.If) and _is_main_guard(node) for node in tree.body)


def _imports_spawn_seam(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module is not None
        and (
            node.module == "ralph.executor.process"
            or node.module.startswith("ralph.process.manager")
        )
        for node in ast.walk(tree)
    )


def _function_definitions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _guard_entry_names(tree: ast.Module) -> set[str]:
    return {
        name
        for guard in tree.body
        if isinstance(guard, ast.If) and _is_main_guard(guard)
        for call in ast.walk(guard)
        if isinstance(call, ast.Call)
        if (name := _called_name(call)) is not None
    }


def _first_statement(function: ast.FunctionDef) -> ast.stmt:
    statements = function.body
    if (
        isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    while (
        isinstance(statements[0], ast.If)
        and not statements[0].orelse
        and all(isinstance(node, ast.Raise) for node in statements[0].body)
    ):
        statements = statements[1:]
    return statements[0]


def _calls_sanitizer(statement: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Call) and _called_name(node) == "sanitize_process_environment"
        for node in ast.walk(statement)
    )


def _string_keyed_mapping(value: object, label: str) -> dict[str, object]:
    assert isinstance(value, dict), f"{label} must be a mapping"
    result: dict[str, object] = {}
    for key, item in value.items():
        assert isinstance(key, str), f"{label} keys must be strings"
        result[key] = item
    return result


def _script_targets() -> dict[str, set[str]]:
    parsed: object = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = _string_keyed_mapping(parsed, "pyproject")
    scripts = _string_keyed_mapping(project["project"], "project")
    targets: dict[str, set[str]] = {}
    for target in _string_keyed_mapping(scripts["scripts"], "project.scripts").values():
        module, separator, attribute = str(target).partition(":")
        assert separator and module and attribute, f"invalid console-script target: {target!r}"
        targets.setdefault(module, set()).add(attribute)
    return targets


def _script_module_names() -> set[str]:
    return set(_script_targets())


def _callback_entry_name(tree: ast.Module, attribute: str) -> str | None:
    """Resolve ``app.callback()(main)``-style console-script targets."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Call):
            continue
        callback = node.func
        if not isinstance(callback.func, ast.Attribute) or callback.func.attr != "callback":
            continue
        if not isinstance(callback.func.value, ast.Name) or callback.func.value.id != attribute:
            continue
        if len(node.args) == 1 and isinstance(node.args[0], ast.Name):
            return node.args[0].id
    return None


def _declared_entry_names(tree: ast.Module, module_name: str, attributes: set[str]) -> set[str]:
    functions = _function_definitions(tree)
    entry_names: set[str] = set()
    for attribute in attributes:
        if attribute in functions:
            entry_names.add(attribute)
            continue
        callback_name = _callback_entry_name(tree, attribute)
        assert callback_name is not None, (
            f"{module_name}:{attribute} must resolve to a function or callback"
        )
        assert callback_name in functions, (
            f"{module_name}:{attribute} callback must resolve to a module-level function"
        )
        entry_names.add(callback_name)
    return entry_names


@pytest.mark.timeout_seconds(5)
def test_spawn_capable_entry_points_sanitize_before_work() -> None:
    """Full-package entry-point sweep; 5s covers parallel-load I/O on slow disks."""
    modules = _parsed_modules()
    guard_paths = {path for path, tree in modules.items() if _has_main_guard(tree)}
    assert all(_GUARD_ALLOWLIST.values())
    spawn_guard_paths = {
        path
        for path, tree in modules.items()
        if _has_main_guard(tree) and _imports_spawn_seam(tree)
    }
    unclassified_guards = (
        guard_paths - set(_GUARD_ALLOWLIST) - {"ralph/cli/main.py"} - spawn_guard_paths
    )
    assert not unclassified_guards, unclassified_guards

    script_targets = _script_targets()
    discovered_modules = set(script_targets)
    for path in spawn_guard_paths:
        discovered_modules.add(_module_path(PACKAGE_ROOT / path))

    checked_entries: set[str] = set()
    for module_name in sorted(discovered_modules):
        path = module_name.replace(".", "/") + ".py"
        tree = modules[path]
        functions = _function_definitions(tree)
        entry_names = (
            _guard_entry_names(tree).intersection(functions) if _has_main_guard(tree) else set()
        )
        if attributes := script_targets.get(module_name):
            entry_names.update(_declared_entry_names(tree, module_name, attributes))
        assert entry_names, f"{module_name} has no resolvable entry function"
        for entry_name in entry_names:
            assert entry_name in functions, f"{module_name}.{entry_name} must be module-level"
            function = functions[entry_name]
            assert _calls_sanitizer(_first_statement(function)), (
                f"{module_name}.{entry_name} must call sanitize_process_environment first"
            )
            checked_entries.add(f"{module_name}.{entry_name}")

    assert checked_entries == {
        "ralph.cli.main.main",
        "ralph.install.main",
        "ralph.mcp.server.runtime.main",
        "ralph.test_suites.main",
        "ralph.verify.main",
        "ralph.verify_timeout.main",
    }


def test_spawn_env_containment_regression_checks_declared_script_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DA-001: a non-``main`` console-script target cannot evade the sweep."""
    fake_module = "ralph.fake_b"
    monkeypatch.setattr(
        __import__(__name__, fromlist=["_script_targets"]),
        "_script_targets",
        lambda: {fake_module: {"launch"}},
    )
    monkeypatch.setattr(
        __import__(__name__, fromlist=["_parsed_modules"]),
        "_parsed_modules",
        lambda: {
            "ralph/fake_b.py": ast.parse(
                "from ralph.executor.process import run_process\n\n"
                "def launch():\n"
                "    run_process('child')\n"
            )
        },
    )

    with pytest.raises(AssertionError, match=r"ralph\.fake_b\.launch"):
        test_spawn_capable_entry_points_sanitize_before_work()
