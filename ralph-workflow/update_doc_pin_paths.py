#!/usr/bin/env python3
"""Update watchdog-spec.md to reference the consolidated file."""
import re
from pathlib import Path

path = Path('docs/agents/watchdog-spec.md')
src = path.read_text()

# Replace each 'tests/agents/idle_watchdog/X.py' reference with
# 'tests/agents/test_idle_watchdog.py' (the consolidated file).
# This is safe because all those test files were consolidated into
# the single new file.
new_src = re.sub(
    r'tests/agents/idle_watchdog/[a-zA-Z0-9_]+\.py',
    'tests/agents/test_idle_watchdog.py',
    src,
)

if new_src != src:
    path.write_text(new_src)
    print(f'OK; updated {src.count("tests/agents/idle_watchdog/")} doc references')
else:
    print('No changes needed')
