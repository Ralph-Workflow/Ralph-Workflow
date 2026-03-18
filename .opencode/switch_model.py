#!/usr/bin/env python3
"""
Script to switch models in opencode.json between GLM and MiniMax.

Usage:
    python switch_model.py glm          # Switch to GLM
    python switch_model.py minimax      # Switch to MiniMax
    python switch_model.py              # Toggle between models
"""

import json
import sys
from pathlib import Path

# Model configurations
MODELS = {
    'glm': 'zai-coding-plan/glm-5',
    'minimax': 'minimax/MiniMax-M2.5-highspeed'
}

# Agents that should use subagent models (not the primary agent)
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

def detect_current_model(config):
    """Detect which model is currently in use."""
    # Check one of the subagents
    sample_agent = config['agent'].get('workflow-gui', {})
    current = sample_agent.get('model', '')
    
    if MODELS['glm'] in current:
        return 'glm'
    elif MODELS['minimax'] in current:
        return 'minimax'
    return None

def switch_to_model(config, target_model):
    """Switch all subagents to the target model."""
    if target_model not in MODELS:
        raise ValueError(f"Unknown model: {target_model}. Choose 'glm' or 'minimax'.")
    
    model_string = MODELS[target_model]
    count = 0
    
    for agent_name in SUBAGENTS:
        if agent_name in config['agent']:
            config['agent'][agent_name]['model'] = model_string
            count += 1
    
    return count

def main():
    config_path = get_config_path()
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)
    
    # Load configuration
    config = load_config(config_path)
    
    # Determine target model
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        if target not in MODELS:
            print(f"Error: Unknown model '{target}'. Use 'glm' or 'minimax'.")
            sys.exit(1)
    else:
        # Toggle mode: detect current and switch to the other
        current = detect_current_model(config)
        if current == 'glm':
            target = 'minimax'
        elif current == 'minimax':
            target = 'glm'
        else:
            print("Could not detect current model. Defaulting to minimax.")
            target = 'minimax'
    
    # Make the switch
    print(f"Switching to {target.upper()} ({MODELS[target]})...")
    count = switch_to_model(config, target)
    
    # Save configuration
    save_config(config_path, config)
    
    print(f"✓ Successfully updated {count} agents to use {MODELS[target]}")

if __name__ == '__main__':
    main()
