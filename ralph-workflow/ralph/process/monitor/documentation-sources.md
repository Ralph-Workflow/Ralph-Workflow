# Subagent output capture — documentation sources

This file records the external documentation used to ground the
per-agent subagent output discovery strategies in
`ralph/process/monitor/`.

## Claude Code

- Repository and public documentation: https://github.com/anthropics/claude-code
- Context7 index: `/anthropics/claude-code`, accessed 2026-06-14.

Claude Code stores session metadata under `.claude/` in the working
directory. The public repository exposes session metadata at
`.claude/sessions/<number>.json` and transcripts at
`.claude/projects/<project-id>/<session-uuid>.jsonl`. A
security-guidance plugin writes its own log to
`~/.claude/security/log.txt`.

No official documentation documents a stable per-worker subagent output
log path (e.g. `worker-*/log.txt`). The discovery strategy therefore
reports the channel as unavailable rather than guessing a path or format.

## OpenCode

- Repository and documentation: https://github.com/opencode-ai/opencode
- Context7 index: `/opencode-ai/opencode`, accessed 2026-06-14.

OpenCode uses `.opencode/` as its data directory and documents its tools
(glob, grep, ls, view, write, edit, patch, diagnostics), permission
service, and bash tool.

No official documentation documents a stable per-worker subagent output
log path (e.g. `.agent/workers/<worker-id>/output.log`). The discovery
strategy therefore reports the channel as unavailable rather than
guessing a path or format.

## Role classification

Process-tree role classification (host / spawned subagent /
incidental helper) has two independent mechanisms:

1. **Subagent PID discovery via `SubagentPidSource`.** OpenCode emits
   structured child lifecycle events on stdout (e.g. `child_started`
   with a `pid` field). `OpenCodeExecutionStrategy` ingests those
   events into a per-invocation `ChildLivenessRegistry`, and
   `ChildLivenessSubagentPidSource` exposes the registered PIDs to
   `DefaultProcessMonitor`. A descendant PID that is present in the
   source is classified as `SPAWNED_SUBAGENT`. This is first-party
   evidence from the agent's own output stream.

2. **Command-line role classifiers in `_role_classifier.py`.** Each
   transport's classifier cites official documentation. Because none
   of the supported agent CLIs document a stable external signal for
   identifying spawned subagents by command line, process name, or
   environment variable, every command-line classifier degrades
   conservatively to `INCIDENTAL_HELPER`. The PID source takes
   precedence when it is available.

## AGY (Google Antigravity CLI)

- Public documentation: https://antigravity.google/docs/cli-subagents
  (accessed 2026-06-14).
- GitHub repository: https://github.com/google-gemini/gemini-cli
  (accessed 2026-06-14).

Antigravity CLI supports asynchronous subagents and background tasks
managed through an interactive ``/agents`` panel and a ``/tasks``
command. The official documentation does not describe a stable
command-line token, process-name prefix, or environment variable that
an external observer can use to identify spawned subagent processes on
the OS process tree. The command-line role classifier therefore
degrades conservatively to ``INCIDENTAL_HELPER``.

## Codex CLI

- Repository and documentation: https://github.com/openai/codex
  (accessed 2026-06-14).
- Context7 index: ``/openai/codex``, accessed 2026-06-14.

Codex CLI exposes experimental process-management RPCs such as
``process/spawn`` in its app/exec server surfaces. These are server
APIs, not a documented CLI convention for identifying spawned subagent
processes by their command line or process tree from an external
observer. No official Codex CLI documentation documents a stable
subagent-identification signal visible to Ralph. The discovery strategy
therefore reports the channel as unavailable, and the command-line role
classifier degrades conservatively to ``INCIDENTAL_HELPER``.

## Nanocoder

- Repository and documentation: https://github.com/nano-collective/nanocoder
  (accessed 2026-06-14).
- Context7 index: ``/nano-collective/nanocoder``, accessed 2026-06-14.

Nanocoder supports subagents, skills, and a per-project daemon. The
documented CLI surface exposes ``nanocoder daemon`` commands for
managing the daemon and ``DEBUG=nanocoder:*`` for verbose logging, but
does not document a stable per-subagent command-line token or
process-tree signal that Ralph can observe from outside the process.
The discovery strategy therefore reports the channel as unavailable,
and the command-line role classifier degrades conservatively to
``INCIDENTAL_HELPER``.

## Fallback policy

If documented behavior for an agent cannot be established, the
corresponding discovery strategy reports the channel as unavailable
rather than guessing a path or format, and the command-line role
classifier treats all descendants as incidental helpers rather than
guessing a subagent-identifying token. OpenCode subagent PIDs are
identified through the stdout-backed registry described above.
