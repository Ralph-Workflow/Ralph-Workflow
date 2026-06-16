"""Test that every public ralph module and package is covered in Sphinx autodoc."""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"

# Modules and packages intentionally excluded from autodoc coverage, with reasons.
# These are internal/helper modules that should not appear in the public API reference.
_EXCLUDED: dict[str, str] = {
    "testing.fake_process": "test infrastructure, not public API",
    # Internal event/model types not intended as public API
    "agents.catalog": "internal injectable registry, not public API",
    "agents.builtin": "internal declarative source of truth, not public API",
    "agents.invocation_context": "internal DI seam, not public API",
    "agents.post_exit_verdict": "internal watchdog verdict type, not public API",
    "agents.registration": "internal registration seam, opt-in import only; not public API",
    "agents.spec": "internal agent spec type, not public API",
    "agents.support": "internal agent support type, not public API",
    "api.model_entry": "internal API model entry, not public API",
    "cli.commands.smoke_run_params": "internal CLI parameter type, not public API",
    "diagnostics.agent_diagnostics": "internal diagnostics type, not public API",
    "diagnostics.agent_status": "internal diagnostics type, not public API",
    "diagnostics.system_info": "internal diagnostics type, not public API",
    "display.agent_activity_event": "internal display event type, not public API",
    "display.budget_progress": "internal display type, not public API",
    "display.event_options": "internal display type, not public API",
    "display.exit_context": "internal display context, not public API",
    "display.phase_activity_counts": "internal display type, not public API",
    "display.phase_entry_model": "internal display model, not public API",
    "display.phase_exit_model": "internal display model, not public API",
    "display.pipeline_snapshot": "internal display type, not public API",
    "display.plan_summary": "internal display type, not public API",
    "display.worker_snapshot": "internal display type, not public API",
    "exit_pause.exit_outcome": "internal exit type, not public API",
    "exit_pause.pause_on_exit_mode": "internal exit type, not public API",
    "git.git_run_result": "internal git type, not public API",
    "git.rebase.process_result": "internal git type, not public API",
    "git.rebase.rebase_conflicts": "internal git type, not public API",
    "git.rebase.rebase_no_op": "internal git type, not public API",
    "git.rebase.rebase_operation_error": "internal git type, not public API",
    "git.rebase.rebase_success": "internal git type, not public API",
    "git.rebase.subprocess_executor": "internal git type, not public API",
    "mcp.artifacts.analysis_item_proof": "internal artifact type, not public API",
    "mcp.artifacts.development_result_continuation": "internal artifact type, not public API",
    "mcp.artifacts.development_result_validation_error": "internal artifact type, not public API",
    "mcp.artifacts.plan.plan_artifact_validation_error": "internal artifact type, not public API",
    "mcp.artifacts.plan_item_proof": "internal artifact type, not public API",
    "mcp.artifacts.smoke_test_result_validation_error": "internal artifact type, not public API",
    "mcp.upstream.upstream_config_error": "internal upstream type, not public API",
    "mcp.upstream.upstream_tool": "internal upstream type, not public API",
    "phases.commit_attempt_log": "internal phases type, not public API",
    "phases.phase_context": "internal phases type, not public API",
    "phases.phase_timing_record": "internal phases type, not public API",
    "pipeline.agent_chain_state": "internal pipeline state, not public API",
    "pipeline.agent_execution_deps": "internal pipeline type, not public API",
    "pipeline.agent_recovery_input": "internal pipeline type, not public API",
    "pipeline.agent_recovery_plan": "internal pipeline type, not public API",
    "pipeline.artifact_handoff_context": "internal pipeline type, not public API",
    "pipeline.commit_executor": "internal pipeline type, not public API",
    "pipeline.commit_state": "internal pipeline type, not public API",
    "pipeline.fallover_record": "internal pipeline type, not public API",
    "pipeline.parallel.parallel_coordinator": "internal pipeline type, not public API",
    "pipeline.plumbing": "internal pipeline plumbing submodule, not public API",
    "pipeline.session_bridge": "internal pipeline DI helper, not public API",
    "pipeline.parallel.worker_context": "internal pipeline type, not public API",
    "pipeline.parallel.worker_failure_error": "internal pipeline type, not public API",
    "pipeline.parallel.worker_log": "internal pipeline type, not public API",
    "pipeline.parallel.worker_session_bundle": "internal pipeline type, not public API",
    "pipeline.rebase_state": "internal pipeline type, not public API",
    "pipeline.run_metrics": "internal pipeline type, not public API",
    "pipeline.verification_result": "internal pipeline type, not public API",
    "pydantic_compat": "internal Pydantic compat shim; re-exports cause Sphinx autodoc warnings",
    "prompts.developer.developer_prompt_inputs": "internal prompts type, not public API",
    "prompts.template_not_found_error": "internal prompts type, not public API",
    "prompts.commit_cleanup": "internal prompts type, not public API",
    "recovery.agent_unavailability_tracker": "internal recovery type, not public API",
    "recovery.connectivity_event": "internal recovery type, not public API",
    "recovery.connectivity_state": "internal recovery type, not public API",
    "recovery.failure_classifier": "internal recovery type, not public API",
    "recovery.failure_event": "internal recovery type, not public API",
    "recovery.fallover_event": "internal recovery type, not public API",
    "recovery.recovery_controller_options": "internal recovery type, not public API",
    "recovery.unavailability_reason": "internal recovery type, not public API",
    "testing.fake_run": "test infrastructure, not public API",
    "testing.audit_test_policy": "test infrastructure, not public API",
    "testing.audit_lint_bypass": "test infrastructure, not public API",
    "testing.audit_typecheck_bypass": "test infrastructure, not public API",
    "testing.audit_mcp_timeout": "test infrastructure, not public API",
    "testing.audit_activity_aware_watchdog": "test infrastructure, not public API",
    "testing.audit_agent_registry_sync": "test infrastructure, not public API",
    "testing.audit_agent_module_state": "test infrastructure, not public API",
    "test_suites": "internal test runner helper, not public API",
}

