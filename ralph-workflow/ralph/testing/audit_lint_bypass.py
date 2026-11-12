from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def _load_toml_root(path: Path) -> dict[str, object] | None:
    try:
        parsed_obj: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def _nested_mapping(root: dict[str, object], *keys: str) -> dict[str, object]:
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return {}
        mapping = cast("dict[str, object]", current)
        next_value = mapping.get(key)
        if next_value is None:
            return {}
        current = next_value
    if not isinstance(current, dict):
        return {}
    return cast("dict[str, object]", current)


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("dict[object, object]", value)
    return {
        raw_key: raw_value for raw_key, raw_value in mapping.items() if isinstance(raw_key, str)
    }


_NOQA_ALLOWLIST: set[tuple[str, str]] = {
    ("audit_test_policy", "PLR0911"),
    ("audit_test_policy", "PLW0603"),
    ("analysis_decision", "PLR0911"),
    ("commit_executor", "PLC0415"),
    ("runner", "PLC0415"),
    ("worker_runtime", "PLC0415"),
    ("commit_review", "PLC0415"),
    ("materialize", "PLC0415"),
    ("supervising", "PLC0415"),
    ("pytest_timeout_plugin", "PLC0415"),
    ("_event_classification", "PLC0415"),
    ("_host_contention_signal", "PLC0415"),
    ("_process_manager", "PLC0415"),
    ("run_loop", "PLC0415"),
    ("scoped_auto_commit", "PLC0415"),
    (
        "scoped_auto_commit",
        "PLR0911",
    ),
    (
        "scoped_auto_commit",
        "PLR0912",
    ),
    (
        "scoped_auto_commit",
        "PLR0915",
    ),
    (
        "scoped_auto_commit",
        "C420",
    ),
    ("preflight", "C420"),
    ("_run_start_setup", "PLC0415"),
    (
        "_run_start_setup",
        "PLR0912",
    ),
    ("init", "PLC0415"),
    (
        "manager",
        "PLC0415",
    ),
    (
        "bootstrap",
        "PLC0415",
    ),
    ("preflight", "PLC0415"),
    (
        "cli_integration",
        "PLC0415",
    ),
    ("_installer", "PLC0415"),
    (
        "_installer_candidates",
        "PLC0415",
    ),
    (
        "validators",
        "PLC0415",
    ),
    ("run_loop", "PLR0912"),
    ("run_loop", "PLR0915"),
    (
        "run_loop",
        "PLR0911",
    ),
    ("idle_watchdog", "PLR0911"),
    ("idle_watchdog", "PLR0912"),
    ("idle_watchdog", "PLR0915"),
    ("_active_branch", "PLR0911"),
    ("_fire_evaluators", "PLR0911"),
    ("_fire_evaluators", "PLR0912"),
    ("_waiting_branch", "PLR0911"),
    ("_waiting_branch", "PLR0912"),
    ("_waiting_branch", "PLR0915"),
    ("_stuck_classifier", "PLR0911"),
    (
        "parallel_display",
        "PLR0911",
    ),
    (
        "parallel_display",
        "PLR0912",
    ),
    ("heartbeat", "PLC0415"),
    ("canonical_submit", "PLC0415"),
    ("artifact", "PLC0415"),
    ("completion_signals", "PLC0415"),
    ("pydantic_validation_errors", "PLR0911"),
    ("failure_classifier", "PLR0911"),
    ("commit_plumbing", "UP047"),
    ("claude_interactive_transcript_parser", "PLR0911"),
    ("claude_interactive_transcript_parser", "PLR0912"),
    ("smoke_multimodal", "PLR0911"),
    ("smoke_multimodal", "PLR0912"),
    ("smoke_plumbing", "PLR0912"),
    ("mock_multimodal_agent", "PLR0912"),
    ("mock_multimodal_agent", "PLR0915"),
    ("_workspace", "PLC0415"),
    ("run", "PLC0415"),
    ("workspace_health", "PLC0415"),
    ("_metrics", "PLW0603"),
    ("_sentry", "PLW0603"),
    ("smoke_multimodal", "PLR0911"),
    ("parallel_display", "PLR0912"),
    ("pydantic_validation_errors", "PLR0911"),
    ("_command_builders", "PLC0415"),
    ("_runtime_resolvers", "PLC0415"),
    ("context", "PLC0415"),
    ("_media_io", "PLW0603"),
    ("_terminal_bg_query", "PLW0603"),
    ("_start_stage_tracker", "PLW0603"),
    ("_mcp_health_event", "PLW0603"),
    ("__init__", "PLC0415"),
    ("__init__", "RUF022"),
    ("catalog", "PLC0415"),
    ("registration", "UP037"),
    ("registration", "PLC0415"),
    ("registry", "PLC0415"),
    ("vision_agent_provisioning", "PLC0415"),
    ("catalog", "N802"),
    ("_factory", "PLC0415"),
    ("test_audit_agent_module_state", "PLC0415"),
    ("_auto_commit", "PLC0415"),
    ("audit_skill_auto_commit", "PLR0912"),
    ("plan", "PLR0912"),
    ("scoped_auto_commit", "PLC0415"),
    ("cli_integration", "PLC0415"),
    ("auto_integrate_recovery", "PLR0911"),
    ("auto_integrate_recovery", "PLR0912"),
    ("auto_integrate_remote_sync", "PLR0911"),
    ("loader", "PLR0912"),
    ("_direct_mcp_recovery", "PLR0912"),
    ("subscriber", "PLR0911"),
    ("_ndjson_base", "PLR0911"),
    ("teardown", "PLC0415"),
}

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".venv312",
        ".mypy_cache",
        "tmp",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
    }
)

