#!/usr/bin/env python3
"""Update RALPH_PIN_TEST_PATHS to point to consolidated file."""
import re
from pathlib import Path

path = Path('tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py')
src = path.read_text()

m = re.search(
    r'(RALPH_PIN_TEST_PATHS:\s*tuple\[str,\s*\.\.\.\]\s*=\s*\()(.*?)(\)\s*\n)',
    src,
    re.DOTALL,
)
assert m, 'Could not find RALPH_PIN_TEST_PATHS tuple'
prefix, inner, suffix = m.group(1), m.group(2), m.group(3)

def replace_path(m):
    path_str = m.group(1)
    if path_str.startswith('tests/agents/idle_watchdog/'):
        return '"tests/agents/test_idle_watchdog.py"'
    return m.group(0)

new_inner = re.sub(r'"([^"]+\.py)"', replace_path, inner)

new_src = src.replace(prefix + inner + suffix, prefix + new_inner + suffix, 1)
path.write_text(new_src)

import ast
ast.parse(new_src)
print(f'OK; updated {inner.count("tests/agents/idle_watchdog/")} idle_watchdog pin paths to consolidated file')
