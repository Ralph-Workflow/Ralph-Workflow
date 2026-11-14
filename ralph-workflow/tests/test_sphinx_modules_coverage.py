from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "module docstring floor": "documented public modules now carry no module docstring",
}
_RALPH_ROOT = Path(__file__).parent.parent / "ralph"
_MODULES_RST = Path(__file__).parent.parent / "docs" / "sphinx" / "modules.rst"

_EXCLUDED: dict[str, str] = {
    "pipeline.auto_integrate_resolution_state": "internal integration-state helper, not public API",
    "pipeline.integration_resolution_status": "internal integration verdict enum, not public API",
    "pipeline.integration_resolution_types": "internal integration verdict value, not public API",
    "testing.fake_process": "test infrastructure, not public API",
    "agents.catalog": "internal injectable registry, not public API",
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
    "git.rebase.legacy_checkpoint": "internal legacy-checkpoint classification seam, not public API",
    "display.phase_activity_counts": "internal display type, not public API",
    "display.phase_entry_model": "internal display model, not public API",
    "display.phase_exit_model": "internal display model, not public API",
    "display.pipeline_snapshot": "internal display type, not public API",
    "display.plan_summary": "internal display type, not public API",
    "display.surface_catalog": "internal display metadata, not public API",
    "display.worker_snapshot": "internal display type, not public API",
    "exit_pause.exit_outcome": "internal exit type, not public API",
    "exit_pause.pause_on_exit_mode": "internal exit type, not public API",
    "git.conflict_stage_entries": "internal git merge stage helper, not public API",
    "git.hardening": "internal git hardening, not public API",
    "git.git_run_result": "internal git type, not public API",
    "git.operation_snapshot": "internal git type, not public API",
    "git.rebase.process_result": "internal git type, not public API",
    "git.rebase.rebase_conflicts": "internal git type, not public API",
    "git.rebase.rebase_no_op": "internal git type, not public API",
    "git.rebase.rebase_operation_error": "internal git type, not public API",
    "git.rebase.rebase_success": "internal git type, not public API",
    "git.rebase.subprocess_executor": "internal git type, not public API",
    "install_transaction": "internal installer transaction helper, not public API",
    "mcp.artifacts.analysis_item_proof": "internal artifact type, not public API",
    "mcp.artifacts.development_result_continuation": "internal artifact type, not public API",
    "mcp.artifacts.development_result_validation_error": "internal artifact type, not public API",
    "mcp.artifacts.plan_item_proof": "internal artifact type, not public API",
    "mcp.artifacts.smoke_test_result_validation_error": "internal artifact type, not public API",
    "mcp.upstream.upstream_config_error": "internal upstream type, not public API",
    "workspace.probe": "internal workspace change observation seam protocol and default probe, not public API",
    "mcp.upstream.upstream_tool": "internal upstream type, not public API",
    "phases.commit_attempt_log": "internal phases type, not public API",
    "phases.phase_context": "internal phases type, not public API",
    "phases.phase_timing_record": "internal phases type, not public API",
    "policy.validation.artifact_contract": "internal artifact contract validation helper, not public API",
    "pipeline.agent_chain_state": "internal pipeline state, not public API",
    "pipeline.auto_integrate_catalog_rationales": "internal catalog rationale registry, not public API",
    "pipeline.auto_integrate_recovery_legacy": "internal recovery legacy helper, not public API",
    "pipeline.auto_integrate_recovery_lock": "internal lock-liveness helper, not public API",
    "pipeline.auto_integrate_recovery_record": "internal recovery record helper, not public API",
    "pipeline.auto_integrate_remote_client": "internal remote client helper, not public API",
    "pipeline.auto_integrate_remote_constants": "internal remote constants helper, not public API",
    "pipeline.auto_integrate_remote_push": "internal remote push helper, not public API",
    "pipeline.auto_integrate_remote_reconcile": "internal remote reconciliation helper, not public API",
    "pipeline.auto_integrate_worktree_state": "internal worktree-state probe, not public API",
    "pipeline.agent_execution_deps": "internal pipeline type, not public API",
    "pipeline.agent_recovery_input": "internal pipeline type, not public API",
    "pipeline.agent_recovery_plan": "internal pipeline type, not public API",
    "pipeline.artifact_handoff_context": "internal pipeline type, not public API",
    "pipeline.commit_executor": "internal pipeline type, not public API",
    "pipeline.commit_state": "internal pipeline type, not public API",
    "pipeline.fallover_record": "internal pipeline type, not public API",
    "pipeline.integration_strategy": "internal pipeline type, not public API",
    "pipeline.parallel.parallel_coordinator": "internal pipeline type, not public API",
    "pipeline.plumbing": "internal pipeline plumbing submodule, not public API",
    "pipeline.resolution_outcome": "internal pipeline type, not public API",
    "pipeline.session_bridge": "internal pipeline DI helper, not public API",
    "pipeline.parallel.worker_context": "internal pipeline type, not public API",
    "pipeline.parallel.worker_failure_error": "internal pipeline type, not public API",
    "pipeline.parallel.worker_log": "internal pipeline type, not public API",
    "pipeline.parallel.worker_session_bundle": "internal pipeline type, not public API",
    "pipeline.rebase_state": "internal pipeline type, not public API",
    "pipeline.run_metrics": "internal pipeline type, not public API",
    "pipeline.verification_result": "internal pipeline type, not public API",
    "prompts.developer.developer_prompt_inputs": "internal prompts type, not public API",
    "prompts.template_not_found_error": "internal prompts type, not public API",
    "prompts.commit_review": "internal prompts type, not public API",
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
    "testing.policy_allowlists": "test infrastructure, not public API",
    "testing.whitebox_detectors": "test infrastructure, not public API",
    "testing.whitebox_reconciliation": "test infrastructure, not public API",
    "testing.test_whitebox_baseline": "test infrastructure, not public API",
    "testing.audit_repo_structure": "test infrastructure, not public API",
    "testing.audit_regression_test_elimination": "test infrastructure, not public API",
    "testing.audit_integration_no_legacy": "test infrastructure, not public API",
    "testing.audit_integration_single_source": "test infrastructure, not public API",
    "testing.audit_integration_swallow": "test infrastructure, not public API",
    "testing.audit_lint_bypass": "test infrastructure, not public API",
    "testing.audit_typecheck_bypass": "test infrastructure, not public API",
    "testing.audit_mcp_timeout": "test infrastructure, not public API",
    "testing.audit_canonical_session_text": "test infrastructure, not public API",
    "testing.audit_activity_aware_watchdog": "test infrastructure, not public API",
    "testing.audit_agent_registry_sync": "test infrastructure, not public API",
    "testing.audit_agent_module_state": "test infrastructure, not public API",
    "testing.audit_agent_internal_paths": "test infrastructure, not public API",
    "testing.audit_skill_auto_commit": "test infrastructure, not public API",
    "testing.audit_artifact_skill_types": "test infrastructure, not public API",
    "testing.audit_fsevents_watch_consolidation": "test infrastructure, not public API",
    "testing.audit_filesystem_polling_invocation": "test infrastructure, not public API",
    "testing.audit_log_sink_buffering": "test infrastructure, not public API",
    "testing._audit_scope": "test infrastructure, not public API",
}
_EXCLUDED_PREFIXES = tuple(f"{name}." for name in _EXCLUDED)

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


