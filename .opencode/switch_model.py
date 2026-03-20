#!/usr/bin/env python3
"""
Script to switch models in opencode.json.

Usage:
    python switch_model.py <model_string>  # Switch to specific model
    python switch_model.py                 # Toggle between defined models
"""

import json
import sys
from pathlib import Path

MODELS = {
    'glm': 'zai-coding-plan/glm-5',
    'minimax': 'minimax/MiniMax-M2.7-highspeed',
    'gpt5': 'openai/gpt-5.1-codex-mini'
}

# Subagents to exclude from model switching
EXCLUDED_SUBAGENTS = ['build']

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

def get_current_model_string(config):
    """Get the current model string from config."""
    for agent_name in config['agent']:
        if agent_name not in EXCLUDED_SUBAGENTS:
            return config['agent'][agent_name].get('model', '')
    return ''

def detect_known_model(model_string):
    """Detect if model string matches a known model alias."""
    for alias, model in MODELS.items():
        if model in model_string or model_string.endswith(alias):
            return alias
    return None

def switch_to_model(config, model_string):
    """Switch all subagents to the target model (except excluded ones)."""
    count = 0
    for agent_name in config['agent']:
        if agent_name not in EXCLUDED_SUBAGENTS:
            config['agent'][agent_name]['model'] = model_string
            count += 1
    return count

def main():
    config_path = get_config_path()
    
    if not config_path.exists():
        print(f"Error: Configuration file not found at {config_path}")
        sys.exit(1)
    
    config = load_config(config_path)
    current_model = get_current_model_string(config)
    
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in MODELS:
            target_model = MODELS[arg]
        else:
            target_model = arg
    else:
        current_alias = detect_known_model(current_model)
        if current_alias == 'glm':
            target_model = MODELS['minimax']
        else:
            target_model = MODELS['glm']
    
    print(f"Switching to {target_model}...")
    count = switch_to_model(config, target_model)
    save_config(config_path, config)
    
    print(f"Successfully updated {count} agents to use {target_model}")

if __name__ == '__main__':
    main()
