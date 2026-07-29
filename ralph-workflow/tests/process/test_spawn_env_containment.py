"""Containment coverage for spawn-capable process entry points."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

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
    **dict.fromkeys(
        (
            "ralph/testing/audit_activity_aware_watchdog.py",
            "ralph/testing/audit_agent_internal_paths.py",
            "ralph/testing/audit_agent_module_state.py",
            "ralph/testing/audit_agent_registry_sync.py",
            "ralph/testing/audit_artifact_submission_canonical_path.py",
            "ralph/testing/audit_cast_policy.py",
            "ralph/testing/audit_di_seam.py",
            "ralph/testing/audit_fenced_artifact_examples.py",
            "ralph/testing/audit_fsevents_watch_consolidation.py",
            "ralph/testing/audit_idempotent_write_adoption.py",
            "ralph/testing/audit_lint_bypass.py",
            "ralph/testing/audit_log_sink_buffering.py",
            "ralph/testing/audit_mcp_timeout.py",
            "ralph/testing/audit_parallelization_dormant.py",
            "ralph/testing/audit_public_docstrings.py",
            "ralph/testing/audit_repo_structure.py",
            "ralph/testing/audit_resource_lifecycle.py",
            "ralph/testing/audit_skill_auto_commit.py",
            "ralph/testing/audit_template_render_integrity.py",
            "ralph/testing/audit_terminal_escape_containment.py",
            "ralph/testing/audit_test_policy.py",
            "ralph/testing/audit_typecheck_bypass.py",
            "ralph/testing/audit_watchdog_drift.py",
        ),
        "AST-only audit CLI; no process spawn seam",
    ),
}


def _module_path(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).with_suffix("").as_posix().replace("/", ".")


def _parsed_modules() -> dict[str, ast.Module]:
    script_paths = {module.replace(".", "/") + ".py" for module in _script_modules()}
    modules: dict[str, ast.Module] = {}
    for path in RALPH_ROOT.rglob("*.py"):
        relative_path = path.relative_to(PACKAGE_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        if relative_path in script_paths or 'if __name__ == "__main__":' in source:
            modules[relative_path] = ast.parse(source)
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
        and (node.module == "ralph.executor.process" or node.module.startswith("ralph.process.manager"))
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


def _script_modules() -> set[str]:
    scripts = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    return {str(target).partition(":")[0] for target in scripts.values()}


def test_spawn_capable_entry_points_sanitize_before_work() -> None:
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

    discovered_modules = _script_modules()
    for path in spawn_guard_paths:
        discovered_modules.add(_module_path(PACKAGE_ROOT / path))

    checked_entries: set[str] = set()
    for module_name in sorted(discovered_modules):
        path = module_name.replace(".", "/") + ".py"
        tree = modules[path]
        functions = _function_definitions(tree)
        entry_names = _guard_entry_names(tree) if _has_main_guard(tree) else {"main"}
        if module_name in _script_modules() and "main" in functions:
            entry_names.add("main")
        for entry_name in entry_names:
            if entry_name not in functions:
                continue
            function = functions[entry_name]
            assert _calls_sanitizer(_first_statement(function)), (
                f"{module_name}.{entry_name} must call sanitize_process_environment first"
            )
            checked_entries.add(f"{module_name}.{entry_name}")

    assert checked_entries == {
        "ralph.cli._prompt_helper_entry.main",
        "ralph.cli.main.main",
        "ralph.install.main",
        "ralph.mcp.server.runtime.main",
        "ralph.test_suites.main",
        "ralph.verify.main",
        "ralph.verify_timeout.main",
    }
