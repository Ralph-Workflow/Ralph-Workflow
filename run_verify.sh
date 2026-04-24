#!/bin/bash
cd /Users/mistlight/Projects/RalphWithReviewer/wt-81-display/ralph-workflow
uv run pytest tests/test_display_thinking_preview_reproduction.py tests/test_display_lifecycle_regression.py tests/test_plain_renderer_activity.py -x -q