@cache
def _walk_public_modules_and_packages(root: Path, prefix: str = "") -> tuple[str, ...]:
    results: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("_"):
            continue

        if entry.suffix == ".py":
            if entry.name == "__main__.py":
                continue
            name = prefix + entry.stem if prefix else entry.stem
            results.append(name)
        elif entry.is_dir():
            if (entry / "__init__.py").exists():
                child_prefix = prefix + entry.name + "." if prefix else entry.name + "."
                results.append(prefix + entry.name if prefix else entry.name)
                results.extend(_walk_public_modules_and_packages(entry, child_prefix))
    return tuple(results)


def _extract_documented_modules(modules_rst_text: str) -> set[str]:
    documented: set[str] = set()

    for directive in re.findall(r"^\.\. automodule:: (.+)$", modules_rst_text, re.MULTILINE):
        module_name = directive.strip()
        if module_name.startswith("ralph."):
            documented.add(module_name[len("ralph.") :])
        else:
            documented.add(module_name)

    for rst_line in modules_rst_text.splitlines():
        line = rst_line.rstrip()
        if line.startswith(".. ") or line.startswith("   ") or line.startswith("\t"):
            continue
        if line.startswith(":") or line.startswith(".."):
            continue
        if re.match(r"^[\w./]+$", line) and ("." in line or "/" in line):
            normalised = line.replace("/", ".").strip(".")
            if normalised.startswith("ralph."):
                normalised = normalised[len("ralph.") :]
            if normalised and normalised not in _TOP_LEVEL_SECTION_HEADERS:
                documented.add(normalised)

    return documented


