# Provenance: real `opencode` `killed` capture

- Binary: `/home/<user>/.local/bin/opencode`
- Version: `1.19.4`
- Command: `opencode run --format json --auto --model opencode/longcat-2.5-free --dir <abs-path>/tmp/rec-opencode-killed reply with the single word OK`
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `143` (124 means the 45 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `yes` (SIGTERM delivered to the child PID after 12s)
- Started (UTC): `2026-11-20T11:05:00Z`
- Ended (UTC): `2026-11-20T11:05:12Z`
- Capture file: `opencode_wire.jsonl` (`304` bytes before redaction)
- Redaction: absolute repository paths replaced with `<abs-path>`, the local username with `<user>`
- Operator: ralph development agent, unattended run, 2026-11-20T11:05:12Z
