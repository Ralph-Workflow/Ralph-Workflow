# Provenance: real `opencode` `descendant-hang` capture

- Binary: `/home/<user>/.local/bin/opencode`
- Version: `1.19.4`
- Command: `opencode run --format json --auto --model opencode/longcat-2.5-free --dir <abs-path>/tmp/rec-opencode-descendant-hang "Start a background process that sleeps for 300 seconds, detached so that it keeps running after your turn ends, then reply with the single word OK"`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 150 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (SIGTERM delivered to the child PID after 0s)
- Started (UTC): `2026-11-20T11:25:19Z`
- Ended (UTC): `2026-11-20T11:26:15Z`
- Capture file: `opencode_wire.jsonl` (`2835` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, 2026-11-20T11:26:15Z
