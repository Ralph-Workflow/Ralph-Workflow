"""Static audit: no direct subprocess calls outside ProcessManager."""

from __future__ import annotations

from pathlib import Path

RALPH_ROOT = Path(__file__).parent.parent / "ralph"

FORBIDDEN_PATTERNS = [
    "subprocess.run(",
    "subprocess.Popen(",
    "asyncio.create_subprocess_exec(",
    "asyncio.create_subprocess_shell(",
]

ALLOWLIST: list[tuple[str, str]] = [
    (
        "diagnostics/__init__.py",
        "Short-lived diagnostic probes (git version, which) not part of tracked lifecycle; "
        "deferred to follow-up refactor.",
    ),
    (
        "exit_pause/__init__.py",
        "Windows-only PowerShell parent-process detection; platform-specific one-shot probe "
        "not appropriate for tracked lifecycle; deferred to follow-up.",
    ),
    (
        "mcp/tools/git_read.py",
        "DI-based runner interface (GitRunner type alias); _run_git_subprocess is the default "
        "injectable; refactor deferred to keep this PR small.",
    ),
    (
        "pipeline/parallel/merge_integrator.py",
        "Async git operations via GitExecutor.arun; requires async run_git which is out of scope "
        "for this PR; deferred to follow-up.",
    ),
]


def _allowed(rel_path: str) -> bool:
    return any(rel_path == path for path, _ in ALLOWLIST)


def test_no_direct_subprocess_calls_outside_process_manager() -> None:
    """Assert no production file under ralph/ uses subprocess directly except manager.py."""
    violations = [
        f"{py_file.relative_to(RALPH_ROOT).as_posix()}: contains '{pattern}'"
        for py_file in sorted(RALPH_ROOT.rglob("*.py"))
        for rel in [py_file.relative_to(RALPH_ROOT).as_posix()]
        if rel != "process/manager.py" and not _allowed(rel)
        for pattern in FORBIDDEN_PATTERNS
        if pattern in py_file.read_text(encoding="utf-8")
    ]

    assert not violations, (
        "Direct subprocess calls found outside ralph/process/manager.py:\n"
        + "\n".join(violations)
    )
