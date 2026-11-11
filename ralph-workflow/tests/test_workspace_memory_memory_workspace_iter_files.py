from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


def test_memory_workspace_iter_files_excludes_shared_generated_build_directory() -> None:
    workspace = MemoryWorkspace()
    workspace.write("source.py", "value = 1\n")
    workspace.write("build/generated.py", "value = 2\n")

    files = workspace.iter_files(".")

    assert "source.py" in files
    assert "build/generated.py" not in files


def test_memory_workspace_iter_files_skips_a_generated_base_directory() -> None:
    workspace = MemoryWorkspace()
    workspace.write(".git/config", "[core]\n")

    assert workspace.iter_files(".git") == ()
