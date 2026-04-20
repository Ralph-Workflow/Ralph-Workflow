#!/usr/bin/env python3
"""Fix the failing test in test_reducer.py"""

# Read the file
with open('tests/test_reducer.py', 'r') as f:
    content = f.read()

# The test expects 'effects == []' but should expect the ExitFailureEffect
# Find the specific test and fix it
old_test = """    assert effects == []

def test_merge_conflict_"""
new_test = """    assert len(effects) == 1
    assert isinstance(effects[0], ExitFailureEffect)
    assert effects[0].reason == "Merge conflict in workers: u1, u2"
    assert new_state.phase == PHASE_FAILED

def test_merge_conflict_"""

if old_test in content:
    content = content.replace(old_test, new_test)
    with open('tests/test_reducer.py', 'w') as f:
        f.write(content)
    print("Fixed test_merge_conflict_fails_phase")
else:
    print("Pattern not found - test may already be fixed or structure is different")
    # Try to find the test and see what's there
    if 'def test_merge_conflict_fails_phase' in content:
        print("Found test_merge_conflict_fails_phase")
        # Extract a portion around it
        idx = content.find('def test_merge_conflict_fails_phase')
        print("Context:")
        print(content[idx:idx+500])
