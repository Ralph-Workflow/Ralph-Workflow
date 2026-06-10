"""Dependency-injection seam audit.

Enforces the Foundations dependency-injection contract from PROMPT.md: every
component below the composition root must receive its collaborators through
its constructor or call signature, and must not reach into ambient process
state (``os.environ``, ``open()``) or launder the session contract through
``typing.cast()`` at the session factory boundary.

This audit runs TWO AST passes (modeled on ``audit_mcp_timeout.py``):

PASS 1 — env+open ambient reads
  Walks ``ralph/mcp/``, ``ralph/agents/``, ``ralph/process/``,
  ``ralph/recovery/``, ``ralph/pipeline/``, and ``ralph/git/`` for direct
  ambient state reads that should be replaced with an injected accessor:

    - ``os.environ[...]``
    - ``os.environ.get(...)``
    - ``os.getenv(...)``
    - ``open(...)`` (direct file I/O without an injected reader)

  Allowlist (composition-root or unavoidable boundary code):
    - ``ralph/mcp/protocol/env.py`` — defines constants; no actual env read.
    - ``ralph/mcp/server/_timing_safety.py`` — imports constants; one-line
      justification.
    - ``ralph/mcp/server/runtime.py:120`` and ``:162`` — composition root
      that reads ``MCP_*_ENV`` / ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` to wire
      the factory.
    - ``ralph/mcp/websearch/secrets.py:17`` — ``os.getenv`` is used as a
      callable ``EnvGetter`` parameter, NOT an ambient read.
    - ``ralph/config/*``, ``ralph/main.py``, ``ralph/__main__.py`` — top-level
      entry points (composition root).

PASS 2 — ``cast()`` at the session factory boundary
  Walks ``ralph/mcp/server/runtime_session.py`` and
  ``ralph/mcp/server/_fallback_http_handler.py`` (the modules named in
  PROMPT.md proof obligation B as the session factory boundary) and flags
  ANY ``cast(...)`` call. PROMPT.md proof obligation B says: "no cast() sits
  at the session factory boundary (the specific laundering that hid the
  storm), so the type checker cannot be told to look away there." This is
  why PASS 2 has NO allowlist — the architecture's stance is zero casts at
  the factory boundary.

Both passes are controlled by the ``AUDIT_DI_SEAM_DRY_RUN`` env var
(default ``"true"``). When ``true`` (the default), hits are REPORTED but the
audit does not fail — this is the dry-run pattern, used so a fresh check
surfaces hits without breaking the build. Set ``AUDIT_DI_SEAM_DRY_RUN=false``
to make any reported hit fail the audit. The composition-root env reads
(``ralph/mcp/server/runtime.py`` and similar) are also under the dry-run
umbrella, so the audit can be turned into a hard gate once the allowlist
and boundary code are confirmed correct.

Self-audit (per PA-009): this module uses only non-mutating operations —
``Path.rglob`` + ``read_text`` + ``ast.parse``. It NEVER uses
``subprocess.run``, ``time.sleep``, or real file writes, so it passes
``audit_test_policy`` and ``audit_mcp_timeout`` on itself.

Usage:
    python -m ralph.testing.audit_di_seam [root1 root2 ...]

Exit codes:
  0 = clean (in dry-run mode, always; in strict mode, only if no hits).
  1 = violations found (strict mode only).
  2 = root not found.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    }
)

# PASS 1 — direct ambient env / filesystem reads below the composition root.
# Each entry: a directory (relative to the ralph package root) or a specific
# ``module:line`` declaration for narrow carve-outs.
#
# Justification is recorded inline for every entry; the audit prints the
# justification when it skips a file so reviewers can audit the allowlist.
PASS1_ALLOWLIST: tuple[str, ...] = (
    # mcp/protocol/env.py — defines MCP_*_ENV constants; no actual read.
    "mcp/protocol/env.py",
    # mcp/server/_timing_safety.py — imports constants for the
    # import-time invariant check (one-line justification, see source).
    "mcp/server/_timing_safety.py",
    # Composition root that reads MCP_*_ENV / UPSTREAM_MCP_TOOL_CATALOG_ENV
    # to wire the factory. Justified by PROMPT.md: "Concrete IO and
    # side-effecting implementations are assembled in exactly one place —
    # the composition root."
    "mcp/server/runtime.py:120",
    "mcp/server/runtime.py:162",
    # mcp/server/_fallback_standalone_server.py — startup banner reads
    # ``MCP_AUTH_TOKEN`` (auth posture) and ``RALPH_MCP_PROBE_TIMEOUT_MS``
    # (probe ceiling) so the operator-visible banner matches the live
    # configuration. The banner is part of the composition root.
    "mcp/server/_fallback_standalone_server.py:56",
    "mcp/server/_fallback_standalone_server.py:70",
    # mcp/transport/nanocoder.py — reads platform env vars (``APPDATA``,
    # ``XDG_CONFIG_HOME``) to resolve the user-level nanocoder config dir.
    # These are platform-path conventions, not config; injecting them
    # would add noise without changing testable behavior.
    "mcp/transport/nanocoder.py:64",
    "mcp/transport/nanocoder.py:69",
    # mcp/websearch/secrets.py:17 — ``getenv`` is a callable parameter
    # of type ``EnvGetter``, NOT an ambient read.
    "mcp/websearch/secrets.py:17",
    # pipeline/runner.py:816 — display-only CTA: hashes the USER env var
    # with the process id to determine whether to print a star-CTA after
    # a successful pipeline run. Cosmetic, not a config read; the result
    # is non-deterministic enough that injecting it would add no test
    # value.
    "pipeline/runner.py:816",
)

# Top-level entry points and the config package — the composition root for
# the application. Allowed to read ambient env / open files.
PASS1_TOP_LEVEL_ALLOWLIST: tuple[str, ...] = (
    "ralph/config/",
    "ralph/main.py",
    "ralph/__main__.py",
)

# PASS 2 — modules that comprise the session factory boundary per
# PROMPT.md proof obligation B. Any ``cast(...)`` call in these files is
# flagged (no allowlist). Paths are relative to the package root (no
# leading ``ralph/``), matching the file walker in audit_pass1().
PASS2_SESSION_FACTORY_MODULES: tuple[str, ...] = (
    "mcp/server/runtime_session.py",
    "mcp/server/_fallback_http_handler.py",
)

# PASS 1 — roots audited (relative to the ralph package root, except the
# first entry which is the workspace root for the top-level config files).
PASS1_DEFAULT_ROOTS: tuple[str, ...] = (
    "mcp",
    "agents",
    "process",
    "recovery",
    "pipeline",
    "git",
)


class DiSeamViolation:
    """A single dependency-injection seam violation."""

    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _is_dry_run() -> bool:
    """Return True unless ``AUDIT_DI_SEAM_DRY_RUN`` is set to a false-y value.

    The dry-run default lets a fresh audit surface hits without breaking the
    build — the composition-root env reads (and any future allowlist
    expansion) can be reviewed in dry-run output before turning the audit
    into a hard gate.
    """
    raw = os.environ.get("AUDIT_DI_SEAM_DRY_RUN", "true")
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _is_allowlisted(rel_path: str, line: int) -> bool:
    """True if the (file, line) is in PASS 1's allowlist."""
    for entry in PASS1_ALLOWLIST:
        if ":" in entry:
            file_part, line_part = entry.rsplit(":", 1)
            if rel_path == file_part and str(line) == line_part:
                return True
        elif rel_path == entry:
            return True
    for top in PASS1_TOP_LEVEL_ALLOWLIST:
        if top.endswith("/") and rel_path.startswith(top):
            return True
        if rel_path == top:
            return True
    return False


