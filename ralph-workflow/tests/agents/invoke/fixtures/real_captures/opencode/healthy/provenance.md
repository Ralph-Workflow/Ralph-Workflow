# Provenance: real `opencode` `healthy` capture

- Binary: `/home/<user>/.local/bin/opencode`
- Version: `1.19.4`
- Command: `opencode run --format json --auto --model opencode/longcat-2.5-free --dir <abs-path>/tmp/rec-opencode-healthy reply with the single word OK`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (SIGTERM delivered to the child PID after 0s)
- Started (UTC): `2026-11-20T11:02:56Z`
- Ended (UTC): `2026-11-20T11:03:09Z`
- Capture file: `opencode_wire.jsonl` (`1030` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, 2026-11-20T11:03:09Z
