#!/usr/bin/env python3
"""
Script to ensure cargo variants exist for all subagents in opencode.json.

Usage:
    python ensure_cargo.py  # Ensures cargo variants exist for all subagents

This is idempotent - running multiple times produces the same result.
"""

import json
import sys
from pathlib import Path

SUBAGENTS = [
    'workflow-gui',
    'workflow-core',
    'workflow-reducer',
    'workflow-execution',
    'workflow-io',
    'workflow-workspace',
    'workflow-git',
    'workflow-config',
    'workflow-app',
    'workflow-logging',
    'workflow-monitoring',
    'workflow-misc',
    'workflow-future',
    'workflow-agents',
    'workflow-prompts',
    'workflow-json',
    'workflow-cloud',
    'test-helpers',
    'xtask',
    'workflow-tests',
    'workflow-lints',
    'workflow-docs'
]

CARGO_PERMISSIONS = {
    "rustup show active-toolchain": "allow",
    "export RUSTUP_TOOLCHAIN=*": "allow",
    "cargo build *": "allow",
    "cargo check *": "allow",
    "cargo test *": "allow"
}

CARGO_PERMISSION_KEYS = {
    "rustup show active-toolchain",
    "export RUSTUP_TOOLCHAIN=*",
    "cargo build *",
    "cargo check *",
    "cargo test *",
}

def get_config_path():
    script_dir = Path(__file__).parent
    return script_dir / 'opencode.json'

def load_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)

def save_config(config_path, config):
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')

def strip_cargo_permissions_from_normal_agents(config):
    """Remove cargo permissions from normal agents (those without -cargo suffix)."""
    stripped = 0
    
    for agent_name in config['agent']:
        if agent_name.endswith('-cargo'):
            continue
        
        if agent_name == 'build':
            continue
        
        agent = config['agent'][agent_name]
        if 'permission' not in agent or 'bash' not in agent['permission']:
            continue
        
        bash_perms = agent['permission']['bash']
        cargo_keys_in_bash = CARGO_PERMISSION_KEYS.intersection(bash_perms.keys())
        
        if cargo_keys_in_bash:
            for key in cargo_keys_in_bash:
                del bash_perms[key]
            stripped += 1
    
    return stripped

def ensure_cargo_agents(config):
    """Ensure cargo variants exist for all subagents with cargo permissions."""
    created = 0
    updated = 0
    
    for agent_name in SUBAGENTS:
        if agent_name not in config['agent']:
            continue
        
        cargo_agent_name = f"{agent_name}-cargo"
        original_agent = config['agent'][agent_name]
        
        if cargo_agent_name in config['agent']:
            cargo_agent = config['agent'][cargo_agent_name]
            cargo_agent['description'] = original_agent.get('description', '')
            cargo_agent['mode'] = original_agent.get('mode', 'subagent')
            cargo_agent['model'] = original_agent.get('model', '')
            cargo_agent['permission'] = {k: v.copy() if isinstance(v, dict) else v for k, v in original_agent.get('permission', {}).items()}
            if 'bash' in cargo_agent['permission']:
                cargo_agent['permission']['bash'].update(CARGO_PERMISSIONS)
            else:
                cargo_agent['permission']['bash'] = CARGO_PERMISSIONS.copy()
            updated += 1
        else:
            cargo_agent = {
                'description': original_agent.get('description', ''),
                'mode': original_agent.get('mode', 'subagent'),
                'model': original_agent.get('model', ''),
                'permission': {k: v.copy() if isinstance(v, dict) else v for k, v in original_agent.get('permission', {}).items()}
            }
            if 'bash' in cargo_agent['permission']:
                cargo_agent['permission']['bash'].update(CARGO_PERMISSIONS)
            else:
                cargo_agent['permission']['bash'] = CARGO_PERMISSIONS.copy()
            config['agent'][cargo_agent_name] = cargo_agent
            created += 1
    
    return created, updated

def main():
    config_path = get_config_path()
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)
    
    config = load_config(config_path)
    
    print("Stripping cargo permissions from normal agents...")
    stripped = strip_cargo_permissions_from_normal_agents(config)
    print(f"Stripped cargo permissions from {stripped} normal agents")
    
    print("Ensuring cargo variants exist for all subagents...")
    created, updated = ensure_cargo_agents(config)
    save_config(config_path, config)
    
    print(f"Created {created} new cargo agents, updated {updated} existing cargo agents")

if __name__ == '__main__':
    main()
