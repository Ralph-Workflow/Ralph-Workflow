#!/bin/bash
cd "$(dirname "$0")/ralph-workflow"
uv run pytest -q tests/test_agents_invoke.py -x --tb=short 2>&1 | head -200