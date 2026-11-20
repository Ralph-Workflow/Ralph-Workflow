# Provenance: real `codex` `killed` capture

- Binary: `/home/<user>/.nvm/versions/node/v24.20.0/bin/codex`
- Version: `codex-cli 0.167.2`
- Command: `codex exec --json --ephemeral -m auto/cheap "reply with the single word OK"`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `143` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after `6`s)
- Started (UTC): `2026-11-20T11:08:12Z`
- Ended (UTC): `2026-11-20T11:08:18Z`
- Capture file: `codex_wire.jsonl` (`300` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, `2026-11-20T11:08:18Z`
