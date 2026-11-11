"""Default gitignore patterns for project bootstrap."""

from __future__ import annotations

_DEFAULT_GITIGNORE_PATTERNS: tuple[str, ...] = (
    # Ralph Workflow local artifacts (existing — DO NOT REORDER).
    # The first entry ``.agent/`` already covers the
    # ``.agent/ralph-explore/`` disposable cache by virtue of its
    # trailing slash. The explicit child rule below is added to
    # satisfy the prompt's "managed gitignore child entry"
    # requirement (AC-05) without reordering the protected entries
    # above: ``auto_seed_default_gitignore`` only appends missing
    # patterns, so the parent ``.agent/`` rule is preserved and
    # the explicit child rule is added next to the parent.
    ".agent/",
    ".agent/ralph-explore/",
    "/PROMPT*",
    "wt-*/",
    "/checkpoint.json",
    # Python
    "__pycache__/",
    "*.py[codz]",
    "*$py.class",
    ".venv/",
    "venv/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    "dist/",
    "build/*",
    "!build/.gitkeep",
    "*.egg-info/",
    # Python extras
    ".tox/",
    ".nox/",
    ".pdm-build/",
    "*.pyo",
    ".ipynb_checkpoints/",
    "pip-wheel-metadata/",
    # Node
    "node_modules/",
    ".next/",
    ".nuxt/",
    # Node extras
    "coverage/",
    "*.tsbuildinfo",
    ".parcel-cache/",
    ".cache/",
    ".env",
    ".env.local",
    ".env.*.local",
    # Rust target patterns; Cargo lockfile intentionally checked in by applications
    "target/*",
    "!target/.gitkeep",
    "**/*.rs.bk",
    # Go packages; vendor directory is opt-in, checksum file intentionally checked in
    "vendor/*",
    # Ruby (note: vendor/bundle/.gitkeep cannot be allowlisted here because
    # the vendor/* pattern above also matches it. A user who tracks
    # vendor/bundle/.gitkeep can add a one-line !vendor/bundle/.gitkeep
    # to their project-local gitignore — the appended default cannot honor
    # both the Go "ignore all of vendor/" and the Ruby "track this
    # marker in vendor/bundle/" semantics simultaneously).
    "vendor/bundle/*",
    ".bundle/",
    "log/",
    "tmp/",
    "*.gem",
    # PHP
    ".phpunit.cache/",
    "/storage/*.key",
    "composer.phar",
    # Java/Kotlin extras (paired with existing .idea/ below)
    ".gradle/*",
    "!.gradle/.gitkeep",
    "*.class",
    "*.jar",
    # .NET / Visual Studio
    "bin/*",
    "!bin/.gitkeep",
    "obj/*",
    "!obj/.gitkeep",
    "*.user",
    "*.suo",
    "*.userosscache",
    "*.sln.docstates",
    "[Dd]ebug/",
    "[Rr]elease/",
    "x64/",
    "x86/",
    "*.dll",
    "*.exe",
    "*.pdb",
    # Dart / Flutter
    ".dart_tool/",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    ".packages",
    ".pub-cache/",
    ".pub/",
    # Elixir
    "_build/*",
    "!_build/.gitkeep",
    "deps/*",
    "!deps/.gitkeep",
    ".elixir_ls/",
    "cover/",
    "doc/",
    "fetch.*.exs",
    # Scala / Metals / BSP
    ".bsp/",
    ".metals/",
    "project/target/",
    "project/project/",
    # Terraform (note: *.tfvars is in the positive list; *.tfvars.example
    # is intentionally NOT allowlisted — the user must explicitly add
    # tracked example files).
    ".terraform/",
    "*.tfstate",
    "*.tfstate.*",
    ".terraform.lock.hcl",
    "terraform.tfvars",
    "crash.log",
    "crash.*.log",
    "*.tfvars",
    # Editors / IDEs extra
    ".fleet/",
    ".cursor/",
    ".windsurf/",
    ".idea_modules/",
    "*.iml",
    # Editors / IDEs (NOTE: .vscode/ intentionally NOT included —
    # the upstream repository has tracked files under .vscode/ that
    # a blanket ignore would hide.)
    ".idea/",
    "*.swp",
    "*.swo",
    # OS metadata
    ".DS_Store",
    "Thumbs.db",
    # OS metadata extras
    "Desktop.ini",
    "ehthumbs.db",
    "$RECYCLE.BIN/",
)

__all__ = ["_DEFAULT_GITIGNORE_PATTERNS"]
