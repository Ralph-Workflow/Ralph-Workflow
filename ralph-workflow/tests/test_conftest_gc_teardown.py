from __future__ import annotations

from typing import TYPE_CHECKING

from _pytest.unraisableexception import gc_collect_iterations_key

if TYPE_CHECKING:
    import pytest


def test_session_disables_teardown_gc_collect(request: pytest.FixtureRequest) -> None:
    assert request.config.stash.get(gc_collect_iterations_key, -1) == 0
