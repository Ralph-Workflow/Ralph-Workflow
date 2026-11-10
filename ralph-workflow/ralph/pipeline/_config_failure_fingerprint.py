"""Compatibility re-exports for the configuration-failure fingerprint helpers.

The implementation moved to two public modules so callers (and tests) can
import the fingerprint types without reaching into a private module path:

- :mod:`ralph.pipeline.config_file_fingerprint` -- the pure fingerprint
  computation and the retry gate;
- :mod:`ralph.pipeline.config_file_fingerprint_watcher` -- the stateful
  watcher over those fingerprints.

This module keeps the historical import path working for the callers that
have not moved to the public modules yet.
"""

from __future__ import annotations

from ralph.pipeline.config_file_fingerprint import (
    ConfigFileFingerprint,
    compute_config_file_fingerprint,
    extract_config_path_from_reason,
    get_config_file_fingerprint,
    should_retry_config_failure,
)
from ralph.pipeline.config_file_fingerprint_watcher import ConfigFileFingerprintWatcher

__all__ = [
    "ConfigFileFingerprint",
    "ConfigFileFingerprintWatcher",
    "compute_config_file_fingerprint",
    "extract_config_path_from_reason",
    "get_config_file_fingerprint",
    "should_retry_config_failure",
]