_PYPROJECT_IGNORE_ALLOWLIST: dict[str, dict[str, object]] = {
    "RUF022": {
        "pattern": "ralph/agents/__init__.py",
        "reason": (
            "Discoverability: register_my_agent (the 90% recipe) must precede register_agent_support."
        ),
    },
    "PLR2004": {
        "pattern": [
            "tests/**/*.py",
            "ralph/visual/**/*.py",
            "ralph/agents/multimodal_status.py",
            "ralph/agents/delegation_capabilities.py",
        ],
        "reason": (
            "Tests use literal fixtures; visual and transport policy code uses "
            "closed protocol constants that read more clearly at the validation site."
        ),
    },
    "ANN001": {
        "pattern": "tests/**/*.py",
        "reason": (
            "Test fixtures and helper closures take a few positional-only "
            "kwargs without full annotation; the behaviour is covered by "
            "the assertion on the helper's return."
        ),
    },
    "ANN201": {
        "pattern": "tests/**/*.py",
        "reason": ("Test helpers' return types are not needed; assertions pin the expected shape."),
    },
    "ANN202": {
        "pattern": "tests/**/*.py",
        "reason": (
            "Private test helpers (in-memory workspaces, decoders) are "
            "small and are covered by the public test asserts; full "
            "annotations add noise without safety."
        ),
    },
    "ANN204": {
        "pattern": "tests/**/*.py",
        "reason": ("Test __str__ / __repr__ helpers are documented inline."),
    },
    "ANN401": {
        "pattern": ["tests/**/*.py", "ralph/visual/**/*.py"],
        "reason": (
            "Tests sometimes use Any for fixture dict shapes; visual capture "
            "adapters accept untyped renderer payloads at their boundary."
        ),
    },
    "RUF043": {
        "pattern": "tests/**/*.py",
        "reason": "Tests retain literal regular expressions as executable fixtures.",
    },
    "PERF401": {
        "pattern": "tests/**/*.py",
        "reason": "Test setup favors explicit loops that keep fixture assertions readable.",
    },
    "PLR0124": {
        "pattern": "tests/**/*.py",
        "reason": "Tests use explicit self-comparisons to pin identity and alias behavior.",
    },
    "N802": {
        "pattern": "tests/**/*.py",
        "reason": "Tests preserve externally defined API names exactly.",
    },
    "TC002": {
        "pattern": "tests/**/*.py",
        "reason": "Tests import pytest at runtime for fixtures and markers; type-only annotations live in TYPE_CHECKING where applicable.",
    },
    "TC003": {
        "pattern": [
            "tests/**/*.py",
            "ralph/mcp/explore/**/*.py",
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/server/_host_contention_signal.py",
        ],
        "reason": (
            "Tests legitimately use Path() at runtime for fixture "
            "construction (tmp_path / '...' join) and literal-path "
            "assertions; cannot be TYPE_CHECKING only. Explore and "
            "workspace runtime-evaluate type aliases (collections.abc, "
            "pathlib, Sequence) inside ``Protocol`` definitions and "
            "``isinstance`` checks; moving them to TYPE_CHECKING would "
            "split the type-only path from the runtime path."
        ),
    },
    "PLC0415": {
        "pattern": [
            "ralph/cli/**/*.py",
            "ralph/config/**/*.py",
            "ralph/display/**/*.py",
            "ralph/mcp/explore/**/*.py",
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/pipeline/**/*.py",
            "ralph/phases/**/*.py",
            "ralph/agents/invoke/_workspace.py",
            "ralph/diagnostics/**/*.py",
            "ralph/agents/builtin_spec.py",
            "ralph/agents/support.py",
            "ralph/agents/completion_signals.py",
            "ralph/project_policy/remediation.py",
            "ralph/prompts/__init__.py",
            "ralph/agents/execution_state/_helpers.py",
            "ralph/pipeline/plumbing/smoke_multimodal.py",
            "ralph/pipeline/plumbing/smoke_plumbing.py",
            "ralph/mcp/tools/_validation_retry_hints.py",
            "ralph/mcp/tools/coordination.py",
            "ralph/supervising.py",
            "tests/**/*.py",
        ],
        "reason": (
            "Lazy imports break specific cycles: explore<->workspace seam "
            "for the FTS/evidence substrate; workspace<->explore for the "
            "index handle session seam; pipeline<->explore for the "
            "before/after dev-fix session refresh hook; phases->pipeline"
            "->config->policy->loader->phases for the phase-handler "
            "registration seam so register_role_handlers can be defined "
            "before ralph.policy.loader imports it. _workspace<->dirty_paths "
            "for the scheduler lifecycle; diagnostics<->CLI/explore for the "
            "workspace-health maintenance read. The two MCP tool modules "
            "resolve their retry-hint and validation-banner dependencies at "
            "call time so the MCP module tree does not pull the "
            "ralph.recovery module tree. Mirrors the "
            "CLI/config/display precedent."
        ),
    },
    "E501": {
        "pattern": [
            "ralph/**/*.py",
            "tests/**/*.py",
        ],
        "reason": (
            "Production and test lines may exceed the default length when "
            "they embed real tool output (script text, error messages) in "
            "fixture strings; the line-length signal is not informative there."
        ),
    },
    "PLR0911": {
        "pattern": [
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/explore/**/*.py",
            "ralph/visual/**/*.py",
            "ralph/agents/multimodal_status.py",
            "ralph/agents/delegation_capabilities.py",
            "ralph/agents/vision_agent_provisioning.py",
            "ralph/agents/builtin_spec.py",
            "ralph/agents/support.py",
            "ralph/pipeline/plumbing/smoke_multimodal.py",
            "ralph/pipeline/plumbing/smoke_plumbing.py",
            "ralph/mcp/multimodal/capabilities.py",
        ],
        "reason": (
            "Short-circuit contract validation and transport/capture state "
            "dispatch stay local; extracting the branches would obscure the "
            "fail-closed decision order."
        ),
    },
    "PLR0912": {
        "pattern": [
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/explore/**/*.py",
            "ralph/visual/**/*.py",
            "ralph/agents/builtin_spec.py",
            "ralph/pipeline/plumbing/smoke_multimodal.py",
            "ralph/pipeline/plumbing/smoke_plumbing.py",
        ],
        "reason": (
            "Short-circuit contract validation and transport/capture state "
            "dispatch stay local; extracting the branches would obscure the "
            "fail-closed decision order."
        ),
    },
    "PLR0915": {
        "pattern": [
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/explore/**/*.py",
        ],
        "reason": (
            "Indexed grep_files handler statements grew with the "
            "eligibility/fallback/evidence-id plumbing; per-branch "
            "control flow stays local."
        ),
    },
    "RUF012": {
        "pattern": [
            "ralph/mcp/explore/**/*.py",
        ],
        "reason": (
            "Class-level _active dict is the single-writer registry; "
            "instance-level would lose cross-instance coalescing, which "
            "is the documented contract."
        ),
    },
}

