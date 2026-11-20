# Provenance: real `agy` `descendant-hang` capture

- Binary: `/home/<user>/.local/bin/agy`
- Version: `1.5.13`
- Command: `agy --print "Start a background process that sleeps for 300 seconds, then reply with the single word OK" --output-format stream-json --dangerously-skip-permissions`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `124` (124 means the 60 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after 60s)
- Started (UTC): `2026-11-20T11:03:14Z`
- Ended (UTC): `2026-11-20T11:04:14Z`
- Capture file: `agy_wire.jsonl` (`2510` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, `2026-11-20T11:04:14Z`
