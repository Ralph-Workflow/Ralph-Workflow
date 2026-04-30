"""Top-level DI-contract entry point.

Re-exports the canonical DI contract test suite from
tests/unit/display/test_di_contract.py so that both
``pytest tests/test_di_contract.py`` and
``pytest tests/unit/display/test_di_contract.py``
collect the same suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_canonical_path = Path(__file__).parent / "unit" / "display" / "test_di_contract.py"
_spec = importlib.util.spec_from_file_location("_test_di_contract_canonical", _canonical_path)
assert _spec is not None and _spec.loader is not None, (
    f"Cannot locate canonical DI contract test module at {_canonical_path}"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["_test_di_contract_canonical"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

TestRenderersRequireDisplayContext = _mod.TestRenderersRequireDisplayContext
TestColorDisabledPropagates = _mod.TestColorDisabledPropagates
TestCompactModeLimits = _mod.TestCompactModeLimits
TestRefreshedPicksUpNewWidth = _mod.TestRefreshedPicksUpNewWidth
TestNoLiteralStyles = _mod.TestNoLiteralStyles
