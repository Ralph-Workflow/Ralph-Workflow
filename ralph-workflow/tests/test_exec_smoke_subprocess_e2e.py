"""Stub for the wt-19 exec smoke tests.

The real subprocess_e2e tests land via U-SMOKE. The marker contract
required for make test selection is preserved so the registry entry
added by U-GATE is valid before the real tests arrive.
"""

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e]


def test_placeholder() -> None:
    """Placeholder — replaced by U-SMOKE."""