_TOP_LEVEL_SECTION_HEADERS = frozenset(
    {
        "Top-Level",
        "CLI",
        "Config",
        "Policy",
        "Pipeline",
        "Phases",
        "Agents",
        "MCP",
        "Git",
        "Workspace",
        "Recovery",
        "Runtime",
        "Process",
        "API",
        "Utilities",
        "Testing",
    }
)


def _walk_public_modules_and_packages(root: Path, prefix: str = "") -> list[str]:
    """Walk root and return all public module/package names.

    A public name is a directory with __init__.py or a .py file (not starting
    with _).  Names are returned as dot-separated qualified paths relative to
    the root (e.g. "mcp.server", "mcp.server.factory").
    """
    results: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("_"):
            continue

        if entry.suffix == ".py":
            # Leaf module (but not __main__.py which is entry-point only)
            if entry.name == "__main__.py":
                continue
            name = prefix + entry.stem if prefix else entry.stem
            results.append(name)
        elif entry.is_dir():
            if (entry / "__init__.py").exists():
                # Package: recurse into it
                child_prefix = prefix + entry.name + "." if prefix else entry.name + "."
                results.append(prefix + entry.name if prefix else entry.name)
                results.extend(_walk_public_modules_and_packages(entry, child_prefix))
    return results


def _extract_documented_modules(modules_rst_text: str) -> set[str]:
    """Extract module names from modules.rst RST source.

    Handles two forms:
      - ``.. automodule:: ralph.foo.bar``  (explicit automodule directive)
      - RST section-title lines like "ralph.mcp.server" or "ralph/mcp/server"
        that represent documented module headings.
    """
    documented: set[str] = set()

    # 1. Explicit automodule directives
    for directive in re.findall(r"^\.\. automodule:: (.+)$", modules_rst_text, re.MULTILINE):
        module_name = directive.strip()
        # Normalise ralph.mcp -> mcp, ralph.mcp.server -> mcp.server
        if module_name.startswith("ralph."):
            documented.add(module_name[len("ralph.") :])
        else:
            documented.add(module_name)

    # 2. RST section-title lines that look like module paths.
    #    A title like "ralph.mcp.server" or "ralph/mcp/server" under the
    #    "API Reference" heading represents documentation for that module.
    #    We identify them by matching dotted or slash-separated Ralph paths.
    for rst_line in modules_rst_text.splitlines():
        line = rst_line.rstrip()
        # Skip directive lines
        if line.startswith(".. ") or line.startswith("   ") or line.startswith("\t"):
            continue
        # Skip RST field lists and comments
        if line.startswith(":") or line.startswith(".."):
            continue
        # A bare module-like title (letters, dots, slashes, underscores)
        if re.match(r"^[\w./]+$", line) and ("." in line or "/" in line):
            # Normalise slashes to dots and strip ralph. prefix
            normalised = line.replace("/", ".").strip(".")
            if normalised.startswith("ralph."):
                normalised = normalised[len("ralph.") :]
            if normalised and normalised not in _TOP_LEVEL_SECTION_HEADERS:
                documented.add(normalised)

    return documented