def test_all_public_modules_and_packages_covered_in_modules_rst() -> None:
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    documented = _extract_documented_modules(modules_rst_text)

    all_names: list[str] = _walk_public_modules_and_packages(_RALPH_ROOT)

    def is_excluded(name: str) -> bool:
        return name in _EXCLUDED or name.startswith(_EXCLUDED_PREFIXES)

    public_names = [n for n in all_names if not is_excluded(n)]

    policy_disagreements: list[str] = []
    for excluded_name, reason in _EXCLUDED.items():
        if excluded_name in documented:
            policy_disagreements.append(
                f"  ralph.{excluded_name} is documented in modules.rst but _EXCLUDED says: {reason}"
            )
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

    missing = [name for name in sorted(public_names) if name not in documented]

    assert not missing, (
        "The following public modules/packages are missing from "
        "docs/sphinx/modules.rst:\n"
        + "\n".join(f"  ralph.{name}" for name in missing)
        + "\n\nAdd corresponding entries to modules.rst and update _EXCLUDED "
        "in this test if the module is intentionally private."
    )


def _resolve_to_source(rel_name: str, ralph_root: Path) -> Path | None:
    parts = rel_name.split(".")
    leaf = ralph_root.joinpath(*parts[:-1], parts[-1] + ".py")
    if leaf.exists():
        return leaf
    pkg = ralph_root.joinpath(*parts, "__init__.py")
    if pkg.exists():
        return pkg
    return None


@cache
def _ast_module_docstring(source_path: Path) -> str:
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
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    documented = _extract_documented_modules(modules_rst_text)
    all_names = _walk_public_modules_and_packages(_RALPH_ROOT)

    def is_excluded(name: str) -> bool:
        return name in _EXCLUDED or name.startswith(_EXCLUDED_PREFIXES)

    public_names = {n for n in all_names if not is_excluded(n)}
    return sorted(documented & public_names)


def _has_module_docstring(source: Path) -> bool:
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("from __future__"):
            continue
        return stripped[0] in {'"', "'"}
    return False


def _collect_documented_public_modules_with_docstrings() -> tuple[str, ...]:
    offending: list[str] = []
    for rel_name in _documented_public_targets():
        source = _resolve_to_source(rel_name, _RALPH_ROOT)
        if source is None:
            continue
        if _has_module_docstring(source):
            offending.append(f"  ralph.{rel_name}  ({source.relative_to(_RALPH_ROOT.parent)})")
    return tuple(offending)


_DOCUMENTED_PUBLIC_MODULES_WITH_DOCSTRINGS: tuple[str, ...] = (
    _collect_documented_public_modules_with_docstrings()
)


def test_documented_public_modules_have_no_module_docstrings() -> None:
    offending = _DOCUMENTED_PUBLIC_MODULES_WITH_DOCSTRINGS

    assert not offending, (
        "The following documented public modules/packages carry a module "
        "docstring; source files carry no docstrings:\n" + "\n".join(offending)
    )


def test_all_documented_autodoc_targets_resolve_to_real_source() -> None:
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
    modules_rst_text = _MODULES_RST.read_text(encoding="utf-8")
    assert "package map in ``ralph-workflow/README.md``" not in modules_rst_text, (
        "docs/sphinx/modules.rst still contains the stale claim that it mirrors "
        "a README package map. Remove that sentence."
    )
