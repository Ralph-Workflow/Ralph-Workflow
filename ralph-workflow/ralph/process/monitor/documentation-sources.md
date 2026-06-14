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
`.claude/projects/<project-id>/<session-uuid>.jsonl`. Subagent worker
output is written to per-worker log files under the session directory;
the `worker-*/log.txt` convention is treated as available only when
that directory layout is actually present on disk.

## OpenCode

- Repository and documentation: https://github.com/opencode-ai/opencode
- Context7 index: `/opencode-ai/opencode`, accessed 2026-06-14.

OpenCode uses `.opencode/` as its data directory and `.agent/` for
project-local agent state. Subagent worker output is discovered under
`.agent/workers/<worker-id>/output.log`; the strategy returns an empty
mapping when that layout is not present.

## Fallback policy

If documented behavior for an agent cannot be established, the
corresponding discovery strategy reports the channel as unavailable
rather than guessing a path or format.
