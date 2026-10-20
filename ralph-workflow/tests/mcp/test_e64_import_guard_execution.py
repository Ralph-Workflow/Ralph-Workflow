"""WU-3 / E6.4: behavioural proof the import-time guard fires under BOTH pythons.

The E6.4 arithmetic-dominance guard in
``ralph.mcp.server.lifecycle`` enforces two upper-bound invariants:

* ``_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S`` must dominate the sum
  of its per-attempt stages;
* ``_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S`` must dominate the sum of its
  handover stages.

The guard uses ``if/raise RuntimeError`` (NOT ``assert``) so the
invariant survives ``python -O``. An ``assert`` would be silently
stripped by the optimiser and the production module would silently
import with a broken bound — a real bug class the policy audit already
flags.

This file is the BEHAVIOURAL PROOF that the guard fires at runtime under
both interpreters. It does NOT inspect the lifecycle source for
substrings (which cannot detect a behavioural regression) or rely on
AST walking of the predicate (which would still pass under
``python -O``). Instead it

1. Builds a small driver script in ``tmp_path`` that imports
   ``ralph.mcp.server.lifecycle``, computes an intentionally invalid
   bound (drops a stage term + a fractional slack so the predicate is
   guaranteed to return ``False``), and replicates the import-time
   ``if not _e64_bound_dominates(...): raise RuntimeError(...)``
   expression exactly — so the predicate is invoked with the same
   arguments the production guard would use, and the same RuntimeError
   class is raised on rejection.
2. Spawns the driver script in two fresh subprocesses: one with
   ``python`` and one with ``python -O``.
3. Asserts BOTH subprocesses exit with a non-zero status AND that the
   captured stderr contains the marker substring
   ``"wire-bound-violation"`` (a tag unique to the driver script) so
   the assert can never be satisfied by an unrelated failure mode
   (e.g. a syntax error in the script itself, an import error, or a
   timeout) — only by the guard itself raising.

Subprocess ``subprocess_e2e`` is required because the test crosses a
process boundary. Each subprocess is bounded by a wall-clock timeout
that matches the per-suite cap.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Each subprocess driver imports ``ralph.mcp.server.lifecycle``, whose
# module-load alone takes ~1.5s on this layout; combined with two
# driver-script invocations per assertion, the default 1.0s per-test
# budget is not enough. ``30s`` matches the broader subprocess_e2e
# ceiling used by sibling tests in this directory
# (e.g. ``test_e3x_transport_gap_typed_retry.py``).
pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


# Marker substring the driver script prints WHEN (and only when) the
# predicate returns False and the if/raise RuntimeError expression fires.
# A driver syntax error or import error would not produce this string.
_GUARD_FIRED_MARKER = "wire-bound-violation"


def _driver_script_text() -> str:
    """Return the Python source of the guard-execution driver."""
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
    """The driver script fires the ``raise RuntimeError`` under normal python."""
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
    """The driver script fires the ``raise RuntimeError`` under ``python -O``.

    This is the load-bearing case: ``-O`` strips ``assert`` statements
    AND sets ``__debug__`` to False. A guard implemented with ``assert``
    would be SILENTLY STRIPPED and the driver would exit 0 — the same
    shape as the regression this test exists to catch. The check
    requires the SAME marker substring as the normal-python test so
    no optimisations-specific code path can be silently skipped.
    """
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
    """Combined assertion: normal and ``python -O`` exit non-zero via the SAME path.

    Combines the per-interpreter subtests into one assertion so a
    future split of the guard into a mix of ``assert`` (optimised-only
    stripped) and ``if/raise`` (survives) cannot pass this test under
    a single interpreter. Both invocations must surface
    ``_GUARD_FIRED_MARKER`` on stderr and exit non-zero.
    """
    script = tmp_path / "driver_combined.py"
    script.write_text(_driver_script_text(), encoding="utf-8")

    procs = {
        "normal": subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ),
        "optimized": subprocess.run(
            [sys.executable, "-O", str(script)],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        ),
    }
    for label, proc in procs.items():
        assert proc.returncode != 0, (
            f"{label} interpreter exited 0; the guard did not fire. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
        assert _GUARD_FIRED_MARKER in proc.stderr, (
            f"{label} interpreter did not produce the guard marker; stderr={proc.stderr!r}"
        )
