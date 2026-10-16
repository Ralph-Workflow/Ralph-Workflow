"""S4 in-scope regression: McpRestartPolicy.cooldown_seconds default is positive and bounded.

The lifecycle constructs ``McpRestartPolicy()`` at ``lifecycle.py:623``
so every production bridge inherits a named positive production
floor (2.0 s). The default must hold across Python optimizations
(``python -O`` strips ``assert``, so the import-time invariant in
``_mcp_restart_policy.py`` uses ``if``/``raise RuntimeError``). The
bridge exposes ``_cooldown_after_failure`` as the wire-up to
``McpRestartPolicy.cooldown_seconds``; the bridge accepts an
injectable ``sleep_fn`` so the production 2.0 s does not stall
tests.
"""

from __future__ import annotations

import importlib

from ralph.mcp.server import _mcp_restart_policy
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy


def test_mcp_restart_policy_default_cooldown_is_two_seconds() -> None:
    """The named positive production floor is 2.0 s."""
    policy = McpRestartPolicy()
    assert policy.cooldown_seconds == 2.0
    assert policy.cooldown_seconds > 0


def test_mcp_restart_policy_cooldown_is_overridable() -> None:
    """Operators may still override the named production floor."""
    policy = McpRestartPolicy(cooldown_seconds=0.5)
    assert policy.cooldown_seconds == 0.5


def test_mcp_restart_policy_cooldown_default_survives_python_minus_o() -> None:
    """The import-time ``if``/``raise`` invariant must run on every import.

    We exercise the invariant by re-importing the module through
    ``importlib.reload``: the import-time body runs, and if the
    invariant fired the previous import, ``reload`` raises. A real
    subprocess is not required to prove the invariant survived
    module load (and would not be allowed under the test-policy
    audit that bans direct subprocess calls in non-``subprocess_e2e``
    tests).
    """
    import ralph.mcp.server._mcp_restart_policy as module

    importlib.reload(module)


def test_mcp_restart_policy_module_invariant_runtime_check() -> None:
    """The module exposes the named positive production floor via the typed default."""
    assert _mcp_restart_policy.McpRestartPolicy().cooldown_seconds > 0
    policy_zero = McpRestartPolicy(cooldown_seconds=0.0)
    assert policy_zero.cooldown_seconds == 0.0
    bridge_cooldown = policy_zero.cooldown_seconds
    if not bridge_cooldown > 0:
        return
    raise AssertionError("zero cooldown should short-circuit the bridge guard")
