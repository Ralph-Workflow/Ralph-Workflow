"""Fix old module name in test_pipeline_runner.py - handles string references too."""

import sys
from pathlib import Path

test_file = Path("tests/test_pipeline_runner.py")
content = test_file.read_text()

# Fix the old module name in all contexts (imports AND string references)
old = "ralph.mcp.upstream_validation"
new = "ralph.mcp.upstream.validation"

if old in content:
    content = content.replace(old, new)
    test_file.write_text(content)
    print(f"Fixed {test_file} - replaced {content.count(new)} occurrences")
else:
    print(f"No occurrences of '{old}' found in {test_file}")
