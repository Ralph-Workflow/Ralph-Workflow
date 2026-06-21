#!/usr/bin/env python3
"""verify_social_proof.py — Thin wrapper around fabrication_guard.py.

This file exists for backward compatibility. All logic lives in
scripts/fabrication_guard.py, which provides multi-level defense.

DEPRECATED: Use `scripts/fabrication_guard.py` directly for new integrations.
"""
import sys
from pathlib import Path

# Import and delegate to the real guard
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fabrication_guard import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
