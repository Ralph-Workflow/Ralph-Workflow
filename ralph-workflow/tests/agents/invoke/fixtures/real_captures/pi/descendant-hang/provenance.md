# Provenance: real `pi` `descendant-hang` capture

- Binary: `/home/<user>/.nvm/versions/node/v24.20.0/bin/pi`
- Version: `1.1.0`
- Command: ``pi -p --mode json "Run exactly this shell command with the bash tool and do NOT wait for it: `nohup sleep 300 >/dev/null 2>&1 &` . The command must return immediately. Then reply with the single word OK"``
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 120 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (SIGTERM delivered to the child PID after `0`s)
- Started (UTC): `2026-11-20T11:49:08Z`
- Ended (UTC): `2026-11-20T11:49:20Z`
- Capture file: `pi_wire.jsonl` (`261320` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, `2026-11-20T11:49:20Z`
