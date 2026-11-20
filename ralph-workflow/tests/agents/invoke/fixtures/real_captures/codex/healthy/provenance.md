# Provenance: real `codex` `healthy` capture

- Binary: `/home/<user>/.nvm/versions/node/v24.20.0/bin/codex`
- Version: `codex-cli 0.167.2`
- Command: `codex exec --json --ephemeral -m auto/cheap "reply with the single word OK"`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (SIGTERM delivered to the child PID after `0`s)
- Started (UTC): `2026-11-20T11:06:51Z`
- Ended (UTC): `2026-11-20T11:07:08Z`
- Capture file: `codex_wire.jsonl` (`659` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, `2026-11-20T11:07:08Z`
