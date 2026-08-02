"""Patch consolidate_any.py to emit constants AFTER helpers/fixtures."""
from pathlib import Path

path = Path('.agent/tmp/consolidate_any.py')
src = path.read_text()

old1 = '    constants_block = "\\n".join(constants_block_parts) + "\\n\\n" if constants_block_parts else ""\n    body_parts = [header, imports_block, constants_block]'
new1 = '    # Defer constants until after helpers/fixtures so any helper\n    # referenced from a top-level constant resolves at module load time.\n    constants_block = "\\n".join(constants_block_parts) + "\\n\\n" if constants_block_parts else ""\n    body_parts = [header, imports_block]'

old2 = '    # Tag each emitted block with its source filename so the post-emit\n    # class rename pass can scope renames to a single file\'s own blocks.\n    file_blocks = {}  # filename -> list of body_part indices'
new2 = '    # Insert the constants block here (after helpers+fixtures, before\n    # classes and test_fns) so any helper referenced from a top-level\n    # constant resolves at module load time.\n    if constants_block:\n        body_parts.append(constants_block)\n    # Tag each emitted block with its source filename so the post-emit\n    # class rename pass can scope renames to a single file\'s own blocks.\n    file_blocks = {}  # filename -> list of body_part indices'

if old1 not in src:
    print('OLD1 NOT FOUND')
elif old2 not in src:
    print('OLD2 NOT FOUND')
else:
    src = src.replace(old1, new1)
    src = src.replace(old2, new2)
    path.write_text(src)
    print('OK')

import ast
ast.parse(open(path).read())
print('Parses OK')
