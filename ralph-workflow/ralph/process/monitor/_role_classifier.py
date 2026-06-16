"""Transport-specific role classifiers for process-tree classification.

A role classifier decides whether a descendant process in the agent process tree
is a spawned subagent doing delegated work, or merely an incidental helper such
as a tool subprocess, MCP server worker, or shell.

Every supported agent transport's official documentation does not describe a stable
subagent-identification signal visible to an external observer. Because the
classification must be grounded in documented behavior, all classifiers degrade
conservatively and treat every descendant as ``INCIDENTAL_HELPER``.

For OpenCode, spawned subagents are identified separately via the injected
``SubagentPidSource`` (backed by the ``ChildLivenessRegistry``) because OpenCode
emits structured child lifecycle events on stdout that carry the child PID.
That first-party evidence is used before the command-line classifier is
consulted. The command-line classifier in this module therefore remains
conservative for every transport.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ._process_monitor import ProcessRole

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport

RoleClassifier = Callable[[int, list[str] | None], ProcessRole]


def _conservative_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Conservative fallback: every descendant is an incidental helper.

    Used for agent transports whose official documentation does not describe a
    stable command-line, process-name, or environment-variable signal for
    distinguishing spawned subagents from other descendants of the host
    process.
    """
    return ProcessRole.INCIDENTAL_HELPER


def role_classifier_for_transport(transport: AgentTransport | None) -> RoleClassifier:
    """Return the documentation-grounded role classifier for an agent transport.

    Each returned classifier is a function ``(pid, cmdline) -> ProcessRole``.
    For every supported transport the current official documentation does not
    expose a stable external subagent-identification signal, so the
    classifier degrades conservatively to ``INCIDENTAL_HELPER`` for all
    descendants. This avoids the false-positive misclassification that broad
    substring heuristics (e.g. matching ``worker``, ``task``, ``agent``) can
    produce.

    Args:
        transport: The agent transport whose classifier is requested. ``None``
            is treated like ``AgentTransport.GENERIC`` and returns the
            conservative classifier.

    Returns:
        A ``RoleClassifier`` that never invents undocumented behavior.
    """
    return _conservative_role_classifier
