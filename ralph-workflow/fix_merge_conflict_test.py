#!/usr/bin/env python3
"""Fix test_merge_conflict_fails_phase in test_reducer.py"""

import re

filepath = "/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow/tests/test_reducer.py"

with open(filepath, "r") as f:
    content = f.read()

# Find the test_merge_conflict_fails_phase function and fix it
old_assertion = "assert effects == []"
new_assertion = """assert len(effects) == 1
    assert isinstance(effects[0], ExitFailureEffect)
    assert effects[0].reason == "Merge conflict in workers: u1, u2"
    assert new_state.phase == PHASE_FAILED"""

# Find the test function and replace the assertion
# The pattern matches from def test_merge_conflict_fails_phase to the next def or end of file
pattern = r'(def test_merge_conflict_fails_phase.*?)(assert effects == \[\])'
replacement = r'\1' + new_assertion.replace('\n', '\n    ')

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

if new_content == content:
    print("WARNING: Pattern not found, trying alternative approach")
    # Alternative: just replace the specific line
    if old_assertion in content:
        new_content = content.replace(old_assertion, new_assertion.replace('\n    ', '\n'))
        print("Applied alternative replacement")
    else:
        print("ERROR: Could not find the assertion to replace")
        exit(1)

with open(filepath, "w") as f:
    f.write(new_content)

print("Fixed test_merge_conflict_fails_phase in test_reducer.py")
