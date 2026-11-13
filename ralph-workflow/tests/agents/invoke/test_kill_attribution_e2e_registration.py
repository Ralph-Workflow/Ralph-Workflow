from __future__ import annotations

import pytest

from ralph.test_suites import (
    REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    validate_required_auto_integrate_selection,
)

_E2E_PATH = "tests/agents/invoke/test_kill_attribution_subprocess_e2e.py"


def test_kill_attribution_e2e_is_required_by_default_verify_selection() -> None:
    assert _E2E_PATH in REQUIRED_AUTO_INTEGRATE_E2E_FILES

    selection_without_e2e = tuple(
        path for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES if path != _E2E_PATH
    )
    with pytest.raises(RuntimeError, match=_E2E_PATH):
        validate_required_auto_integrate_selection(selection_without_e2e)
