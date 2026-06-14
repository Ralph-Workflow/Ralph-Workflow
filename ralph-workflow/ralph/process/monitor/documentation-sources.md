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

The same documentation-grounded rule applies to process-tree role
classification (host / spawned subagent / incidental helper). Each
transport's role classifier in `_role_classifier.py` cites the same
official sources. Because none of the supported agent CLIs document a
stable external signal for identifying spawned subagents by command
line, process name, or environment variable, every classifier degrades
conservatively: descendants are classified as `INCIDENTAL_HELPER`
unless the caller injects a transport-specific, documented classifier.

## Fallback policy

If documented behavior for an agent cannot be established, the
corresponding discovery strategy reports the channel as unavailable
rather than guessing a path or format, and the role classifier treats
all descendants as incidental helpers rather than guessing a
subagent-identifying token.