_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*(.*?))?(?:\s*$|\s+\S)")

_TEST_NOQA_EXEMPT_STEMS: frozenset[str] = frozenset(
    {
        "test_audit_lint_bypass",
        "audit_lint_bypass",
        "test_audit_comments",
    }
)


class LintBypassViolation:
    def __init__(
        self,
        file_path: str,
        line: int,
        category: str,
        detail: str,
    ) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [LINT-BYPASS] {self.category}: {self.detail}"


def _find_noqa_violations(lines: list[str], rel_path: str) -> list[LintBypassViolation]:
    violations: list[LintBypassViolation] = []
    file_stem = Path(rel_path).stem

    in_triple = False
    for idx, raw_line in enumerate(lines):
        lineno = idx + 1
        quote_count = raw_line.count('"""') + raw_line.count("'''")

        if in_triple or quote_count % 2 == 1:
            if quote_count % 2 == 1:
                in_triple = not in_triple
            continue

        match = _NOQA_RE.search(raw_line)
        if not match:
            continue

        if file_stem in _TEST_NOQA_EXEMPT_STEMS:
            continue
        if rel_path.startswith("tests/"):
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="test-noqa",
                    detail="# noqa in test file — tests must follow all lint rules",
                )
            )
            continue

        codes_str = match.group(1)

        if codes_str is None:
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="bare-noqa",
                    detail="bare '# noqa' without specific error code",
                )
            )
            continue

        codes = [c.strip() for c in str(codes_str).split(",") if c.strip()]
        for code in codes:
            if (file_stem, code) in _NOQA_ALLOWLIST:
                continue
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="forbidden-noqa",
                    detail=f"'# noqa: {code}' — inline lint suppression is not "
                    f"permitted; remove the comment and fix the underlying lint error",
                )
            )

    return violations


