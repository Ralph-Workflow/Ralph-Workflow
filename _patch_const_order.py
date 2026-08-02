"""Move constants_block to after fixtures, before test_fns."""
from pathlib import Path

path = Path('.agent/tmp/consolidate_any.py')
src = path.read_text()

old1 = '    for fixture_name, fixture_source in all_fixtures.items():\n        if fixture_name not in conflicting_fixtures:\n            body_parts.append("\\n\\n# === Fixture: {} ===\\n{}".format(fixture_name, fixture_source))\n    # Add module-level test functions (preserved as-is, no namespacing)\n    for filename, test_name, test_source in all_test_fns:'

new1 = '    for fixture_name, fixture_source in all_fixtures.items():\n        if fixture_name not in conflicting_fixtures:\n            body_parts.append("\\n\\n# === Fixture: {} ===\\n{}".format(fixture_name, fixture_source))\n    # Emit the constants block AFTER helpers and fixtures so any\n    # helper referenced from a top-level constant resolves at module\n    # load time. Constants themselves are not namespaced so they\n    # cannot refer to renamed (per-file) helpers in OTHER files.\n    if constants_block:\n        body_parts.append(constants_block)\n    # Add module-level test functions (preserved as-is, no namespacing)\n    for filename, test_name, test_source in all_test_fns:'

old2 = '    # Insert the constants block here (after helpers+fixtures, before\n    # classes and test_fns) so any helper referenced from a top-level\n    # constant resolves at module load time.\n    if constants_block:\n        body_parts.append(constants_block)\n    # Tag each emitted block with its source filename so the post-emit'

new2 = '    # Tag each emitted block with its source filename so the post-emit'

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
