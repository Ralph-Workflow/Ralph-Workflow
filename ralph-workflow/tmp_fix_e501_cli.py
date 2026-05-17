"""Fix E501 long lines in test_cli.py."""

path = "ralph-workflow/tests/test_cli.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

old = '        None, RunPipelineOpts(cli_overrides={}, dry_run=False, resume=False, no_resume=False), display_context=ctx'
new = '        None,\n        RunPipelineOpts(cli_overrides={}, dry_run=False, resume=False, no_resume=False),\n        display_context=ctx,'

count = content.count(old)
print(f"Found {count} occurrences")
new_content = content.replace(old, new)
print(f"Changed: {new_content != content}")
with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Written!")
