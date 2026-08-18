"""Child-process helper for the AGY config-overlay cross-process E2E test.

Invoked as::

    python tests/_support/_agy_config_overlay_child.py \
        [--raise-inside] <primary_config> <secondary_config> \
        <hold_seconds> <timeout_seconds>

The child monkeypatches AGY's two global config paths to the supplied
temporary paths, shrinks the advisory-lock budget to ``timeout_seconds``,
opens :func:`ralph.mcp.transport.agy.agy_workspace_mcp_endpoint`, prints
``STAGED`` once the Ralph endpoint is provably staged, holds the overlay
for ``hold_seconds``, then exits. A child that cannot acquire the
cross-process lock inside the deadline prints ``LOCK_TIMEOUT`` and exits
with code 3 so the parent can assert the fail-closed path. With
``--raise-inside`` the child raises inside the overlay body instead and
prints ``FAILED_AND_RESTORED`` after the context manager restores both
configs, proving exception-safe cleanup.

Kept as a real file (rather than an inline ``-c`` program) so the child
gets a clean module import environment and the program is reviewable in
one place. Used only by
``tests/test_agy_config_overlay_cross_process_e2e.py`` (``subprocess_e2e``).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

# The outer checkout's installed ``sitecustomize`` shim force-moves ITS
# OWN project root to the FRONT of ``sys.path`` at interpreter startup
# (ahead of any ``PYTHONPATH``), so without this correction a bare child
# ``python`` would resolve ``import ralph`` to the WRONG checkout's
# package -- one that may predate the overlay lock under test. Inserting
# this worktree's repo root at position 0 HERE (after ``sitecustomize``
# has already run, before ``import ralph``) is the only ordering that
# survives that shim. See ``_child_env`` in
# ``tests/test_agy_config_overlay_cross_process_e2e.py``.
_repo_root = os.environ.get("RALPH_AGY_OVERLAY_CHILD_REPO_ROOT")
if _repo_root:
    sys.path.insert(0, _repo_root)


def main() -> int:
    """Run the overlay-hold child contract; return the process exit code."""
    # Deferred import: must run AFTER the sys.path correction above so the
    # child resolves ``ralph`` from THIS worktree, not the outer checkout
    # (see the header comment). Function scope keeps the module top-level
    # import-clean (E402) without a noqa bypass.
    from ralph.mcp.transport import agy as agy_transport

    args = list(sys.argv[1:])
    raise_inside = "--raise-inside" in args
    if raise_inside:
        args.remove("--raise-inside")
    primary = Path(args[0])
    secondary = Path(args[1])
    hold_seconds = float(args[2])
    timeout_seconds = float(args[3])

    agy_transport._agy_global_config_path = lambda: primary
    agy_transport._agy_secondary_config_path = lambda: secondary
    # Shrink the lock budget so the contending case resolves quickly. The
    # endpoint's context manager rebinds this module attribute at call
    # time, so assigning it here changes the effective deadline.
    agy_transport._AGY_CONFIG_LOCK_TIMEOUT_SECONDS = timeout_seconds

    workspace = primary.parent.parent
    try:
        with agy_transport.agy_workspace_mcp_endpoint(workspace, "http://127.0.0.1:9/mcp"):
            # While inside the overlay the Ralph endpoint MUST be staged.
            staged = json.loads(primary.read_text(encoding="utf-8"))
            assert "ralph" in staged.get("mcpServers", {}), staged
            print("STAGED", flush=True)
            if raise_inside:
                raise RuntimeError("injected overlay failure")
            threading.Event().wait(timeout=hold_seconds)
    except agy_transport.AgyMcpConfigLockTimeoutError:
        print("LOCK_TIMEOUT", flush=True)
        return 3
    except RuntimeError:
        print("FAILED_AND_RESTORED", flush=True)
        return 0
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
