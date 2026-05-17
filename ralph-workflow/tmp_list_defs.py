import ast

with open("ralph-workflow/ralph/pipeline/runner.py") as f:
    tree = ast.parse(f.read())

for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        print(f"Line {node.lineno}: def/class {node.name}")
    elif isinstance(node, ast.Assign) and hasattr(node, "lineno"):
        # Only top-level assignments
        for t in node.targets:
            if isinstance(t, ast.Name):
                print(f"Line {node.lineno}: assign {t.id}")
