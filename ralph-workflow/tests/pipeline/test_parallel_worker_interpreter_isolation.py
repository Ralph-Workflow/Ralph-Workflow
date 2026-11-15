from __future__ import annotations

from pathlib import Path

import pytest

import ralph
from ralph.pipeline import fan_out
from ralph.process.manager import ProcessTerminationIssuer, SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_parallel_worker_interpreter_ignores_broken_ralph_in_its_working_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    broken_pkg = workspace / "ralph"
    broken_pkg.mkdir(parents=True)
    (broken_pkg / "__init__.py").write_text(
        "<<<<<<< HEAD\n=======\n>>>>>>> 42d31a5dd (conflicted checkout)\n",
        encoding="utf-8",
    )
    command = fan_out.parallel_worker_command()
    interpreter = command[: command.index("-m")]
    probe = (
        "import sys\n"
        "from pathlib import Path\n"
        "import ralph\n"
        "sys.exit(0 if Path(ralph.__file__).resolve() == Path(sys.argv[1]) else 3)\n"
    )
    proc = get_process_manager().spawn(
        [*interpreter, "-c", probe, str(Path(ralph.__file__).resolve())],
        SpawnOptions(cwd=str(workspace), label="parallel-worker-isolation-test-child"),
    )
    try:
        rc = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            proc.wait(timeout=2)

    assert command[command.index("-m") + 1] == "ralph"
    assert rc == 0
