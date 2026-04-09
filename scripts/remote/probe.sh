#!/usr/bin/env bash
# Exit 0 if rw-build-server is reachable, non-zero otherwise.
# ConnectTimeout=5 caps the fallback latency to at most 5 seconds.
# BatchMode=yes prevents interactive password prompts (fails fast if key auth is not set up).
ssh -o ConnectTimeout=5 -o BatchMode=yes rw-build-server exit 0 2>/dev/null
