#!/bin/bash
cd /Users/mistlight/Projects/RalphWithReviewer/wt-77-fix-counting-/ralph-workflow
uv run pytest tests/test_reducer.py -x -v 2>&1 | head -100