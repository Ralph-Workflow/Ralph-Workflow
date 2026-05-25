"""Entry point for `ralph-prompt` — alternate entrypoint for prompt-helper mode.

Launches the same interactive prompt-refinement flow as `ralph --prompt-helper`.
Installed as the `ralph-prompt` executable when the package is installed via pip.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Launch the interactive prompt-helper (same as `ralph --prompt-helper`)."""
    from ralph.config.bootstrap import (
        ensure_global_config,
        ensure_global_mcp_config,
        ensure_global_policy_configs,
    )
    from ralph.config.loader import load_config
    from ralph.workspace.scope import resolve_workspace_scope

    try:
        ensure_global_config()
        ensure_global_mcp_config()
        ensure_global_policy_configs()
        workspace_scope = resolve_workspace_scope()
        workspace_root = workspace_scope.root
        cfg = load_config(None, {}, workspace_scope=workspace_scope)
    except Exception as exc:
        print(f"Error starting ralph-prompt: {exc}", file=sys.stderr)
        sys.exit(1)

    from ralph.cli.commands.prompt_helper import run_prompt_helper as _run_prompt_helper

    _run_prompt_helper(cfg, workspace_root)
