"""Regression test for the suite-level teardown-gc configuration.

pytest's unraisableexception plugin runs ``gc_collect_harder(5)`` at the end
of every xdist worker session. On this suite that performs five full
``gc.collect`` passes over the ~12k-test object graph, which was measured at
5.7s+ per worker (~11.5s of the 48.87s baseline ``make test`` run). Setting
the plugin's iteration stash key to 0 keeps the unraisable hook active while
skipping only those teardown gc passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _pytest.unraisableexception import gc_collect_iterations_key

if TYPE_CHECKING:
    import pytest


def test_session_disables_teardown_gc_collect(request: pytest.FixtureRequest) -> None:
    """The suite config must disable the unraisableexception teardown gc loop."""
    assert request.config.stash.get(gc_collect_iterations_key, -1) == 0
