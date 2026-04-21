#!/usr/bin/env python3
"""Analyze runner.py structure."""
import ast
import sys

with open("ralph/pipeline/runner.py", "r") as f:
    source = f.read()

tree = ast.parse(source)

# Find all function definitions
functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]

# Find all imports
imports = []
for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom):
        if node.module and "ralph" in node.module:
            for alias in node.names:
                imports.append(f"from {node.module} import {alias.name}")

print("Functions:", functions[:30])
print("\nRalph imports:")
for imp in imports:
    print(f"  {imp}")
