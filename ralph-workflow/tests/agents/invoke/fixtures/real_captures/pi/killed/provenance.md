# Provenance: real `pi` `killed` capture

- Binary: `/home/<user>/.nvm/versions/node/v24.20.0/bin/pi`
- Version: `1.1.0`
- Command: `pi -p --mode json reply with the single word OK`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `143` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after `6`s)
- Started (UTC): `2026-11-20T11:02:25Z`
- Ended (UTC): `2026-11-20T11:02:31Z`
- Capture file: `pi_wire.jsonl` (`171820` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, 2026-11-20T11:02:31Z
