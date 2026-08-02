"""Add negative lookbehind to all \b{Name}\b regex patterns in consolidate_any.py."""
import re
from pathlib import Path

path = Path('.agent/tmp/consolidate_any.py')
src = path.read_text()

# Find all r"\b{}\b".format(re.escape(...)) patterns and add negative lookbehind.
# Use raw string pattern matching.
# Pattern: r"\b{X}\b".format(re.escape(Y))
pattern = r'r"\\b\{\}\\b"\.format\(re\.escape\(([^)]+)\)\)'
# Replace with: r"(?<!\.){X}\b".format(re.escape(Y))
replacement = r'r"(?<!\\.){}\\b".format(re.escape(\1))'

new_src, count = re.subn(pattern, replacement, src)
print(f'Replaced {count} patterns')

path.write_text(new_src)
import ast
ast.parse(new_src)
print('Parses OK')