def test_all_public_modules_and_packages_covered_in_modules_rst() -> None:
    """Every public Python module/package under ralph/ must appear in modules.rst.

    This test inventories the complete public surface of the ralph package
    (all packages and leaf modules, excluding private/_-prefixed names and
    intentionally internal namespaces listed in _EXCLUDED) and verifies each
    has a corresponding entry in docs/sphinx/modules.rst.

    Entries in _EXCLUDED are checked for consistency: if modules.rst documents
    a name that is also in _EXCLUDED, the test fails to catch the policy
    disagreement.
    """
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    documented = _extract_documented_modules(modules_rst_text)

    # Build the full public surface
    all_names: list[str] = _walk_public_modules_and_packages(_RALPH_ROOT)

    # Filter out excluded namespaces
    def is_excluded(name: str) -> bool:
        return any(name == excluded or name.startswith(excluded + ".") for excluded in _EXCLUDED)

    public_names = [n for n in all_names if not is_excluded(n)]

    # Check for excluded modules that are still documented (policy disagreement)
    policy_disagreements: list[str] = []
    for excluded_name, reason in _EXCLUDED.items():
        # Check both the top-level package and any nested modules
        if excluded_name in documented:
            policy_disagreements.append(
                f"  ralph.{excluded_name} is documented in modules.rst but _EXCLUDED says: {reason}"
            )
        # Also check nested modules of excluded packages
        policy_disagreements.extend(
            f"  ralph.{doc_name} is documented in modules.rst "
            f"but parent '{excluded_name}' is _EXCLUDED: {reason}"
            for doc_name in documented
            if doc_name.startswith(excluded_name + ".")
        )

    assert not policy_disagreements, (
        "Policy disagreement: modules.rst documents modules that are marked "
        "as intentionally undocumented in _EXCLUDED:\n"
        + "\n".join(policy_disagreements)
        + "\n\nEither remove these entries from modules.rst or remove them "
        "from _EXCLUDED."
    )

    # Find undocumented public modules
    missing = [name for name in sorted(public_names) if name not in documented]

    assert not missing, (
        "The following public modules/packages are missing from "
        "docs/sphinx/modules.rst:\n"
        + "\n".join(f"  ralph.{name}" for name in missing)
        + "\n\nAdd corresponding entries to modules.rst and update _EXCLUDED "
        "in this test if the module is intentionally private."
    )


