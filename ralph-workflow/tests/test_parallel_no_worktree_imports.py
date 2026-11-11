from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

_PARALLEL_MODULES = [
    "ralph.pipeline.parallel.coordinator",
    "ralph.pipeline.parallel.scheduler",
    "ralph.pipeline.parallel.worker_session",
    "ralph.pipeline.parallel.mode",
]

_FORBIDDEN_TOKENS = [
    "git_worktree",
    ".worktrees",
    "create_worktree",
    "rebase_onto",
    "merge_branch",
]


def _read_source(module_name: str) -> str:

    mod = importlib.import_module(module_name)
    try:
        return inspect.getsource(mod)
    except OSError:
        source_file = getattr(mod, "__file__", None)
        if source_file is None:
            pytest.skip(f"No source file for {module_name}")
        return Path(source_file).read_text(encoding="utf-8")


@pytest.mark.parametrize("module_name", _PARALLEL_MODULES)
def test_parallel_module_has_no_forbidden_tokens(module_name: str) -> None:
    source = _read_source(module_name)
    violations = [token for token in _FORBIDDEN_TOKENS if token in source]
    assert violations == [], (
        f"{module_name} contains forbidden token(s): {violations!r}. "
        "The same-workspace parallel path must not reference worktree or merge helpers."
    )


def test_parallel_module_does_not_import_rebase() -> None:

    rebase_key = "ralph.git.rebase"

    before_keys = set(sys.modules.keys())
    importlib.import_module("ralph.pipeline.parallel.coordinator")
    after_keys = set(sys.modules.keys())

    new_imports = after_keys - before_keys
    rebase_imports = [k for k in new_imports if k.startswith(rebase_key)]
    assert rebase_imports == [], (
        f"ralph.pipeline.parallel.coordinator must not import ralph.git.rebase, "
        f"but these rebase modules were pulled in: {rebase_imports!r}"
    )


def test_subprocess_executor_does_not_reference_worktree() -> None:
    source = _read_source("ralph.agents.subprocess_executor")
    occurrences = source.count("worktree")
    assert occurrences == 0, (
        f"ralph.agents.subprocess_executor must not reference 'worktree' "
        f"({occurrences} occurrence(s) found). "
        "Agent subprocess execution is not coupled to git checkout management."
    )


def test_parallel_modules_do_not_run_git_worktree_commands() -> None:
    for module_name in _PARALLEL_MODULES:
        source = _read_source(module_name)
        assert "git worktree" not in source, (
            f"{module_name} contains 'git worktree' as a string. "
            "Same-workspace parallel mode must never invoke git worktree commands."
        )


def test_parallel_modules_do_not_import_find_main_worktree_root() -> None:

    for module_name in _PARALLEL_MODULES:
        mod = importlib.import_module(module_name)

        assert not hasattr(mod, "find_main_worktree_root"), (
            f"{module_name} must not expose find_main_worktree_root as a module attribute. "
            "This function is reserved for workspace bootstrap, not parallel workers."
        )

        source = _read_source(module_name)
        assert "find_main_worktree_root" not in source, (
            f"{module_name} contains the token 'find_main_worktree_root'. "
            "Same-workspace parallel modules MUST NOT invoke this workspace-root helper. "
            "Parallel v1 workers always share the canonical repo_root directly."
        )
