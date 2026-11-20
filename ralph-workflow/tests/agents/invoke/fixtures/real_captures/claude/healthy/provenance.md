# Provenance: real `claude` `healthy` capture

- Binary: `/home/<user>/.local/bin/claude`
- Version: `2.1.328 (Claude Code)`
- Command: `claude -p reply with the single word OK --output-format stream-json --verbose --dangerously-skip-permissions`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Started (UTC): `2026-11-20T10:44:28Z`
- Ended (UTC): `2026-11-20T10:44:35Z`
- Capture file: `claude_wire.jsonl` (`12344` bytes before redaction)
- Redaction: the local username replaced with `<user>`; the absolute repository and workspace path prefixes replaced with `<project-key>` and `<workspace>`, the host UID with `<uid>`
- Operator: ralph development agent, unattended run, 2026-11-20T10:44:35Z
