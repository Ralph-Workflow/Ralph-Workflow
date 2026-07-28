"""Bounded, failure-safe lexer inference for file-content previews."""

from __future__ import annotations

from functools import lru_cache
from pathlib import PurePath
from typing import Final

from pygments.lexers import get_lexer_for_filename, guess_lexer

_SUFFIXES: Final[dict[str, str]] = {".d.ts": "typescript", ".tsx": "typescript", ".ts": "typescript", ".py": "python", ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".swift": "swift", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".pl": "perl", ".lua": "lua", ".r": "r", ".scala": "scala", ".clj": "clojure", ".ex": "elixir", ".erl": "erlang", ".hs": "haskell", ".ml": "ocaml", ".zig": "zig", ".dart": "dart", ".vue": "vue", ".svelte": "svelte", ".astro": "astro", ".scss": "scss", ".sass": "sass", ".less": "less", ".sql": "sql", ".graphql": "graphql", ".proto": "protobuf", ".tf": "hcl", ".nix": "nix", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".csv": "text", ".tsv": "text", ".rst": "rst", ".tex": "latex", ".j2": "jinja", ".diff": "diff", ".patch": "diff", ".ps1": "powershell", ".bat": "bat", ".fish": "fish", ".zsh": "bash", ".s": "asm", ".sol": "solidity", ".jl": "julia", ".fs": "fsharp", ".groovy": "groovy", ".vim": "vim", ".md": "markdown", ".html": "html", ".xml": "xml", ".css": "css", ".js": "javascript", ".sh": "bash"}
_NAMES: Final[dict[str, str]] = {"dockerfile": "docker", "containerfile": "docker", "makefile": "make", "justfile": "make", "cmakelists.txt": "cmake", ".gitignore": "text", ".editorconfig": "ini", ".bashrc": "bash", ".zshrc": "bash", "go.mod": "go", "go.sum": "text", "license": "text", "codeowners": "text"}


def _sniff(prefix: str) -> str:
    first = prefix.splitlines()[0] if prefix.startswith("#!") and prefix.splitlines() else ""
    stripped = prefix.lstrip()
    choices = (("python", "python" in first), ("bash", bool(first)), ("json", stripped.startswith(("{", "["))), ("yaml", stripped.startswith("---")), ("diff", stripped.startswith(("diff ", "@@ "))), ("xml", stripped.startswith("<?xml")), ("html", stripped.lower().startswith("<!doctype html")))
    return next((lexer for lexer, matched in choices if matched), "")


@lru_cache(maxsize=512)
def _infer(filename: str, prefix: str) -> str:
    lowered = filename.lower()
    lexer = "text" if lowered.endswith((".tar.gz", ".zip", ".png", ".jpg", ".pdf")) else _NAMES.get(lowered, "")
    if not lexer and (lowered.startswith(".env") or PurePath(lowered).name.startswith("requirements")):
        lexer = "bash" if lowered.startswith(".env") else "text"
    if not lexer:
        lexer = next((value for suffix, value in _SUFFIXES.items() if lowered.endswith(suffix)), "")
    if not lexer:
        lexer = _sniff(prefix)
    if not lexer:
        try:
            aliases = getattr(get_lexer_for_filename(filename), "aliases", ())
            lexer = str(aliases[0]) if aliases else ""
        except Exception:
            pass
    if not lexer:
        try:
            aliases = getattr(guess_lexer(prefix), "aliases", ())
            lexer = str(aliases[0]) if aliases else ""
        except Exception:
            pass
    return lexer or "text"


def lexer_for_path(path: str | None, content: str = "") -> str:
    """Return a lexer alias for arbitrary path/content without raising."""
    return _infer(path or "", content[:4096])


__all__ = ["lexer_for_path"]
