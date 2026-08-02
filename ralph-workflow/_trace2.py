"""Trace what the rename does for _height_aware_display."""
import re

helper_source = '''def _height_aware_display(*, height: int) -> tuple[ParallelDisplay, StringIO]:
    """Build a display whose Console carries the requested height."""
    pass
'''

# Simulate the conflicting helper rename for config_table
filename = 'test_parallel_display_emit_config_table.py'
short = filename.replace('.py', '').replace('test_', '')[:30]
new_name = '_{}_{}'.format(short, '_height_aware_display'.lstrip('_'))
print(f'Expected new name: {new_name!r}')

# Apply the rename
new_source = re.sub(r'^def ' + re.escape('_height_aware_display') + r'\b', 'def ' + new_name, helper_source, count=1, flags=re.MULTILINE)
print('After def rename:')
print(new_source)
