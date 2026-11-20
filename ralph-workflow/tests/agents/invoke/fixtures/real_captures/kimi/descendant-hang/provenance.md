# Provenance: real `kimi` `descendant-hang` capture

- Binary: `/home/<user>/.kimi-code/bin/kimi`
- Version: `2.3.0`
- Command: ``/home/<user>/.kimi-code/bin/kimi -p "Run exactly this shell command with the Bash tool and do NOT set run_in_background: `nohup sleep 300 >/dev/null 2>&1 &` . The command must return immediately. Then reply with the single word OK" --output-format stream-json``
- Working directory: a scratch under the repository `tmp/` directory
- Exit status: `0` (124 means the 120 s timeout fired; 143 means the child was terminated by SIGTERM)
- Killed by signal: `no` (SIGTERM delivered to the child PID after `0`s)
- Started (UTC): `2026-11-20T11:27:04Z`
- Ended (UTC): `2026-11-20T11:27:15Z`
- Capture file: `kimi_wire.jsonl` (`645` bytes before redaction)
- Redaction: the local username replaced with `<user>`; no absolute repository path occurs in the capture file or this document, so no `<abs-path>` replacement was needed
- Operator: ralph development agent, unattended run, `2026-11-20T11:27:15Z`
