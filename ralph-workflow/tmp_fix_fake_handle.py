"""Script to fix _FakeHandle nested class violations in opencode session execution tests."""
from pathlib import Path
import re

FULLER_FAKE_HANDLE = """    class _FakeHandle:
        returncode: int = 0
        stdout = None
        stderr = None

        def __init__(self, *, returncode: int = 0, has_descendants: bool = False) -> None:
            self.returncode = returncode
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants

        def descendant_snapshot(self) -> tuple[int, float | None]:
            return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)

        def poll(self) -> int | None:
            return self.returncode"""

SIMPLE_QUIET_PARENT_HANDLE = """    class _FakeHandle:
        \"\"\"Minimal fake ManagedProcess for strategy tests.\"\"\"

        def __init__(
            self,
            *,
            returncode: int = 0,
            has_descendants: bool = False,
        ) -> None:
            self.returncode = returncode
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants"""

SIMPLE_INTEGRATION_HANDLE = """    class _FakeHandle:
        returncode = 0

        def __init__(self, *, has_descendants: bool = False) -> None:
            self._has_descendants = has_descendants

        def has_live_descendants(self) -> bool:
            return self._has_descendants"""

tests_dir = Path('ralph-workflow/tests')
changed = []

for path in sorted(tests_dir.rglob('*.py')):
    if '__pycache__' in str(path):
        continue
    content = path.read_text()
    original = content

    # Check which variant is present
    if FULLER_FAKE_HANDLE in content or SIMPLE_QUIET_PARENT_HANDLE in content:
        # Remove the nested class
        content = content.replace(FULLER_FAKE_HANDLE, '', 1)
        content = content.replace(SIMPLE_QUIET_PARENT_HANDLE, '', 1)
        # Remove the module-level alias
        content = re.sub(r'\n_FakeHandle = \w+\._FakeHandle\n', '\n', content)
        # Add import
        future_import = 'from __future__ import annotations\n'
        if future_import in content:
            content = content.replace(future_import, future_import + 'from tests.fake_handle import _FakeHandle\n', 1)
    elif SIMPLE_INTEGRATION_HANDLE in content:
        content = content.replace(SIMPLE_INTEGRATION_HANDLE, '', 1)
        content = re.sub(r'\n_FakeHandle = \w+\._FakeHandle\n', '\n', content)
        future_import = 'from __future__ import annotations\n'
        if future_import in content:
            content = content.replace(future_import, future_import + 'from tests.integration.fake_handle import _FakeHandle\n', 1)
    else:
        continue

    if content != original:
        path.write_text(content)
        changed.append(str(path.relative_to('ralph-workflow')))
        print(f'Updated: {path.relative_to("ralph-workflow")}')

print(f'\nTotal updated: {len(changed)}')