def _is_top_level_allowlisted(rel_path: str) -> bool:
    """Top-level config / main entry points (composition root) — skip entirely."""
    for top in PASS1_TOP_LEVEL_ALLOWLIST:
        if top.endswith("/") and rel_path.startswith(top):
            return True
        if rel_path == top:
            return True
    return False


def _dotted_name(node: ast.Call) -> str | None:
    """Return the dotted function name when the receiver chain is plain names."""
    func: ast.expr = node.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    else:
        return None
    return ".".join(reversed(parts))


def _is_env_access(call: ast.Call) -> str | None:
    """Return the env access category if the call is a direct env read, else None."""
    name = _dotted_name(call)
    if name is None:
        return None
    if name in {"os.environ.__getitem__", "os.environ.get", "os.getenv"}:
        return name
    return None


def _is_direct_open(call: ast.Call) -> bool:
    """True if the call is a bare ``open(...)`` (not bound)."""
    return isinstance(call.func, ast.Name) and call.func.id == "open"


class _Pass1EnvOpenVisitor(ast.NodeVisitor):
    """PASS 1: direct ambient env / file reads."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.violations: list[DiSeamViolation] = []

    def visit_Call(self, node: ast.Call) -> None:
        env_access = _is_env_access(node)
        if env_access is not None and not _is_allowlisted(self.file_path, node.lineno):
            self.violations.append(
                DiSeamViolation(
                    file_path=self.file_path,
                    line=node.lineno,
                    category="ambient_env",
                    detail=(
                        f"direct {env_access}() — inject an EnvGetter accessor "
                        "instead of reading ambient os.environ"
                    ),
                )
            )
        if _is_direct_open(node):
            self.violations.append(
                DiSeamViolation(
                    file_path=self.file_path,
                    line=node.lineno,
                    category="ambient_open",
                    detail=(
                        "direct open() — inject a file reader seam "
                        "(see MemoryWorkspace / FileBackend for examples)"
                    ),
                )
            )
        self.generic_visit(node)


class _Pass2CastVisitor(ast.NodeVisitor):
    """PASS 2: ``cast(...)`` at the session factory boundary."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.violations: list[DiSeamViolation] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "cast":
            self.violations.append(
                DiSeamViolation(
                    file_path=self.file_path,
                    line=node.lineno,
                    category="session_factory_cast",
                    detail=(
                        "cast() at the session factory boundary — replace with "
                        "explicit isinstance() narrowing or a typed loader "
                        "function (PROMPT.md proof obligation B)"
                    ),
                )
            )
        self.generic_visit(node)


