#!/usr/bin/env bash
# Packaging commands extracted from the Makefile.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

packaging_smoke() {
  local smoke_dir="$ROOT/../tmp/packaging-smoke"
  mkdir -p "$(dirname "$smoke_dir")"
  rm -rf "$smoke_dir"
  trap 'rm -rf "$smoke_dir"' RETURN
  python -m venv --without-pip "$smoke_dir"
  PYTHONPATH="" uv pip install --quiet --python "$smoke_dir/bin/python" "$ROOT"
}

dist_homebrew() {
  mkdir -p "$ROOT/Formula"
  echo "Building Homebrew bottle..."
  python -m installer --print-blob --no-scrollbar "$ROOT"/ralph_python*.whl 2>/dev/null || \
    echo "Note: Homebrew bottle creation requires macOS and Homebrew environment"
}

formula_check() {
  echo "Validating Homebrew formula..."
  if command -v ruby >/dev/null 2>&1; then
    ruby -c "$ROOT/Formula/ralph-workflow.rb"
  else
    echo "Ruby not installed, skipping formula check (install Ruby to enable)"
  fi
}

case "${1:-}" in
  packaging-smoke) packaging_smoke ;;
  dist-homebrew) dist_homebrew ;;
  formula-check) formula_check ;;
  *)
    echo "usage: $0 {packaging-smoke|dist-homebrew|formula-check}" >&2
    exit 2
    ;;
esac
