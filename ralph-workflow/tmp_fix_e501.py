"""Fix E501 long line in test_agents_invoke.py."""

path = "ralph-workflow/tests/test_agents_invoke.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

old = '    monkeypatch.setattr(invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines)'
new = '    monkeypatch.setattr(\n        invoke_module, "run_subprocess_and_read_lines", fake_run_subprocess_and_read_lines\n    )'

count = content.count(old)
print(f"Found {count} occurrences")
new_content = content.replace(old, new)
print(f"Changed: {new_content != content}")
with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Written!")
