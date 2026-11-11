#!/usr/bin/env sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/../..:${PYTHONPATH:-}"
export PYTHONPATH
exec python3 "$SCRIPT_DIR/mock_agy.py" "$@"
