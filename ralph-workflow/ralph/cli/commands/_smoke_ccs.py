"""CCS (Claude Code Switch) smoke command implementation.

Extracted from :mod:`ralph.cli.commands.smoke` so the main smoke module
can stay under the 1000-line audit cap (see
:mod:`ralph.testing.audit_repo_structure`), mirroring the
``_smoke_opencode_override.py`` split for the OpenCode binary-override
helpers.

Dependencies that a test needs to override (``resolve_workspace_scope``,
``load_config``, ``AgentRegistry``) are accessed through their owning
PUBLIC module objects (``ralph.workspace.scope``, ``ralph.config.loader``,
``ralph.agents.registry``) rather than imported as bare names, so tests
can ``monkeypatch.setattr`` those canonical public modules directly --
the repo-structure audit forbids test files from importing this private
``_``-prefixed submodule. ``smoke_harness_agent_command`` is imported
lazily (inside the function body, not at module level) from ``smoke.py``
because ``smoke.py`` imports this module at its own module level -- a
module-level import back would be circular; tests patch that one seam on
``ralph.cli.commands.smoke`` instead, exactly as they already do for the
AGY / Cursor / Nanocoder commands that still live directly in ``smoke.py``.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents import registry as _registry_module
from ralph.config import loader as _loader_module
from ralph.config.enums import AgentTransport
from ralph.workspace import scope as _scope_module

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pro_support.hooks import ProPipelineHooks

__all__ = ["smoke_interactive_ccs_command"]


def smoke_interactive_ccs_command(
    agent_name: str = "ccs/glm",
    *,
    display_context: DisplayContext | None = None,
    pro_hooks: ProPipelineHooks | None = None,
    model_identity: MultimodalModelIdentity | None = None,
    subagents: bool = False,
    subagent_prompt_file: Path | None = None,
    multimodal: bool = False,
) -> int:
    """Run the manual smoke harness for a CCS (Claude Code Switch) alias.

    The alias follows the dynamic ``ccs/<alias>`` pattern (e.g.
    ``ccs/glm``); the command builder strips the leading ``ccs/`` and
    passes ``<alias>`` to the configured ``ccs`` wrapper as
    ``ccs <alias> --print --output-format=stream-json ...`` (headless
    Claude flags -- see :class:`ralph.config.ccs_config.CcsConfig`).
    ``ccs/<alias>`` resolves even without a ``[ccs_aliases]`` config
    entry (the registry synthesizes ``cmd="ccs <alias>"`` dynamically),
    so this command only rejects aliases that resolve to a non-CCS
    command, not aliases missing from config.

    Like every other ``smoke-interactive-*`` command this consumes live
    tokens and is therefore OUTSIDE ``make verify``; it only runs when
    an operator invokes it.
    """
    if shutil.which("ccs") is None:
        logger.error(
            "ccs binary not found. Install Claude Code Switch (CCS) and ensure "
            "`ccs` is on PATH."
        )
        return 2

    workspace_scope = _scope_module.resolve_workspace_scope()
    config = _loader_module.load_config(None, {}, workspace_scope=workspace_scope)
    registry = _registry_module.AgentRegistry.from_config(config)
    agent_config = registry.get(agent_name)
    if agent_config is None:
        logger.error(
            "Agent '{}' is not available. Use --agent with a ccs/<alias> alias, "
            "e.g. --agent 'ccs/glm'.",
            agent_name,
        )
        return 2
    if (
        agent_config.transport is None
        or agent_config.transport != AgentTransport.CLAUDE
        or not agent_config.cmd.startswith("ccs")
    ):
        logger.error(
            "Agent '{}' resolves to command '{}' (transport '{}'), not a CCS "
            "command. Use --agent with a ccs/<alias> alias.",
            agent_name,
            agent_config.cmd,
            agent_config.transport.value if agent_config.transport else "None",
        )
        return 2

    from ralph.cli.commands.smoke import smoke_harness_agent_command

    return smoke_harness_agent_command(
        agent_name,
        display_context=display_context,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
        subagents=subagents,
        subagent_prompt_file=subagent_prompt_file,
        multimodal=multimodal,
    )
