# Provenance: real `codex` `descendant-hang` capture

- Binary: `/home/<user>/.nvm/versions/node/v24.20.0/bin/codex`
- Version: `codex-cli 0.167.2`
- Command: `codex exec --json --ephemeral -m auto/cheap "Start a background process that sleeps for 300 seconds (run `sleep 300 &` with the shell so it keeps running after your turn ends), then reply with the single word OK"`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 60 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (no signal was delivered to the child PID; the CLI exited on its own after `24.297`s while the background `sleep 300` descendant it had started was still alive)
- Started (UTC): `2026-11-20T11:24:00Z`
- Ended (UTC): `2026-11-20T11:24:24Z`
- Capture file: `codex_wire.jsonl` (`1626` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>` (the capture carried neither an absolute repository path nor the local username, so the byte count is unchanged)
- Operator: ralph development agent, unattended run, `2026-11-20T11:24:24Z`
