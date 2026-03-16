# Ralph Makefile
# Build and installation for the Ralph multi-agent orchestrator

# Configuration
BINARY_NAME := ralph
INSTALL_ROOT ?= /usr/local
INSTALL_BIN := $(INSTALL_ROOT)/bin

# Rust build configuration
CARGO := cargo
CARGO_FLAGS :=
RELEASE_FLAGS := --release

# Detect platform
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
    PLATFORM := macos
else ifeq ($(UNAME_S),Linux)
    PLATFORM := linux
else
    PLATFORM := unknown
endif

.PHONY: all build release test clean install install-local install-with-gui install-with-gui-local uninstall check fmt lint dylint dylint-verbose help build-gui install-gui install-gui-local verify verify-gui ci

# Default target
all: build

# Build debug version
build:
	$(CARGO) build $(CARGO_FLAGS)
	echo "Debug build complete: target/debug/$(BINARY_NAME)"

# Build release version (optimized)
release:
	$(CARGO) build $(RELEASE_FLAGS)
	echo "Release build complete: target/release/$(BINARY_NAME)"

# Run all tests (bypasses the full verify contract; use `make verify` for that)
test:
	$(CARGO) test $(CARGO_FLAGS)
	echo "All tests passed"

# Run tests with output
test-verbose:
	$(CARGO) test $(CARGO_FLAGS) -- --nocapture

# Clean build artifacts
clean:
	$(CARGO) clean
	echo "Build artifacts cleaned"

# Install the binary (requires sudo for system directories)
# NOTE: This installs only the CLI binary. For GUI support, use install-with-gui.
install: release
	echo "Installing $(BINARY_NAME) to $(INSTALL_BIN)..."
	mkdir -p $(INSTALL_BIN)
	install -m 755 target/release/$(BINARY_NAME) $(INSTALL_BIN)/$(BINARY_NAME)
	echo "Installed: $(INSTALL_BIN)/$(BINARY_NAME)"
	echo ""
	echo "Installation complete! Run 'ralph --help' to get started."
	echo "To also install the GUI, run: make install-with-gui"

# Install to user's local bin (no sudo needed) - CLI only
install-local:
	$(MAKE) install INSTALL_ROOT=$(HOME)/.local

# Build the GUI and install both CLI and GUI binaries.
# Requires: cargo tauri and bun (for Angular frontend build).
install-with-gui: build-gui
	echo "Installing $(BINARY_NAME) to $(INSTALL_BIN)..."
	mkdir -p $(INSTALL_BIN)
	install -m 755 target/release/$(BINARY_NAME) $(INSTALL_BIN)/$(BINARY_NAME)
	echo "Installed: $(INSTALL_BIN)/$(BINARY_NAME)"
	$(MAKE) install-gui
	echo ""
	echo "Full installation complete! Run 'ralph --help' and 'ralph-gui' to get started."

# Full install (CLI + GUI) to user's local bin (no sudo needed)
install-with-gui-local:
	$(MAKE) install-with-gui INSTALL_ROOT=$(HOME)/.local

# Uninstall the binary
uninstall:
	echo "Removing $(INSTALL_BIN)/$(BINARY_NAME)..."
	rm -f $(INSTALL_BIN)/$(BINARY_NAME)
	echo "Uninstalled"

# Type checking and linting
check:
	$(CARGO) check $(CARGO_FLAGS)
	echo "Type check passed"

# Format code
fmt:
	$(CARGO) fmt
	echo "Code formatted"

# Check formatting without modifying
fmt-check:
	$(CARGO) fmt -- --check
	echo "Format check passed"

# Run clippy lints only (NOT the full required verification contract).
# For the canonical pre-PR gate, use `cargo xtask verify` or `make ci`.
# NOTE: When adding a new workspace member, add its clippy check here AND in docs/agents/verification.md
lint:
	$(CARGO) clippy -p ralph-workflow $(CARGO_FLAGS) --all-targets --all-features -- -D warnings
	$(CARGO) clippy -p ralph-workflow-tests $(CARGO_FLAGS) --all-targets -- -D warnings
	$(CARGO) clippy -p test-helpers $(CARGO_FLAGS) --all-targets -- -D warnings
	$(CARGO) clippy -p xtask $(CARGO_FLAGS) --all-targets -- -D warnings
	$(CARGO) clippy -p ralph-gui $(CARGO_FLAGS) --all-targets -- -D warnings
	echo "Lint check passed"

