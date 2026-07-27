"""Entry point for `ralph-prompt` — alternate entrypoint for prompt-helper mode.

Launches the same interactive prompt-refinement flow as `ralph --prompt-helper`.
Installed as the `ralph-prompt` executable when the package is installed via pip.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Launch the interactive prompt-helper (same as `ralph --prompt-helper`)."""
    from ralph.config.bootstrap import (
        ensure_global_agents_config,
        ensure_global_config,
        ensure_global_mcp_config,
        ensure_global_policy_configs,
    )
    from ralph.config.loader import load_config
    from ralph.workspace.scope import resolve_workspace_scope

    try:
        ensure_global_config()
        ensure_global_agents_config()
        ensure_global_mcp_config()
        ensure_global_policy_configs()
        workspace_scope = resolve_workspace_scope()
        workspace_root = workspace_scope.root
        cfg = load_config(None, {}, workspace_scope=workspace_scope)
    except Exception as exc:
        # DA-005 (wt-028-display AC-10): route through the project's
        # shared logging sink rather than bare ``print(..., file=sys.stderr)``
        # so the failure reaches the operator via the same channel as every
        # other startup-time error and no command reaches the terminal
        # through a private path.
        _emit_startup_failure(f"Error starting ralph-prompt: {exc}")
        sys.exit(1)

    from ralph.cli.commands.prompt_helper import run_prompt_helper as _run_prompt_helper

    _run_prompt_helper(cfg, workspace_root)


def _emit_startup_failure(message: str) -> None:
    """Emit a startup-failure message through the shared logging sink.

    DA-005 (wt-028-display AC-10): no command reaches the terminal via a
    private path. The CLI bootstrap path always calls
    :func:`ralph.logging.configure_logging` before any user-facing output
    is produced, so by the time ``main()`` runs the project's loguru
    sink is the canonical owner of the stderr surface. At the very
    first call into ``main()`` (before ``configure_logging`` has fired)
    the function falls back to ``logger.error`` so the message still
    surfaces via the same loguru handler the rest of the project uses.
    """
    import logging as _stdlib_logging

    logger = _stdlib_logging.getLogger("ralph.cli._prompt_helper_entry")
    logger.error(message)
