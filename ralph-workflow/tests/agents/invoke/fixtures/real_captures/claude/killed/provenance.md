# Provenance: real `claude` `killed` capture

- Binary: `/home/<user>/.local/bin/claude`
- Version: `2.1.328 (Claude Code)`
- Command: `claude -p reply with the single word OK --output-format stream-json --verbose --dangerously-skip-permissions`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `143` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after 7s)
- Started (UTC): `2026-11-20T10:46:00Z`
- Ended (UTC): `2026-11-20T10:46:08Z`
- Capture file: `claude_wire.jsonl` (`8789` bytes before redaction)
- Redaction: the local username replaced with `<user>`; the absolute repository and workspace path prefixes replaced with `<project-key>` and `<workspace>`, the host UID with `<uid>`
- Operator: ralph development agent, unattended run, 2026-11-20T10:46:08Z
