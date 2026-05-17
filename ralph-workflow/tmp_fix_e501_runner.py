"""Fix E501 long lines in test_pipeline_runner.py."""

path = "ralph-workflow/tests/test_pipeline_runner.py"
with open(path, encoding="utf-8") as f:
    content = f.read()

fixes = [
    (
        '    monkeypatch.setattr(effect_executor_module, "materialize_system_prompt", fake_materialize_system_prompt)',
        '    monkeypatch.setattr(\n        effect_executor_module, "materialize_system_prompt", fake_materialize_system_prompt\n    )',
    ),
    (
        '        monkeypatch.setattr(phase_agent_handler_module, "ChainManager", MagicMock(return_value=MagicMock()))',
        '        monkeypatch.setattr(\n            phase_agent_handler_module, "ChainManager", MagicMock(return_value=MagicMock())\n        )',
    ),
    (
        '        monkeypatch.setattr(phase_agent_handler_module, "handle_phase", lambda _effect, _ctx: [event])',
        '        monkeypatch.setattr(\n            phase_agent_handler_module, "handle_phase", lambda _effect, _ctx: [event]\n        )',
    ),
    (
        '    monkeypatch.setattr(effect_executor_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge())',
        '    monkeypatch.setattr(\n        effect_executor_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge()\n    )',
    ),
]

for old, new in fixes:
    count = content.count(old)
    print(f"Found {count} occurrences of: {old[:60]}...")
    content = content.replace(old, new)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("Written!")