def _per_file_ignore_violation(
    pyproject_path: Path,
    file_pattern: str,
    code: str,
) -> LintBypassViolation | None:
    allowlist_entry = _PYPROJECT_IGNORE_ALLOWLIST.get(code)
    if allowlist_entry is None:
        return LintBypassViolation(
            file_path=str(pyproject_path),
            line=0,
            category="per-file-ignores",
            detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
            f"code {code} is not in the per-file-ignores allowlist",
        )
    allowed_patterns = allowlist_entry["pattern"]
    matches = (
        file_pattern in allowed_patterns
        if isinstance(allowed_patterns, list)
        else file_pattern == allowed_patterns
    )
    if matches:
        return None
    return LintBypassViolation(
        file_path=str(pyproject_path),
        line=0,
        category="per-file-ignores",
        detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
        f"allowlisted code {code} applied to non-matching file pattern "
        f"(expected '{allowlist_entry['pattern']}')",
    )


def _per_file_ignore_violations(
    pyproject_path: Path,
    ruff_lint: Mapping[str, object],
) -> list[LintBypassViolation]:
    violations: list[LintBypassViolation] = []
    ignores = _string_key_mapping(ruff_lint.get("per-file-ignores", {}))
    for file_pattern, codes in ignores.items():
        code_list: list[object] = list(codes) if isinstance(codes, list) else [codes]
        for code_raw in code_list:
            violation = _per_file_ignore_violation(pyproject_path, file_pattern, str(code_raw))
            if violation is not None:
                violations.append(violation)
    extended_ignores = _string_key_mapping(ruff_lint.get("extend-per-file-ignores", {}))
    for file_pattern, codes in extended_ignores.items():
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="extend-per-file-ignores",
                detail=f"[tool.ruff.lint.extend-per-file-ignores] '{file_pattern}': {codes} — "
                f"per-file-ignores weakens lint enforcement",
            )
        )
    return violations


def _global_ignore_violations(
    pyproject_path: Path,
    ruff_tool: Mapping[str, object],
    ruff_lint: Mapping[str, object],
) -> list[LintBypassViolation]:
    settings = (
        ("[tool.ruff] ignore", ruff_tool.get("ignore"), "global ignore weakens lint enforcement"),
        (
            "[tool.ruff.lint] ignore",
            ruff_lint.get("ignore"),
            "global ignore weakens lint enforcement",
        ),
        (
            "[tool.ruff.lint] extend-ignore",
            ruff_lint.get("extend-ignore"),
            "extend-ignore weakens lint enforcement",
        ),
    )
    return [
        LintBypassViolation(
            file_path=str(pyproject_path),
            line=0,
            category="global-ignore",
            detail=f"{setting} = {value} - {reason}",
        )
        for setting, value, reason in settings
        if value
    ]


def _check_pyproject_config(pyproject_path: Path) -> list[LintBypassViolation]:
    if not pyproject_path.is_file():
        return []
    data = _load_toml_root(pyproject_path)
    if data is None:
        return []
    ruff_lint = _nested_mapping(data, "tool", "ruff", "lint")
    ruff_tool = _nested_mapping(data, "tool", "ruff")
    return _per_file_ignore_violations(pyproject_path, ruff_lint) + _global_ignore_violations(
        pyproject_path, ruff_tool, ruff_lint
    )


def _collect_py_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def audit_codebase(codebase_root: Path) -> tuple[list[LintBypassViolation], int]:
    all_violations: list[LintBypassViolation] = []
    files_checked = 0

    for py_file in sorted(_collect_py_files(codebase_root)):
        files_checked += 1
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        rel_path = str(py_file.relative_to(codebase_root))
        violations = _find_noqa_violations(lines, rel_path)
        all_violations.extend(violations)

    pyproject_path = codebase_root / "pyproject.toml"
    config_violations = _check_pyproject_config(pyproject_path)
    all_violations.extend(config_violations)

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    codebase_root = Path(args[0]) if args else Path(__file__).parent.parent.parent

    if not codebase_root.is_dir():
        print(f"Error: directory not found: {codebase_root}", file=sys.stderr)
        return 2

    print(f"Auditing lint bypass in: {codebase_root}")
    print()

    violations, files_checked = audit_codebase(codebase_root)

    if violations:
        print(
            f"LINT BYPASS VIOLATIONS FOUND: {len(violations)} violation(s) "
            f"in {files_checked} file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print("These violations weaken lint enforcement. Fix the violation, not the audit.")
        print("Guidance: AGENTS.md §'Non-negotiables' — no weakening checks.")
        return 1

    print(f"No lint bypass violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
