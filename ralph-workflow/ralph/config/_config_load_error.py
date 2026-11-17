from __future__ import annotations

from pathlib import Path

__all__ = ["ConfigLoadError"]


class ConfigLoadError(ValueError):
    def __init__(self, file: Path | str, parse_error: str | None = None) -> None:
        resolved_file = Path(file) if parse_error is not None else Path("<unknown>")
        resolved_error = parse_error if parse_error is not None else str(file)
        self.file = resolved_file
        self.parse_error = resolved_error
        super().__init__(
            f"What failed: Ralph could not read {resolved_file}: {resolved_error}.\n"
            "Why it matters: settings in a malformed file are not safe to use.\n"
            f"Fix: correct the TOML syntax in {resolved_file}, then run `ralph --check-config`."
            if parse_error is not None
            else resolved_error
        )
