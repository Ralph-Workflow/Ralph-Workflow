"""Transport-specific role classifiers for process-tree classification.

A role classifier decides whether a descendant process in the agent process tree
is a spawned subagent doing delegated work, or merely an incidental helper such
as a tool subprocess, MCP server worker, or shell. Every classifier in this
module is grounded in the official documentation for its agent CLI. When the
documented surface does not expose a stable subagent-identification signal, the
classifier degrades conservatively and treats all descendants as
``INCIDENTAL_HELPER`` rather than guessing.

For OpenCode, spawned subagents are identified separately via the injected
``SubagentPidSource`` (backed by the ``ChildLivenessRegistry``) because OpenCode
emits structured child lifecycle events on stdout that carry the child PID.
That first-party evidence is used before the command-line classifier is
consulted. The command-line classifiers in this module therefore remain
conservative for every transport.
"""

from __future__ import annotations

from collections.abc import Callable

from ralph.config.enums import AgentTransport

from ._process_monitor import ProcessRole

RoleClassifier = Callable[[int, list[str] | None], ProcessRole]


def _conservative_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Conservative fallback: every descendant is an incidental helper.

    Used for agent transports whose official documentation does not describe a
    stable command-line, process-name, or environment-variable signal for
    distinguishing spawned subagents from other descendants of the host
    process.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _claude_code_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Claude Code role classifier.

    Claude Code supports subagents (see
    https://docs.claude.com/en/docs/claude-code/sub-agents) and stores session
    metadata under ``.claude/`` in the working directory. Context7
    ``/anthropics/claude-code`` (accessed 2026-06-14) confirms the CLI manages
    child processes such as MCP servers via stdin/stdout JSON-RPC, but does not
    document a stable per-subagent command-line token, process-name prefix, or
    environment variable that Ralph can observe from outside the process.

    Because the classification must be grounded in documented behavior
    (AC-11), this classifier degrades conservatively: every descendant of the
    host is treated as ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _opencode_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """OpenCode command-line role classifier.

    OpenCode uses ``.opencode/`` as its data directory and documents its tools,
    permission service, and shell configuration (Context7
    ``/opencode-ai/opencode``, accessed 2026-06-14). The modular architecture
    does not expose a stable per-subagent command-line signature or documented
    process-tree convention that Ralph can observe from outside the process.

    OpenCode subagents are instead identified via the injected
    ``SubagentPidSource`` backed by the ``ChildLivenessRegistry``, which uses
    the structured child lifecycle events OpenCode emits on stdout. This
    command-line classifier therefore degrades conservatively to
    ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _codex_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Codex CLI role classifier.

    Codex CLI exposes experimental process-management RPCs such as
    ``process/spawn`` in its app/exec server surfaces (Context7
    ``/openai/codex``, accessed 2026-06-14). These are server APIs, not a
    documented CLI convention for identifying spawned subagent processes by
    their command line or process tree from an external observer. No official
    Codex CLI documentation documents a stable subagent-identification signal
    visible to Ralph.

    Because the classification must be grounded in documented behavior
    (AC-11), this classifier degrades conservatively: every descendant of the
    host is treated as ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _nanocoder_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Nanocoder role classifier.

    Nanocoder supports subagents, skills, and a per-project daemon (Context7
    ``/nano-collective/nanocoder``, accessed 2026-06-14). The documented CLI
    surface exposes ``nanocoder daemon`` commands for managing the daemon and
    ``DEBUG=nanocoder:*`` for verbose logging, but does not document a stable
    per-subagent command-line token or process-tree signal that Ralph can
    observe from outside the process.

    Because the classification must be grounded in documented behavior
    (AC-11), this classifier degrades conservatively: every descendant of the
    host is treated as ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _agy_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """AGY (Google Antigravity CLI) role classifier.

    Antigravity CLI supports asynchronous subagents and background tasks
    (https://antigravity.google/docs/cli-subagents, accessed 2026-06-14). The
    documentation describes an interactive ``/agents`` panel and a ``/tasks``
    command for managing background work inside the terminal UI, but does not
    document a stable command-line token, process-name prefix, or environment
    variable that an external observer can use to identify spawned subagent
    processes on the OS process tree. The GitHub repository
    ``google-gemini/gemini-cli`` does not document such a signal either.

    Because the classification must be grounded in documented behavior
    (AC-11) and no documented external signal exists, this classifier degrades
    conservatively: every descendant of the host is treated as
    ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def _generic_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    """Generic agent role classifier.

    The generic transport has no transport-specific documentation to consult
    for subagent identification. The classifier therefore degrades
    conservatively: every descendant of the host is treated as
    ``INCIDENTAL_HELPER``.
    """
    return ProcessRole.INCIDENTAL_HELPER


def role_classifier_for_transport(transport: AgentTransport | None) -> RoleClassifier:
    """Return the documentation-grounded role classifier for an agent transport.

    Each returned classifier is a function ``(pid, cmdline) -> ProcessRole``.
    For every supported transport the current official documentation does not
    expose a stable external subagent-identification signal, so every
    classifier degrades conservatively to ``INCIDENTAL_HELPER`` for
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
    if transport is None:
        return _generic_role_classifier
    mapping: dict[AgentTransport, RoleClassifier] = {
        AgentTransport.CLAUDE: _claude_code_role_classifier,
        AgentTransport.CLAUDE_INTERACTIVE: _claude_code_role_classifier,
        AgentTransport.OPENCODE: _opencode_role_classifier,
        AgentTransport.CODEX: _codex_role_classifier,
        AgentTransport.NANOCODER: _nanocoder_role_classifier,
        AgentTransport.AGY: _agy_role_classifier,
        AgentTransport.GENERIC: _generic_role_classifier,
    }
    return mapping.get(transport, _conservative_role_classifier)