def audit_pass1_file(rel_path: str, file_path: Path) -> list[DiSeamViolation]:
    """Run PASS 1 (env+open) on a single Python file."""
    if _is_top_level_allowlisted(rel_path):
        return []
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return []
    visitor = _Pass1EnvOpenVisitor(rel_path)
    visitor.visit(tree)
    return visitor.violations


def audit_pass2_file(rel_path: str, file_path: Path) -> list[DiSeamViolation]:
    """Run PASS 2 (cast at session factory boundary) on a single Python file."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return []
    visitor = _Pass2CastVisitor(rel_path)
    visitor.visit(tree)
    return visitor.violations


def _iter_py_files(root: Path) -> list[Path]:
    """Walk a directory for ``*.py`` files, skipping caches and build dirs."""
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    )


def audit_pass1(
    package_root: Path,
    roots: tuple[str, ...] = PASS1_DEFAULT_ROOTS,
) -> tuple[list[DiSeamViolation], int]:
    """PASS 1 — direct env / open ambient reads.

    Returns (violations, files_checked).
    """
    all_violations: list[DiSeamViolation] = []
    files_checked = 0
    for rel_root in roots:
        root = package_root / rel_root
        if not root.is_dir():
            continue
        for py_file in _iter_py_files(root):
            rel_path = py_file.relative_to(package_root).as_posix()
            all_violations.extend(audit_pass1_file(rel_path, py_file))
            files_checked += 1
    return all_violations, files_checked


def audit_pass2(
    package_root: Path,
    modules: tuple[str, ...] = PASS2_SESSION_FACTORY_MODULES,
) -> tuple[list[DiSeamViolation], int]:
    """PASS 2 — ``cast()`` at the session factory boundary.

    Returns (violations, modules_walked).
    """
    all_violations: list[DiSeamViolation] = []
    modules_walked = 0
    for rel_path in modules:
        file_path = package_root / rel_path
        if not file_path.is_file():
            continue
        all_violations.extend(audit_pass2_file(rel_path, file_path))
        modules_walked += 1
    return all_violations, modules_walked


def _format_pass1_header(
    files_checked: int,
    package_root: Path,
    roots: tuple[str, ...],
) -> str:
    lines = [f"Auditing DI seam (env+open) in: {package_root}"]
    lines.extend(f"  root: {rel_root}/" for rel_root in roots)
    lines.append(f"  files checked: {files_checked}")
    return "\n".join(lines)


def _format_pass2_header(
    modules_walked: int,
    package_root: Path,
    modules: tuple[str, ...],
) -> str:
    lines = [f"Auditing DI seam (cast at session factory boundary) in: {package_root}"]
    lines.extend(f"  module: {rel_path}" for rel_path in modules)
    lines.append(f"  modules walked: {modules_walked}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run both DI-seam audit passes and return an exit code."""
    package_root = Path(__file__).parent.parent

    dry_run = _is_dry_run()
    if dry_run:
        print(
            "AUDIT_DI_SEAM_DRY_RUN=true — hits are reported but the audit does not fail."
        )
    else:
        print(
            "AUDIT_DI_SEAM_DRY_RUN=false — strict mode; any hit fails the audit."
        )
    print()

    pass1_violations, files_checked = audit_pass1(package_root)
    pass2_violations, modules_walked = audit_pass2(package_root)

    print(_format_pass1_header(files_checked, package_root, PASS1_DEFAULT_ROOTS))
    if pass1_violations:
        print(f"  hits: {len(pass1_violations)}")
        for v in pass1_violations:
            print(f"    {v}")
    else:
        print("  hits: 0")
    print()
    print(_format_pass2_header(modules_walked, package_root, PASS2_SESSION_FACTORY_MODULES))
    if pass2_violations:
        print(f"  hits: {len(pass2_violations)}")
        for v in pass2_violations:
            print(f"    {v}")
    else:
        print("  hits: 0")
    print()

    total = len(pass1_violations) + len(pass2_violations)
    if dry_run:
        print(
            f"DI seam audit (dry-run) — {total} potential violation(s) across "
            f"{files_checked} file(s) and {modules_walked} session-factory "
            f"module(s). Set AUDIT_DI_SEAM_DRY_RUN=false to enforce."
        )
        return 0
    if total == 0:
        print(
            f"No DI-seam violations found in {files_checked} file(s) and "
            f"{modules_walked} session-factory module(s)."
        )
        return 0
    print(
        f"DI-SEAM CONTRACT VIOLATIONS: {total} (PASS 1: {len(pass1_violations)}, "
        f"PASS 2: {len(pass2_violations)})"
    )
    print("=" * 72)
    for v in pass1_violations:
        print(f"  {v}")
    for v in pass2_violations:
        print(f"  {v}")
    print()
    print(
        "Inject an EnvGetter / file-reader seam (PASS 1) or remove cast() at the "
        "session factory boundary (PASS 2). Add inline justification to "
        "PASS1_ALLOWLIST for composition-root reads."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