def _resolve_to_source(rel_name: str, ralph_root: Path) -> Path | None:
    """Map a documented ralph submodule name to its backing source file.

    ``rel_name`` is relative to ``ralph`` (e.g. ``mcp.server.factory``).
    Returns the leaf ``.py`` path for modules or the ``__init__.py`` for packages.
    Returns ``None`` if neither exists.
    """
    parts = rel_name.split(".")
    # Try as leaf module
    leaf = ralph_root.joinpath(*parts[:-1], parts[-1] + ".py")
    if leaf.exists():
        return leaf
    # Try as package
    pkg = ralph_root.joinpath(*parts, "__init__.py")
    if pkg.exists():
        return pkg
    return None


@cache
def _ast_module_docstring(source_path: Path) -> str:
    """Return the top-level module docstring from a source file, or empty string."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        return tree.body[0].value.value
    return ""


@cache
def _documented_public_targets() -> list[str]:
    """Return documented module names (relative to ralph) that are also public surface.

    This is the intersection of the modules.rst documented set and the names
    produced by the public surface walker, so __main__ modules and explicitly
    excluded names are not included.
    """
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    documented = _extract_documented_modules(modules_rst_text)
    all_names = _walk_public_modules_and_packages(_RALPH_ROOT)

    def is_excluded(name: str) -> bool:
        return any(name == excluded or name.startswith(excluded + ".") for excluded in _EXCLUDED)

    public_names = {n for n in all_names if not is_excluded(n)}
    return sorted(documented & public_names)


# Pre-populate caches at module import time so file I/O and AST parsing
# happen before the per-test SIGALRM window is set up.
for _tgt in _documented_public_targets():
    _src = _resolve_to_source(_tgt, _RALPH_ROOT)
    if _src is not None:
        _ast_module_docstring(_src)


def test_documented_public_modules_have_non_empty_docstrings() -> None:
    """Every public module/package documented in modules.rst must have a docstring.

    Uses AST-based inspection so the check is import-free and exhaustive.
    """
    missing: list[str] = []
    for rel_name in _documented_public_targets():
        source = _resolve_to_source(rel_name, _RALPH_ROOT)
        if source is None:
            continue  # covered by coverage test
        docstring = _ast_module_docstring(source)
        if not docstring.strip():
            missing.append(f"  ralph.{rel_name}  ({source.relative_to(_RALPH_ROOT.parent)})")

    assert not missing, (
        "The following documented public modules/packages have no top-level "
        "module docstring (pydoc-first contract):\n"
        + "\n".join(missing)
        + "\n\nAdd a module docstring to each listed source file."
    )


def test_all_documented_autodoc_targets_resolve_to_real_source() -> None:
    """Every automodule target in modules.rst must resolve to a real source file.

    Each ``.. automodule:: ralph.*`` entry must map to either a ``.py`` module
    file or a package ``__init__.py`` in the ralph source tree.  Phantom targets
    left over from older designs (e.g. ralph.phases.development) must be removed.
    """
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    phantom: list[str] = []
    for directive in re.findall(r"^\.\.\s+automodule::\s+(.+)$", modules_rst_text, re.MULTILINE):
        module_name = directive.strip()
        if not module_name.startswith("ralph."):
            continue
        rel_name = module_name[len("ralph.") :]
        source = _resolve_to_source(rel_name, _RALPH_ROOT)
        if source is None:
            phantom.append(f"  {module_name}")

    assert not phantom, (
        "The following autodoc targets in docs/sphinx/modules.rst do not "
        "resolve to any real source module or package:\n"
        + "\n".join(phantom)
        + "\n\nRemove or replace each stale entry with a real maintained module."
    )


def test_modules_rst_has_no_stale_readme_package_map_claim() -> None:
    """modules.rst must not claim to mirror a README package map."""
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    assert "package map in ``ralph-workflow/README.md``" not in modules_rst_text, (
        "docs/sphinx/modules.rst still contains the stale claim that it mirrors "
        "a README package map. Remove that sentence."
    )
