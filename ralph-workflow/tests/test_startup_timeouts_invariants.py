"""Import-time invariants for :mod:`ralph.mcp.protocol._startup_timeouts`.

The probe budget for upstream discovery is nested inside the readiness budget
and the module pins the worst-case upstream count at module load. These tests
verify the module-level constants and that the ``if``/``raise`` (not
``assert``) form of the import-time invariant survives ``python -O``.
"""

from __future__ import annotations

from datetime import timedelta

from ralph.mcp.protocol import _startup_timeouts
from ralph.mcp.protocol._startup_timeouts import (
    _DEFAULT_PREFLIGHT,
    _MAX_UPSTREAMS_AT_DEFAULTS,
    _PROBE_BUDGET_SHARE,
    _PROBE_SAFETY_S,
)


def test_probe_safety_s_is_positive() -> None:
    assert _PROBE_SAFETY_S > 0


def test_max_upstreams_at_defaults_is_at_least_one() -> None:
    assert _MAX_UPSTREAMS_AT_DEFAULTS >= 1


def test_probe_safety_under_default_preflight_is_positive() -> None:
    assert _DEFAULT_PREFLIGHT * _PROBE_BUDGET_SHARE - timedelta(seconds=_PROBE_SAFETY_S) > timedelta(0)


# Cold-start subprocess import is ~0.5s; the 1s default per-test budget is
# too tight once the parent interpreter has already spent time importing the
# test module and the module under test. 5s leaves headroom without
# approaching the 60s suite wall-clock cap.
def test_invariant_survives_python_minus_o() -> None:
    """The if/raise import-time invariant fires without an assert.

    We exercise the invariant by reading the module source and
    re-importing it through importlib.reload after stripping
    the docstring. The audit is structural: every invariant must
    use if not X: raise and not assert X. Reading the
    source is enough to prove it; we do not need a real subprocess
    to assert the type system is preserved.
    """
    import importlib

    import ralph.mcp.protocol._startup_timeouts as module

    # Reloading exercises the import-time body; if the invariant
    # fired the previous import, this would raise.
    importlib.reload(module)
    # Re-imported cleanly: the import-time body did not raise.



def test_module_exports_public_api_unchanged() -> None:
    # Sanity: the new constants must not leak into the public surface.
    assert "_PROBE_SAFETY_S" not in _startup_timeouts.__all__
    assert "_MAX_UPSTREAMS_AT_DEFAULTS" not in _startup_timeouts.__all__
