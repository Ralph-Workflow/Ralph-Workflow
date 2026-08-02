"""Fix word-boundary regex in consolidate_any.py.

The previous regex used \\b which treats '_' as a word char, allowing
matches at the end of longer identifiers (e.g. ``_height_aware_display``
matches ``_display`` because ``\\b`` matches at the transition from
``y`` to non-word). The fix: replace all `(?<!\\.){}\\b` with
`(?<!\\w)(?<!\\.){}\\b` so the regex requires NO word char AND no
dot immediately before the match. This prevents substring renames.
"""
import re
from pathlib import Path

path = Path('.agent/tmp/consolidate_any.py')
src = path.read_text()

# Replace r"(?<!\.){}\b" with r"(?<!\w)(?<!\.){}\b"
old = r'(?<!\\.){}\\b'
new = r'(?<!\\w)(?<!\\.){}\\b'
src2, count = re.subn(old, new, src)
print(f'Replaced {count} patterns')

path.write_text(src2)
import ast
ast.parse(src2)
print('Parses OK')
