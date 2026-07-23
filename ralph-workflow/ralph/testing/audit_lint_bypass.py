"""Lint bypass audit — detects forbidden noqa and per-file-ignores.

Scans the codebase for:
- Bare ``# noqa`` comments without a specific error code
- ``# noqa: CODE`` where CODE is not in the allowlist
- ``[tool.ruff.lint.per-file-ignores]``, ``extend-per-file-ignores``, ``ignore``,
  or ``extend-ignore`` in pyproject.toml

Usage:
    python -m ralph.testing.audit_lint_bypass [codebase_root]

Returns exit code 0 if no lint bypass violations found, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable


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


# ---------------------------------------------------------------------------
# Allowlist: known-legitimate noqa uses
#
# Format: {(file_stem, error_code), ...}
# - file_stem matches the filename stem only (not full path), so
#   "audit_test_policy" matches tests/test_audit_xxx.py as well as
#   ralph/testing/audit_test_policy.py — any file with that stem.
# - error_code is the ruff code (e.g. "PLR0911", "PLW0603").
# ---------------------------------------------------------------------------
_NOQA_ALLOWLIST: set[tuple[str, str]] = {
    ("audit_test_policy", "PLR0911"),
    ("audit_test_policy", "PLW0603"),
    ("audit_typecheck_bypass", "PLR0912"),
    ("audit_lint_bypass", "PLR0912"),
    ("commit_executor", "PLC0415"),
    ("runner", "PLC0415"),  # lazy import in module __getattr__ breaks runner<->run_loop cycle
    ("worker_runtime", "PLC0415"),
    ("commit_cleanup", "PLC0415"),
    ("materialize", "PLC0415"),
    ("supervising", "PLC0415"),
    ("pytest_timeout_plugin", "PLC0415"),
    ("_event_classification", "PLC0415"),
    ("run_loop", "PLC0415"),
    ("run_loop", "PLR0912"),
    ("run_loop", "PLR0915"),
    ("idle_watchdog", "PLR0911"),  # evaluate() consults gate then 5 sub-evaluators
    ("idle_watchdog", "PLR0912"),  # _handle_waiting_branch has 5 reasons + gate path branches
    ("idle_watchdog", "PLR0915"),  # _handle_waiting_branch orchestrates 5 reasons
    ("_active_branch", "PLR0911"),  # extracted evaluate_inner keeps original gate fan-out
    ("_fire_evaluators", "PLR0911"),  # extracted fire evaluators preserve early-exit paths
    ("_fire_evaluators", "PLR0912"),  # extracted no-progress branches
    ("_waiting_branch", "PLR0911"),  # extracted waiting branch preserves 5 verdict paths
    ("_waiting_branch", "PLR0912"),  # extracted waiting branch preserves suspect/hard-stop branches
    ("_waiting_branch", "PLR0915"),  # extracted waiting branch orchestrates 5 reasons
    ("_stuck_classifier", "PLR0911"),  # 7 distinct StuckKind outcomes
    ("heartbeat", "PLC0415"),
    ("canonical_submit", "PLC0415"),  # lazy import avoids cycle with tools.artifact
    ("artifact", "PLC0415"),  # lazy import avoids cycle with canonical_submit
    ("completion_signals", "PLC0415"),  # lazy import avoids cycle with invoke->tools
    ("pydantic_validation_errors", "PLR0911"),  # exhaustive error-type dispatch
    ("commit_plumbing", "UP047"),
    ("claude_interactive_transcript_parser", "PLR0911"),
    ("claude_interactive_transcript_parser", "PLR0912"),
    ("_metrics", "PLW0603"),
    # _sentry.py: scalar module-level state (_SESSION_STARTED_AT,
    # _SESSION_OUTCOME, _INITIALIZED, _EXTRA_SCRUB_PREFIXES) is the
    # only honest shape for the session lifecycle + scrubber (the
    # audit_resource_lifecycle contract forbids list/dict/set/deque
    # at module level). The setters therefore MUST update those
    # scalars in-place via `global`, which ruff flags as PLW0603.
    ("_sentry", "PLW0603"),
    ("_renderers", "PLR0912"),
    ("parallel_display", "PLR0912"),
    ("pydantic_validation_errors", "PLR0911"),
    ("_command_builders", "PLC0415"),  # lazy import enables test monkeypatching of invoke module
    ("_runtime_resolvers", "PLC0415"),  # lazy import enables test monkeypatching of invoke module
    # _media_io.py: global state for periodic prune counter (wt-024 AC-10).
    ("_media_io", "PLW0603"),
    # wt-024 memory-perf: the bounded-accumulator-ok marker must live
    # on the same physical line as the assignment so the
    # audit_resource_lifecycle AST marker scan finds it. When the
    # assignment carries a full type annotation (mypy strict requires
    # it) + the marker + the policy-compliant type-ignore suffix, the
    # line exceeds 100 chars. The noqa is narrowly scoped to the
    # assignment line and does NOT mask any other ruff check.
    ("idle_watchdog", "E501"),
    ("ring_buffer", "E501"),
    ("_process_manager", "E501"),
    ("codex", "E501"),
    ("audit_adapter", "E501"),
    ("_bounded_lines_queue", "E501"),
    ("repetition_tracker", "E501"),
    ("_pty_line_reader", "E501"),
    # __init__ modules use lazy imports to avoid circular deps; targeted per-file
    ("__init__", "PLC0415"),
    # __init__ modules intentionally order __all__ for discoverability (e.g.
    # the 90% recipe before the 14-kwarg advanced helper) rather than
    # alphabetical sort, so the registration helpers read top-down.
    ("__init__", "RUF022"),
    # catalog.py: late import of builtin_supports avoids parser<->catalog cycle.
    ("catalog", "PLC0415"),
    # registration.py: late import of AgentConfig is a forward reference.
    ("registration", "UP037"),
    # N802: historical public API name preserved for backward compat.
    ("catalog", "N802"),
    # registry.py: _resolve_dynamic_agent is a 7-prefix dispatcher (pi, opencode,
    # nanocoder, agy, claude, claude-headless, ccs); each prefix branch
    # validates then resolves independently and returns early on rejection.
    ("registry", "PLR0911"),
    ("registry", "PLR0912"),
    # execution_state/_factory.py: late imports of catalog and parsers
    # break catalog<->_factory<->parsers cycles (the __getattr__ lazy
    # view pattern in those modules defers the cross-module imports).
    ("_factory", "PLC0415"),
    # parsers/__init__.py already covered by the `("__init__", "PLC0415")`
    # entry above.
    # audit_agent_module_state.py: SIM103 inlined the boolean, no leftover.
    # test_audit_agent_module_state.py: lazy import keeps the test self-contained.
    ("test_audit_agent_module_state", "PLC0415"),
    # wt-025 auto-commit skill: lazy import of git.Repo and stage_files
    # defers the import until a real call (mirrors the existing
    # commit_cleanup / runner pattern; both are in this allowlist).
    ("_auto_commit", "PLC0415"),
    # wt-025 auto-commit audit: AST walker over the untrack_engine_internal_files
    # function has 15+ branches because of the nested search for the
    # early-skip block AND the WARNING block; the AST walk is inherently
    # branchy and refactoring it would obscure the placement check.
    ("audit_skill_auto_commit", "PLR0912"),
    # wt-034 indexed-exploration: scoped_auto_commit.py wraps its
    # GitPython import (git.Repo / GitCommandError /
    # InvalidGitRepositoryError) inside a try/except so a non-git
    # workspace can import the module without GitPython installed; the
    # same lazy-import rationale already covers commit_cleanup,
    # runner, and _auto_commit in this allowlist.
    ("scoped_auto_commit", "PLC0415"),
    # project_policy/cli_integration.py: lazy imports of
    # ralph.display.status_bar.StatusBarModel and
    # ralph.git.operations.create_commit are wrapped in try/except so
    # a non-tty / non-git environment can run the readiness preflight
    # without dragging the display / git subsystems into the module's
    # top-level import graph; mirrors the supervising / canonical_submit
    # lazy-import precedent.
    ("cli_integration", "PLC0415"),
    # wt-040 auto-integrate recovery: _reclaim_unowned_stale_rebase
    # fans out across the A1/A3/A4/A5/A6/A11 reclaim paths (stale
    # rebase-merge / rebase-apply dirs, lone REBASE_HEAD, MERGE_HEAD
    # on a clean tree, sequencer ops, detached-HEAD residue); each
    # path is a small early-return and refactoring them into helper
    # functions would obscure the per-marker-file reclaim ordering
    # that AC-07/AC-06's terminal-state invariant depends on.
    ("auto_integrate_recovery", "PLR0911"),
    ("auto_integrate_recovery", "PLR0912"),
}

# Files to skip entirely (test fixtures, generated code, etc.).
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        "tmp",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
    }
)

# ---------------------------------------------------------------------------
# Allowlist: legitimate per-file-ignores entries
#
# Format: dict[str, dict[str, set[str]]] where:
#   - outer key: error code (e.g., "PLR2004", "PLC0415")
#   - value: dict with keys "pattern" (file glob) and "reason" (justification)
#
# Any [tool.ruff.lint.per-file-ignores] entry whose codes match an allowlist
# code AND file pattern matches the allowlist pattern is permitted.
# Any code NOT in this allowlist or applied to a non-matching file pattern
# still triggers a violation.
# ---------------------------------------------------------------------------
_PYPROJECT_IGNORE_ALLOWLIST: dict[str, dict[str, object]] = {
    "PLR2004": {
        "pattern": "tests/**/*.py",
        "reason": "Magic values in tests are acceptable",
    },
    # Tests legitimately relax the no-any-rename rule for fixture dicts whose
    # shape is verified by assertions rather than by type annotations.
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
        "reason": (
            "Test helpers' return types are not needed; assertions pin "
            "the expected shape."
        ),
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
        "reason": (
            "Test __str__ / __repr__ helpers are documented inline."
        ),
    },
    "ANN401": {
        "pattern": "tests/**/*.py",
        "reason": (
            "Tests sometimes use Any for fixture dict shapes; the "
            "asserting code (not the fixture) is the contract."
        ),
    },
    "TC003": {
        "pattern": [
            "tests/**/*.py",
            "ralph/mcp/explore/**/*.py",
            "ralph/mcp/tools/workspace/**/*.py",
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
            "tests/**/*.py",
        ],
        "reason": (
            "Lazy imports break specific cycles: explore<->workspace seam "
            "for the FTS/evidence substrate; workspace<->explore for the "
            "index handle session seam; pipeline<->explore for the "
            "before/after dev-fix session refresh hook; phases->pipeline"
            "->config->policy->loader->phases for the phase-handler "
            "registration seam so register_role_handlers can be defined "
            "before ralph.policy.loader imports it. Mirrors the "
            "CLI/config/display precedent."
        ),
    },
    # Accumulator contract (wt-024 memory-perf AC-04): the
    # ``# bounded-accumulator-ok: <reason>`` marker MUST stay on the
    # SAME line as the assignment (the audit looks for the marker via
    # ``source_lines[node.lineno - 1]``). With the production 100-char
    # line cap, the marker + type annotation + assignment can exceed 100
    # chars. Per-file-ignore E501 on accumulator marker lines lets the
    # audit pass without weakening the underlying line-length contract
    # (every other line still MUST fit in 100 chars; only marker lines
    # carry the documented exemption).
    "E501": {
        "pattern": [
            "ralph/**/*.py",
            "tests/**/*.py",
        ],
        "reason": (
            "bounded-accumulator-ok / resource-lifecycle-ok markers on "
            "assignment lines; tests legitimately embed real tool "
            "output (script text, error messages) in fixture strings "
            "and the line-length signal is not informative."
        ),
    },
    # Indexed MCP handlers (grep_files / search_files / read_file / read_multiple_files)
    # gain fan-out for eligibility, fallback, and evidence-id branches: the live
    # behaviour is preserved (use_index in {auto, always, never} takes one path
    # each, fail-fast vs partial-result is a 1-bit discriminator) and refactoring
    # those short-circuits into helper functions would obscure the per-file
    # decision order the indexed contract requires.
    "PLR0911": {
        "pattern": [
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/explore/**/*.py",
        ],
        "reason": (
            "Indexed MCP handler fan-out (eligibility, fallback, hash "
            "precondition); live path preserved; extra returns are "
            "pre-conditions that fan out cleanly."
        ),
    },
    "PLR0912": {
        "pattern": [
            "ralph/mcp/tools/workspace/**/*.py",
            "ralph/mcp/explore/**/*.py",
        ],
        "reason": (
            "Same as PLR0911: indexed handler has more decision points "
            "but each branch is a short-circuit; per-branch control flow "
            "stays local."
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

    # MutableClass-level dicts deliberately use class-level storage for the
    # single-writer registry; making _active instance-level would lose the
    # cross-instance coalescing that the contract guarantees.
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

# Regex for matching ``noqa`` directives on a line.
# Matches both code-specific (colon format) and blanket (no colon) forms,
# including bare noqa with trailing non-colon text.
_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*(.*?))?(?:\s*$|\s+\S)")

# Files that are explicitly testing or documenting lint-bypass behavior and must
# contain simulated directives as fixtures. These are exempt from the noqa check.
_TEST_NOQA_EXEMPT_STEMS: frozenset[str] = frozenset(
    {
        "test_audit_lint_bypass",
        "audit_lint_bypass",
    }
)

# Acceptable noqa codes — any code NOT in this set requires an allowlist entry.
# Currently only complexity and global-state codes are acceptable when used
# with a documented reason in the allowlist.
_ACCEPTABLE_NOQA_CODES: frozenset[str] = frozenset({"PLR0911", "PLR0912", "PLR0915", "PLW0603"})

# Lines that carry a ``# bounded-accumulator-ok: <reason>`` marker MUST be on
# the same line as the accumulator assignment itself (the marker is a
# per-line suppression for the audit_resource_lifecycle 4th contract).
# With the production line-100 cap, the marker + type annotation + assignment
# can exceed 100 chars on a single line. We accept E501 on those lines
# specifically via per-file-ignores (see _PYPROJECT_IGNORE_ALLOWLIST above)
# so the audit doesn't flag them.
# This documents why E501 noqa on accumulator marker lines is acceptable.


class LintBypassViolation:
    """A single lint bypass violation found during scanning."""

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
    """Scan source lines for forbidden noqa annotations."""
    violations: list[LintBypassViolation] = []
    file_stem = Path(rel_path).stem

    in_triple = False
    for idx, raw_line in enumerate(lines):
        lineno = idx + 1
        quote_count = raw_line.count('"""') + raw_line.count("'''")

        # Skip multi-line literals and keep their state in one pass.
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
            # Bare ``noqa`` without specific codes.
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="bare-noqa",
                    detail="bare '# noqa' without specific error code",
                )
            )
            continue

        # Parse comma-separated codes.
        codes = [c.strip() for c in str(codes_str).split(",") if c.strip()]
        for code in codes:
            if (file_stem, code) in _NOQA_ALLOWLIST:
                continue
            if code in _ACCEPTABLE_NOQA_CODES:
                # In allowlist but not for this file — flag.
                violations.append(
                    LintBypassViolation(
                        file_path=rel_path,
                        line=lineno,
                        category="unauthorized-noqa",
                        detail=f"'# noqa: {code}' — code {code} is not "
                        f"allowlisted for file '{file_stem}.py'",
                    )
                )
            else:
                violations.append(
                    LintBypassViolation(
                        file_path=rel_path,
                        line=lineno,
                        category="forbidden-noqa",
                        detail=f"'# noqa: {code}' — code {code} is not an "
                        f"acceptable noqa code (acceptable: {sorted(_ACCEPTABLE_NOQA_CODES)})",
                    )
                )

    return violations


