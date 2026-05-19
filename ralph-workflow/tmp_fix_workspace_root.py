"""Script to fix remaining MockWorkspaceRoot nested class violations."""
from pathlib import Path
import re

WORKSPACE_PATTERN1 = '\n    class MockWorkspaceRoot:\n        def __init__(self, root: object) -> None:\n            self.root = root\n'
WORKSPACE_PATTERN2 = '\n    class MockWorkspaceRoot:\n        def __init__(self, root: object) -> None:\n            self.root = root'

tests_dir = Path('ralph-workflow/tests')
changed = []

for path in sorted(tests_dir.rglob('*.py')):
    if '__pycache__' in str(path):
        continue
    content = path.read_text()
    original = content

    found = False
    for pattern in [WORKSPACE_PATTERN1, WORKSPACE_PATTERN2]:
        if pattern in content:
            content = content.replace(pattern, '\n', 1)
            found = True
            break

    if not found:
        continue

    # Remove module-level alias
    content = re.sub(r'\nMockWorkspaceRoot = \w+\.MockWorkspaceRoot\n', '\n', content)

    # Add import if not already present
    if 'from tests.mock_workspace_root import MockWorkspaceRoot' not in content:
        future_import = 'from __future__ import annotations\n'
        if future_import in content:
            content = content.replace(future_import, future_import + 'from tests.mock_workspace_root import MockWorkspaceRoot\n', 1)

    if content != original:
        path.write_text(content)
        changed.append(str(path.relative_to('ralph-workflow')))
        print(f'Updated: {path.relative_to("ralph-workflow")}')

print(f'\nTotal updated: {len(changed)}')
