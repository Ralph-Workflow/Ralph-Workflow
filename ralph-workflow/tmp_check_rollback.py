from pathlib import Path
import ast

files = [
    'ralph-workflow/tests/test_mcp_tool_artifact_rollback_execute_ops_with_rollback.py',
    'ralph-workflow/tests/test_mcp_tool_artifact_rollback_history_integration_in_submit_ops.py',
    'ralph-workflow/tests/test_mcp_tool_artifact_rollback_invalid_content_rollback.py',
    'ralph-workflow/tests/test_mcp_tool_artifact_rollback_per_artifact_type_submission.py',
    'ralph-workflow/tests/test_mcp_tool_artifact_rollback_rollback_symmetry.py',
]
for fname in files:
    path = Path(fname)
    src = path.read_text()
    lines = src.splitlines()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.ClassDef):
                    body = '\n'.join(lines[child.lineno-1:child.end_lineno])
                    print(f'--- {path.name}: {node.name}.{child.name} ---')
                    print(body[:400])
                    print()
