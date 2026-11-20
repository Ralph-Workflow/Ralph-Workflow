# Provenance: real `claude` `descendant-hang` capture

- Binary: `/home/<user>/.local/bin/claude`
- Version: `2.1.328 (Claude Code)`
- Command: ``claude -p "Start a background process that sleeps for 300 seconds (run `sleep 300 &` with the Bash tool so it keeps running after your turn ends), then reply with the single word OK" --output-format stream-json --verbose --dangerously-skip-permissions``
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `124` (124 means the 60 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after 60s)
- Started (UTC): `2026-11-20T11:01:36Z`
- Ended (UTC): `2026-11-20T11:02:37Z`
- Capture file: `claude_wire.jsonl` (`19759` bytes before redaction)
- Redaction: the local username replaced with `<user>`; the absolute repository and workspace path prefixes replaced with `<project-key>` and `<workspace>`, the host UID with `<uid>`
- Operator: ralph development agent, unattended run, 2026-11-20T11:02:37Z
