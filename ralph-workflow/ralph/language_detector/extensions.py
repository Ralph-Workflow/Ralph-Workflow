"""File extension to language mapping for project detection."""

from __future__ import annotations

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    "rs": "Rust",
    "py": "Python",
    "pyw": "Python",
    "pyi": "Python",
    "js": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "jsx": "JavaScript",
    "ts": "TypeScript",
    "mts": "TypeScript",
    "cts": "TypeScript",
    "tsx": "TypeScript",
    "go": "Go",
    "java": "Java",
    "rb": "Ruby",
    "erb": "Ruby",
    "php": "PHP",
    "yml": "YAML",
    "yaml": "YAML",
    "json": "JSON",
    "html": "HTML",
    "htm": "HTML",
    "css": "CSS",
    "scss": "SCSS",
    "sass": "Sass",
    "less": "Less",
}

NON_PRIMARY_LANGUAGES: set[str] = {
    "YAML",
    "JSON",
    "HTML",
    "CSS",
    "SCSS",
    "Sass",
    "Less",
}


def extension_to_language(extension: str) -> str | None:
    """Map a file extension to a language name."""
    return EXTENSION_TO_LANGUAGE.get(extension.lower())


def is_non_primary_language(language: str) -> bool:
    """Return whether a language should not be preferred as primary."""
    return language in NON_PRIMARY_LANGUAGES
