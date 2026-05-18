"""Script to fix standard MockSession/MockWorkspaceRoot nested class violations."""
from pathlib import Path
import re

# Standard blocks to remove
STANDARD_SESSION_VARIANTS = [
    '\n\n    class MockSession:\n        session_id = "test-session"\n\n        def __init__(self, *args: object) -> None:\n            if not args:\n                self._caps: set[str] = set()\n            elif len(args) == 1 and isinstance(args[0], set):\n                self._caps = {s for s in args[0] if isinstance(s, str)}\n            else:\n                self._caps = {s for s in args if isinstance(s, str)}\n\n        def check_capability(self, capability: str) -> object:\n            return capability in self._caps\n',
    '\n\n    class MockSession:\n        session_id = "test-session"\n\n        def __init__(self, *args: object) -> None:\n            if not args:\n                self._caps: set[str] = set()\n            elif len(args) == 1 and isinstance(args[0], set):\n                self._caps = {s for s in args[0] if isinstance(s, str)}\n            else:\n                self._caps = {s for s in args if isinstance(s, str)}\n\n        def check_capability(self, capability: str) -> object:\n            return capability in self._caps',
]

STANDARD_WORKSPACE_VARIANTS = [
    '\n\n    class MockWorkspaceRoot:\n        def __init__(self, root: object) -> None:\n            self.root = root\n',
    '\n\n    class MockWorkspaceRoot:\n        def __init__(self, root: object) -> None:\n            self.root = root',
]

tests_dir = Path('ralph-workflow/tests')
changed = []

for path in sorted(tests_dir.rglob('*.py')):
    if '__pycache__' in str(path):
        continue
    content = path.read_text()
    original = content

    needs_session_import = False
    needs_workspace_import = False

    # Try to remove standard MockSession
    for variant in STANDARD_SESSION_VARIANTS:
        if variant in content:
            content = content.replace(variant, '', 1)
            needs_session_import = True
            break

    # Try to remove standard MockWorkspaceRoot
    for variant in STANDARD_WORKSPACE_VARIANTS:
        if variant in content:
            content = content.replace(variant, '', 1)
            needs_workspace_import = True
            break

    if not needs_session_import and not needs_workspace_import:
        continue

    # Remove module-level aliases
    content = re.sub(r'\nMockSession = \w+\.MockSession\n', '\n', content)
    content = re.sub(r'\nMockWorkspaceRoot = \w+\.MockWorkspaceRoot\n', '\n', content)

    # Add imports after 'from __future__ import annotations' line
    imports_to_add = []
    if needs_session_import:
        imports_to_add.append('from tests.mock_session import MockSession')
    if needs_workspace_import:
        imports_to_add.append('from tests.mock_workspace_root import MockWorkspaceRoot')

    # Find where to insert imports (after from __future__ import annotations)
    future_import_line = 'from __future__ import annotations\n'
    if future_import_line in content:
        insert_str = future_import_line + '\n'.join(imports_to_add) + '\n'
        content = content.replace(future_import_line, insert_str, 1)

    if content != original:
        path.write_text(content)
        changed.append(str(path.relative_to('ralph-workflow')))
        print(f'Updated: {path.relative_to("ralph-workflow")}')

print(f'\nTotal updated: {len(changed)}')