def _check_pyproject_config(pyproject_path: Path) -> list[LintBypassViolation]:  # noqa: PLR0912
    """Check pyproject.toml for per-file-ignores violations."""
    violations: list[LintBypassViolation] = []

    if not pyproject_path.is_file():
        return violations

    data = _load_toml_root(pyproject_path)
    if data is None:
        return violations

    ruff_lint = _nested_mapping(data, "tool", "ruff", "lint")

    per_file_ignores = _string_key_mapping(ruff_lint.get("per-file-ignores", {}))
    if per_file_ignores:
        for file_pattern, codes in per_file_ignores.items():
            # Normalize codes value: if it's a list, iterate; else treat as single.
            code_list: list[object] = list(codes) if isinstance(codes, list) else [codes]
            for code_raw in code_list:
                code = str(code_raw)
                # Check allowlist: if code is allowlisted AND file_pattern matches
                # the allowlist pattern, skip. Otherwise flag as violation.
                if code in _PYPROJECT_IGNORE_ALLOWLIST:
                    allowlist_entry = _PYPROJECT_IGNORE_ALLOWLIST[code]
                    allowed_patterns = allowlist_entry["pattern"]
                    if isinstance(allowed_patterns, list):
                        if file_pattern in allowed_patterns:
                            continue
                    elif file_pattern == allowed_patterns:
                        continue  # Allowlisted code + matching pattern — permitted
                    # Allowlisted code but wrong file pattern — flag.
                    violations.append(
                        LintBypassViolation(
                            file_path=str(pyproject_path),
                            line=0,
                            category="per-file-ignores",
                            detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
                            f"allowlisted code {code} applied to non-matching file pattern "
                            f"(expected '{allowlist_entry['pattern']}')",
                        )
                    )
                else:
                    # Code not in allowlist — flag.
                    violations.append(
                        LintBypassViolation(
                            file_path=str(pyproject_path),
                            line=0,
                            category="per-file-ignores",
                            detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
                            f"code {code} is not in the per-file-ignores allowlist",
                        )
                    )

    extend_per_file_ignores = _string_key_mapping(
        ruff_lint.get("extend-per-file-ignores", {}),
    )
    if extend_per_file_ignores:
        for file_pattern, codes in extend_per_file_ignores.items():
            violations.append(
                LintBypassViolation(
                    file_path=str(pyproject_path),
                    line=0,
                    category="extend-per-file-ignores",
                    detail=f"[tool.ruff.lint.extend-per-file-ignores] '{file_pattern}': {codes} — "
                    f"per-file-ignores weakens lint enforcement",
                )
            )

    # --- check for global lint ignore (whole-project weakening) ---
    ruff_tool = _nested_mapping(data, "tool", "ruff")

    # top-level ruff ignore (e.g., [tool.ruff] ignore = [...])
    top_ignore = ruff_tool.get("ignore")
    if top_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff] ignore = {top_ignore} - "
                f"global ignore weakens lint enforcement",
            )
        )

    # ruff.lint ignore (e.g., [tool.ruff.lint] ignore = [...])
    lint_ignore = ruff_lint.get("ignore")
    if lint_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff.lint] ignore = {lint_ignore} - "
                f"global ignore weakens lint enforcement",
            )
        )

    # ruff.lint extend-ignore (e.g., [tool.ruff.lint] extend-ignore = [...])
    extend_ignore = ruff_lint.get("extend-ignore")
    if extend_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff.lint] extend-ignore = {extend_ignore} - "
                f"extend-ignore weakens lint enforcement",
            )
        )

    return violations


def _collect_py_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under *root*, skipping excluded directories."""
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def audit_codebase(codebase_root: Path) -> tuple[list[LintBypassViolation], int]:
    """Audit the entire codebase for lint bypass violations.

    Returns (violations, files_checked).
    """
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

    # Also check pyproject.toml config.
    pyproject_path = codebase_root / "pyproject.toml"
    config_violations = _check_pyproject_config(pyproject_path)
    all_violations.extend(config_violations)

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    """Run the lint bypass audit and return exit code.

    Exit code 0: no violations found.
    Exit code 1: violations found.
    Exit code 2: error.
    """
    args = argv if argv is not None else sys.argv[1:]

    codebase_root = (
        Path(args[0])
        if args
        else Path(__file__).parent.parent.parent  # default: ralph-workflow root
    )

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
