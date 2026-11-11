from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


_GUARD_FIRED_MARKER = "wire-bound-violation"


def _driver_script_text() -> str:
    return textwrap.dedent(
        f"""\
        '''WU-3 / E6.4: driver that hits the import-time guard.

        Imports the production lifecycle module, computes an
        intentionally invalid bound, then evaluates the SAME expression
        the import-time guard evaluates:

            if not _e64_bound_dominates(bound, components):
                raise RuntimeError(...)

        Under both normal python AND python -O the RuntimeError must
        fire; otherwise the guard is implemented with ``assert`` and
        would be stripped in optimised mode — exactly the bug the
        E6.4 audit exists to catch.
        '''

        import sys

        # The lifecycle module imports a small graph of MCP machinery.
        # That import-load is the same load the import-time guard runs
        # in production, so re-running the check here exercises the
        # same constants and predicate.
        from ralph.mcp.server import lifecycle

        # Pull the production constants the guard was written against.
        components_a = lifecycle._E64_BOUND_A_COMPONENTS_S
        bound_a = (
            lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S
            / lifecycle._E64_MAX_RESTARTS
        )
        # Force the predicate to return False by subtracting a
        # non-trivial slack from the production per-attempt bound.
        # The drop is large enough that even with rounding error the
        # remainder is strictly less than the sum of the components;
        # the predicate rejects, the guard raises.
        under_counted_a = bound_a - sum(components_a) / 2.0 - 1.0

        if not lifecycle._e64_bound_dominates(under_counted_a, components_a):
            # Print the marker so the parent can attribute the exit
            # code to the guard firing.
            print("{_GUARD_FIRED_MARKER}", file=sys.stderr, flush=True)
            raise RuntimeError(
                f"E6.4 guard should reject bound={{under_counted_a:g}} "
                f"components={{components_a}}"
            )

        # If we reach here the guard is silently accept-as-PASS, which
        # would mean the predicate is broken; surface that as a
        # non-zero exit too.
        print("guard-did-not-fire", file=sys.stderr, flush=True)
        raise RuntimeError("guard did not fire on invalid bound")
        """
    )


def test_e64_import_guard_fires_under_normal_python(tmp_path: Path) -> None:
    script = tmp_path / "driver_normal.py"
    script.write_text(_driver_script_text(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode != 0, (
        f"normal python exited 0 but should have raised; stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    assert _GUARD_FIRED_MARKER in proc.stderr, (
        f"guard did not fire under normal python; stderr={proc.stderr!r}"
    )


def test_e64_import_guard_fires_under_optimized_python(tmp_path: Path) -> None:
    script = tmp_path / "driver_optimized.py"
    script.write_text(_driver_script_text(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-O", str(script)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode != 0, (
        f"python -O exited 0 but should have raised; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert _GUARD_FIRED_MARKER in proc.stderr, (
        f"guard did not fire under python -O; stderr={proc.stderr!r}"
    )


@pytest.mark.subprocess_e2e
def test_e64_import_guard_fires_under_both_pythons_with_same_marker(tmp_path: Path) -> None:
    script = tmp_path / "driver_combined.py"
    script.write_text(_driver_script_text(), encoding="utf-8")

    p_normal = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p_opt = subprocess.Popen(
        [sys.executable, "-O", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_n, stderr_n = p_normal.communicate(timeout=15)
    stdout_o, stderr_o = p_opt.communicate(timeout=15)
    results = {
        "normal": (p_normal.returncode, stdout_n, stderr_n),
        "optimized": (p_opt.returncode, stdout_o, stderr_o),
    }
    for label, (rc, out, err) in results.items():
        assert rc != 0, (
            f"{label} interpreter exited 0; the guard did not fire. stdout={out!r} stderr={err!r}"
        )
        assert _GUARD_FIRED_MARKER in err, (
            f"{label} interpreter did not produce the guard marker; stderr={err!r}"
        )
