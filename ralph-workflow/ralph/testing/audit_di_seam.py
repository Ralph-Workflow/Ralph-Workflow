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

PASS1_ALLOWLIST: tuple[str, ...] = (
    "mcp/protocol/env.py",
    "mcp/server/_timing_safety.py",
    "mcp/server/runtime.py:211",
    "mcp/server/runtime.py:389",
    "mcp/server/runtime.py:428",
    "mcp/server/_start_stage_tracker.py:18",
    "mcp/server/_fallback_standalone_server.py:65",
    "mcp/server/_fallback_standalone_server.py:79",
    "mcp/transport/nanocoder.py:64",
    "mcp/transport/nanocoder.py:69",
    "mcp/transport/kimi.py:92",
    "mcp/websearch/secrets.py:17",
    "pipeline/runner.py:804",
    "pipeline/plumbing/smoke_plumbing.py:443",
    "pipeline/plumbing/smoke_plumbing.py:490",
    "agents/invoke/_process_reader.py:225",
    "pipeline/plumbing/smoke_plumbing.py:516",
    "pipeline/plumbing/smoke_plumbing.py:1542",
    "pipeline/plumbing/smoke_plumbing.py:1761",
)

PASS1_TOP_LEVEL_ALLOWLIST: tuple[str, ...] = (
    "ralph/config/",
    "ralph/main.py",
    "ralph/__main__.py",
)

PASS2_SESSION_FACTORY_MODULES: tuple[str, ...] = (
    "mcp/server/runtime_session.py",
    "mcp/server/_fallback_http_handler.py",
)

PASS1_DEFAULT_ROOTS: tuple[str, ...] = (
    "mcp",
    "agents",
    "process",
    "recovery",
    "pipeline",
    "git",
)


class DiSeamViolation:
    def __init__(self, file_path: str, line: int, category: str, detail: str) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _is_dry_run() -> bool:
    raw = os.environ.get("AUDIT_DI_SEAM_DRY_RUN", "false")
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def _is_allowlisted(rel_path: str, line: int) -> bool:
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
    for top in PASS1_TOP_LEVEL_ALLOWLIST:
        if top.endswith("/") and rel_path.startswith(top):
            return True
        if rel_path == top:
            return True
    return False


def _dotted_name(node: ast.Call) -> str | None:
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
    name = _dotted_name(call)
    if name is None:
        return None
    if name in {"os.environ.__getitem__", "os.environ.get", "os.getenv"}:
        return name
    return None


def _is_direct_open(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "open"


class _Pass1EnvOpenVisitor(ast.NodeVisitor):
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
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, SyntaxError):
        return []
    visitor = _Pass2CastVisitor(rel_path)
    visitor.visit(tree)
    return visitor.violations


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.relative_to(root).parts)
    )


def audit_pass1(
    package_root: Path,
    roots: tuple[str, ...] = PASS1_DEFAULT_ROOTS,
) -> tuple[list[DiSeamViolation], int]:
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
    package_root = Path(__file__).parent.parent

    dry_run = _is_dry_run()
    if dry_run:
        print("AUDIT_DI_SEAM_DRY_RUN=true — hits are reported but the audit does not fail.")
    else:
        print("AUDIT_DI_SEAM_DRY_RUN=false — strict mode; any hit fails the audit.")
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
