"""Trace the helper rename behavior."""
import re

helper_source = '''def _height_aware_display(*, height: int) -> tuple[ParallelDisplay, StringIO]:
    """Build a display whose Console carries the requested height."""
    pass
'''

old_name = '_height_aware_display'
new_name = '_parallel_display_emit_completi_height_aware_display'

# Step 1: rename the def line
new_source = re.sub(r'^def ' + re.escape(old_name) + r'\b', 'def ' + new_name, helper_source, count=1, flags=re.MULTILINE)
print('After def rename:')
print(repr(new_source[:300]))
print()

# Step 2: rename other helpers in body
renames = {'_height_aware_display': '_parallel_display_emit_completi_height_aware_display'}
for other_name, other_new_name in renames.items():
    if other_name == '_height_aware_display':
        continue
    new_source = re.sub(r'(?<!\\.)' + re.escape(other_name) + r'\b', other_new_name, new_source)
print('After body rename:')
print(repr(new_source[:300]))