# Run custom dylint lints (safe default: lib only)
# Delegates to cargo xtask dylint which handles environment setup
dylint:
	$(CARGO) xtask dylint

# Run custom dylint lints with verbose output
dylint-verbose:
	$(CARGO) xtask dylint --verbose

# Run the canonical verification contract (core only - no GUI checks).
# Use this for backend/workflow work.
verify:
	$(CARGO) xtask verify
	echo "Core verification passed"

# Run full verification including GUI cargo, Angular frontend, and release build.
# Use this for GUI/frontend work or when running full CI.
verify-gui:
	$(CARGO) xtask verify --gui
	echo "Full verification (with GUI) passed"

# Run the canonical verification contract (full - includes GUI).
ci:
	$(MAKE) verify-gui
	echo "All CI checks passed"

# GUI targets
# Build the Angular frontend and Tauri GUI binary using Tauri's bundler.
# This produces a standalone distributable app.
# Note: Tauri runs `bun run build` (ng build --configuration production) automatically,
# which produces minified production assets.
build-gui:
	cargo tauri build
	echo "GUI bundle complete. See src-tauri/target/release/bundle/ for distributable."

# Install GUI binary only (requires build-gui first)
install-gui:
	@echo "Installing ralph-gui to $(INSTALL_BIN)..."
	mkdir -p $(INSTALL_BIN)
	install -m 755 target/release/ralph-gui $(INSTALL_BIN)/ralph-gui
	echo "Installed: $(INSTALL_BIN)/ralph-gui"
	echo ""
	echo "GUI installation complete! Run 'ralph-gui' to launch the GUI."

# Install GUI to user's local bin (no sudo needed)
install-gui-local:
	$(MAKE) install-gui INSTALL_ROOT=$(HOME)/.local

# Build documentation
doc:
	$(CARGO) doc --no-deps --open

# Print version info
version:
	echo "Ralph build configuration:"
	echo "  Binary: $(BINARY_NAME)"
	echo "  Platform: $(PLATFORM)"
	echo "  Install path: $(INSTALL_BIN)/$(BINARY_NAME)"
	$(CARGO) --version
	rustc --version

# Help
help:
	echo "Ralph Makefile targets:"
	echo ""
	echo "  build         Build debug version"
	echo "  release       Build optimized release version"
	echo "  test          Run all tests"
	echo "  test-verbose  Run tests with output"
	echo "  clean         Remove build artifacts"
	echo "  install       Install CLI only to $(INSTALL_BIN) (may need sudo)"
	echo "  install-local Install CLI only to ~/.local/bin (no sudo needed)"
	echo "  install-with-gui Install CLI + GUI to $(INSTALL_BIN) (requires cargo tauri)"
	echo "  install-with-gui-local Install CLI + GUI to ~/.local/bin (no sudo needed)"
	echo "  uninstall     Remove installed binary"
	echo "  check         Run type checks"
	echo "  fmt           Format source code"
	echo "  lint          Run clippy lints"
	echo "  dylint        Run custom dylint lints (lib only)"
	echo "  dylint-verbose Run custom dylint lints with debug output"
	echo "  verify        Run core CLI+library verification (no GUI checks)"
	echo "  verify-gui    Run full verification including GUI (for GUI work)"
	echo "  ci            Run all CI checks (includes GUI)"
	echo "  doc           Build and open documentation"
	echo "  version       Print version information"
	echo "  help          Show this help"
	echo ""
	echo "GUI targets:"
	echo "  build-gui       Build the Tauri GUI (produces standalone bundle)"
	echo "  install-gui     Install GUI binary only (requires build-gui first)"
	echo "  install-gui-local Install GUI to ~/.local/bin (no sudo needed)"
	echo ""
	echo "Environment variables:"
	echo "  INSTALL_ROOT  Installation prefix (default: /usr/local)"
	echo ""
	echo "Examples:"
	echo "  make release && sudo make install"
	echo "  make install-local"
	echo "  make install-with-gui"
	echo "  make install-with-gui-local"
	echo "  INSTALL_ROOT=/opt make install"
