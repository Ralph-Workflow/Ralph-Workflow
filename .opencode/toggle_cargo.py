#!/usr/bin/env python3
"""
Script to toggle cargo permissions in opencode.json for all agents.

Usage:
    python toggle_cargo.py disable     # Remove all cargo permissions
    python toggle_cargo.py enable      # Restore all cargo permissions
    python toggle_cargo.py             # Toggle (detect current state)
"""

import json
import sys
from pathlib import Path

# Cargo commands that are typically allowed
CARGO_COMMANDS = [
    'cargo check *',
    'cargo build *',
    'cargo clippy *',
    'cargo test *',
]

# Agents that have bash permissions
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

def get_config_path():
    """Get the path to opencode.json."""
    script_dir = Path(__file__).parent
    return script_dir / 'opencode.json'

def load_config(config_path):
    """Load the opencode.json configuration."""
    with open(config_path, 'r') as f:
        return json.load(f)

def save_config(config_path, config):
    """Save the opencode.json configuration."""
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
        f.write('\n')  # Add newline at end of file

def detect_cargo_state(config):
    """Detect whether cargo commands are currently enabled."""
    # Check one of the subagents for cargo commands
    for agent_name in SUBAGENTS:
        if agent_name in config['agent']:
            agent = config['agent'][agent_name]
            if 'permission' in agent and 'bash' in agent['permission']:
                bash_perms = agent['permission']['bash']
                # Check if any cargo command is present and allowed
                for cmd in CARGO_COMMANDS:
                    if cmd in bash_perms and bash_perms[cmd] == 'allow':
                        return 'enabled'
    return 'disabled'

def disable_cargo(config):
    """Remove all cargo permissions from agents."""
    count = 0
    removed_commands = set()
    
    for agent_name in SUBAGENTS:
        if agent_name not in config['agent']:
            continue
            
        agent = config['agent'][agent_name]
        if 'permission' not in agent or 'bash' not in agent['permission']:
            continue
            
        bash_perms = agent['permission']['bash']
        
        # Remove all cargo commands
        for cmd in CARGO_COMMANDS:
            if cmd in bash_perms:
                del bash_perms[cmd]
                removed_commands.add(cmd)
                count += 1
    
    return count, removed_commands

def enable_cargo(config):
    """Add cargo permissions back to agents."""
    count = 0
    added_commands = set()
    
    for agent_name in SUBAGENTS:
        if agent_name not in config['agent']:
            continue
            
        agent = config['agent'][agent_name]
        if 'permission' not in agent or 'bash' not in agent['permission']:
            continue
            
        bash_perms = agent['permission']['bash']
        
        # Add cargo commands if not present
        for cmd in CARGO_COMMANDS:
            if cmd not in bash_perms:
                bash_perms[cmd] = 'allow'
                added_commands.add(cmd)
                count += 1
    
    return count, added_commands

def main():
    config_path = get_config_path()
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)
    
    # Load configuration
    config = load_config(config_path)
    
    # Determine target action
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()
        if action not in ['enable', 'disable']:
            print(f"Error: Unknown action '{action}'. Use 'enable' or 'disable'.")
            sys.exit(1)
    else:
        # Toggle mode: detect current state and switch
        current = detect_cargo_state(config)
        action = 'disable' if current == 'enabled' else 'enable'
        print(f"Current state: {current}")
    
    # Perform the action
    if action == 'disable':
        print("Disabling cargo permissions...")
        count, commands = disable_cargo(config)
        print(f"✓ Removed {count} cargo permission(s) across all agents")
        if commands:
            print(f"  Commands removed: {', '.join(sorted(commands))}")
    else:
        print("Enabling cargo permissions...")
        count, commands = enable_cargo(config)
        print(f"✓ Added {count} cargo permission(s) across all agents")
        if commands:
            print(f"  Commands added: {', '.join(sorted(commands))}")
    
    # Save configuration
    save_config(config_path, config)
    print(f"✓ Configuration saved to {config_path.name}")

if __name__ == '__main__':
    main()
