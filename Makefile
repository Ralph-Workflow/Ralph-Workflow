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

.PHONY: all build release test clean install uninstall check fmt lint dylint dylint-verbose help build-gui install-gui install-gui-local

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

# Run all tests
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
install: release
	echo "Installing $(BINARY_NAME) to $(INSTALL_BIN)..."
	mkdir -p $(INSTALL_BIN)
	install -m 755 target/release/$(BINARY_NAME) $(INSTALL_BIN)/$(BINARY_NAME)
	echo "Installed: $(INSTALL_BIN)/$(BINARY_NAME)"
	$(MAKE) install-gui
	echo ""
	echo "Installation complete! Run 'ralph --help' to get started."
	echo "GUI: Run 'ralph-gui' to launch the desktop application."

# Install to user's local bin (no sudo needed)
install-local:
	$(MAKE) install INSTALL_ROOT=$(HOME)/.local

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
# Uses cargo xtask dylint wrapper which handles toolchain setup and wrapper scripts
dylint:
	$(CARGO) xtask dylint

# Run custom dylint lints with verbose debugging output
dylint-verbose:
	@bash -euo pipefail -c '\
		DYLINT_QUIET="$${DYLINT_QUIET:-0}"; \
		HOME_DIR="$${HOME:-}"; \
		CARGO_HOME_DIR="$${CARGO_HOME:-}"; \
		RUSTUP_HOME_DIR="$${RUSTUP_HOME:-}"; \
		DYLINT_DRIVER_DIR="$${DYLINT_DRIVER_PATH:-}"; \
		\
		if [ -z "$$CARGO_HOME_DIR" ]; then \
			if [ -n "$$HOME_DIR" ]; then \
				CARGO_HOME_DIR="$$HOME_DIR/.cargo"; \
			else \
				echo "error: HOME is not set and CARGO_HOME is not set." >&2; \
				echo "Set HOME, or set CARGO_HOME and RUSTUP_HOME to writable locations." >&2; \
				exit 1; \
			fi; \
		fi; \
		if [ -z "$$RUSTUP_HOME_DIR" ]; then \
			if [ -n "$$HOME_DIR" ]; then \
				RUSTUP_HOME_DIR="$$HOME_DIR/.rustup"; \
			else \
				echo "error: HOME is not set and RUSTUP_HOME is not set." >&2; \
				echo "Set HOME, or set RUSTUP_HOME to a writable location." >&2; \
				exit 1; \
			fi; \
		fi; \
		if [ -z "$$DYLINT_DRIVER_DIR" ]; then \
			if [ -n "$$HOME_DIR" ]; then \
				DYLINT_DRIVER_DIR="$$HOME_DIR/.dylint_drivers"; \
			else \
				echo "error: HOME is not set and DYLINT_DRIVER_PATH is not set." >&2; \
				echo "Set HOME, or set DYLINT_DRIVER_PATH to a writable location." >&2; \
				exit 1; \
			fi; \
		fi; \
		\
		export CARGO_HOME="$$CARGO_HOME_DIR"; \
		export RUSTUP_HOME="$$RUSTUP_HOME_DIR"; \
		export DYLINT_DRIVER_PATH="$$DYLINT_DRIVER_DIR"; \
		export PATH="$$CARGO_HOME/bin:$$PATH"; \
		CARGO_HOME_WRITABLE=1; \
		if ! mkdir -p "$$CARGO_HOME" 2>/dev/null; then \
			if [ ! -d "$$CARGO_HOME" ]; then \
				echo "error: cannot access cargo home: $$CARGO_HOME" >&2; \
				echo "Set CARGO_HOME to an existing readable location." >&2; \
				exit 1; \
			fi; \
			CARGO_HOME_WRITABLE=0; \
		fi; \
		if [ ! -r "$$CARGO_HOME" ]; then \
			echo "error: cargo home is not readable: $$CARGO_HOME" >&2; \
			echo "Set CARGO_HOME to an existing readable location." >&2; \
			exit 1; \
		fi; \
		if [ ! -w "$$CARGO_HOME" ]; then \
			CARGO_HOME_WRITABLE=0; \
		fi; \
		if [ "$$CARGO_HOME_WRITABLE" = "1" ]; then \
			mkdir -p "$$CARGO_HOME/registry" "$$CARGO_HOME/registry/src" "$$CARGO_HOME/bin"; \
		fi; \
		if [ -n "$$HOME_DIR" ] && [ "$$CARGO_HOME" != "$$HOME_DIR/.cargo" ]; then \
			if [ -d "$$HOME_DIR/.cargo/registry/cache" ] && [ ! -e "$$CARGO_HOME/registry/cache" ]; then \
				ln -s "$$HOME_DIR/.cargo/registry/cache" "$$CARGO_HOME/registry/cache" 2>/dev/null || true; \
			fi; \
			if [ -d "$$HOME_DIR/.cargo/registry/index" ] && [ ! -e "$$CARGO_HOME/registry/index" ]; then \
				ln -s "$$HOME_DIR/.cargo/registry/index" "$$CARGO_HOME/registry/index" 2>/dev/null || true; \
			fi; \
		fi; \
		if [ "$${DYLINT_FORCE_OFFLINE:-0}" = "1" ]; then \
			export CARGO_NET_OFFLINE=true; \
		fi; \
		\
		for dir in "$$DYLINT_DRIVER_PATH"; do \
			if ! mkdir -p "$$dir" 2>/dev/null; then \
				echo "error: cannot create required directory: $$dir" >&2; \
				echo "Set DYLINT_DRIVER_PATH to a writable location." >&2; \
				exit 1; \
			fi; \
			if [ ! -w "$$dir" ]; then \
				echo "error: required directory is not writable: $$dir" >&2; \
				echo "Set DYLINT_DRIVER_PATH to a writable location." >&2; \
				exit 1; \
			fi; \
		done; \
		RUSTUP_HOME_WRITABLE=1; \
		if ! mkdir -p "$$RUSTUP_HOME" 2>/dev/null; then \
			if [ ! -d "$$RUSTUP_HOME" ]; then \
				echo "error: cannot access rustup home: $$RUSTUP_HOME" >&2; \
				echo "Set RUSTUP_HOME to an existing readable location." >&2; \
				exit 1; \
			fi; \
			RUSTUP_HOME_WRITABLE=0; \
		fi; \
		if [ ! -r "$$RUSTUP_HOME" ]; then \
			echo "error: rustup home is not readable: $$RUSTUP_HOME" >&2; \
			echo "Set RUSTUP_HOME to an existing readable location." >&2; \
			exit 1; \
		fi; \
		if [ ! -w "$$RUSTUP_HOME" ]; then \
			RUSTUP_HOME_WRITABLE=0; \
		fi; \
		\
		if ! command -v rustup >/dev/null 2>&1; then \
			if [ "$$CARGO_HOME_WRITABLE" != "1" ]; then \
				echo "error: rustup is not installed and CARGO_HOME is not writable: $$CARGO_HOME" >&2; \
				echo "Set CARGO_HOME to a writable location or preinstall rustup." >&2; \
				exit 1; \
			fi; \
			if [ "$$RUSTUP_HOME_WRITABLE" != "1" ]; then \
				echo "error: rustup is not installed and RUSTUP_HOME is not writable: $$RUSTUP_HOME" >&2; \
				echo "Set RUSTUP_HOME to a writable location or preinstall rustup." >&2; \
				exit 1; \
			fi; \
			echo "rustup not found; installing rustup (required for nightly + rustc-dev)." >&2; \
			if command -v curl >/dev/null 2>&1; then \
				curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path; \
			elif command -v wget >/dev/null 2>&1; then \
				wget -qO- https://sh.rustup.rs | sh -s -- -y --no-modify-path; \
			else \
				echo "error: need curl or wget to install rustup automatically" >&2; \
				exit 1; \
			fi; \
			\
			if [ -n "$$HOME_DIR" ] && [ -f "$$HOME_DIR/.cargo/env" ]; then \
				. "$$HOME_DIR/.cargo/env"; \
			fi; \
			if [ -n "$$HOME_DIR" ] && [ -d "$$HOME_DIR/.cargo/bin" ]; then \
				export PATH="$$HOME_DIR/.cargo/bin:$$PATH"; \
			fi; \
			if [ -d "$$CARGO_HOME/bin" ]; then \
				export PATH="$$CARGO_HOME/bin:$$PATH"; \
			fi; \
		fi; \
		\
		if ! command -v rustup >/dev/null 2>&1; then \
			echo "error: rustup installation succeeded, but rustup is still not on PATH." >&2; \
			echo "Try sourcing $$HOME/.cargo/env or add $$HOME/.cargo/bin (or $$CARGO_HOME/bin) to PATH." >&2; \
			exit 1; \
		fi; \
		RUSTUP_BIN="$$(command -v rustup)"; \
		\
		NIGHTLY_TOOLCHAIN="$$(rustup toolchain list | grep -E "^nightly" | head -n 1 | cut -d" " -f1)"; \
		if [ -z "$$NIGHTLY_TOOLCHAIN" ]; then \
			NIGHTLY_TOOLCHAIN="nightly"; \
		fi; \
		\
		if ! rustup toolchain list | grep -qE "^nightly"; then \
			if [ "$$RUSTUP_HOME_WRITABLE" != "1" ]; then \
				echo "error: nightly toolchain is missing and RUSTUP_HOME is not writable: $$RUSTUP_HOME" >&2; \
				echo "Set RUSTUP_HOME to a writable location or preinstall nightly." >&2; \
				exit 1; \
			fi; \
			if [ "$$DYLINT_QUIET" = "0" ]; then echo "Installing Rust nightly toolchain (required for dylint driver builds)..." >&2; fi; \
			if ! "$$RUSTUP_BIN" toolchain install nightly --profile minimal; then \
				echo "error: failed to install nightly toolchain." >&2; \
				echo "If you are offline, pre-provision nightly:" >&2; \
				echo "  rustup toolchain install nightly --profile minimal" >&2; \
				exit 1; \
			fi; \
		fi; \
		\
		INSTALLED_COMPONENTS="$$(rustup component list --toolchain "$$NIGHTLY_TOOLCHAIN" --installed 2>/dev/null || true)"; \
		MISSING=""; \
		echo "$$INSTALLED_COMPONENTS" | grep -Eq "^rustc-dev([ -]|$$)" || MISSING="$$MISSING rustc-dev"; \
		if ! echo "$$INSTALLED_COMPONENTS" | grep -Eq "^llvm-tools-preview([ -]|$$)" \
			&& ! echo "$$INSTALLED_COMPONENTS" | grep -Eq "^llvm-tools([ -]|$$)"; then \
			MISSING="$$MISSING llvm-tools-preview"; \
		fi; \
		if [ -n "$$MISSING" ]; then \
			if [ "$$RUSTUP_HOME_WRITABLE" != "1" ]; then \
				echo "error: required nightly component(s) missing ($$MISSING) and RUSTUP_HOME is not writable: $$RUSTUP_HOME" >&2; \
				echo "Set RUSTUP_HOME to a writable location or preinstall the missing components." >&2; \
				exit 1; \
			fi; \
			if [ "$$DYLINT_QUIET" = "0" ]; then echo "Installing required nightly components:$$MISSING" >&2; fi; \
			if ! RUSTUP_TERM_QUIET=true rustup component add rustc-dev llvm-tools-preview --toolchain "$$NIGHTLY_TOOLCHAIN" >/dev/null 2>&1; then \
				echo "error: failed to install required nightly component(s):$$MISSING" >&2; \
				echo "Provision them ahead of time (offline/sandboxed):" >&2; \
				echo "  rustup component add rustc-dev llvm-tools-preview --toolchain $$NIGHTLY_TOOLCHAIN" >&2; \
				exit 1; \
			fi; \
		fi; \
		\
		NIGHTLY_CARGO="$$(rustup which cargo --toolchain "$$NIGHTLY_TOOLCHAIN")"; \
		NIGHTLY_BIN_DIR="$$(dirname "$$NIGHTLY_CARGO")"; \
		WRAPPER_DIR="$$(mktemp -d)"; \
		trap "rm -rf $$WRAPPER_DIR" EXIT; \
		printf "%s\n" \
			"#!/usr/bin/env bash" \
			"export RUSTUP_TOOLCHAIN=\"$$NIGHTLY_TOOLCHAIN\"" \
			"exec \"$$NIGHTLY_CARGO\" \"\$$@\"" \
			> "$$WRAPPER_DIR/cargo"; \
		chmod +x "$$WRAPPER_DIR/cargo"; \
		export PATH="$$WRAPPER_DIR:$$NIGHTLY_BIN_DIR:$$PATH"; \
		export RUSTUP_TOOLCHAIN="$$NIGHTLY_TOOLCHAIN"; \
		\
		echo "=== Dylint Environment Debug Info ===" >&2; \
		echo "CARGO_HOME: $$CARGO_HOME" >&2; \
		echo "RUSTUP_HOME: $$RUSTUP_HOME" >&2; \
		echo "DYLINT_DRIVER_PATH: $$DYLINT_DRIVER_PATH" >&2; \
		echo "PATH (first 3 entries): $$(echo $$PATH | cut -d: -f1-3)" >&2; \
		echo "Nightly toolchain: $$NIGHTLY_TOOLCHAIN" >&2; \
		echo "Nightly cargo: $$NIGHTLY_CARGO" >&2; \
		echo "Nightly bin dir: $$NIGHTLY_BIN_DIR" >&2; \
		echo "Wrapper script path: $$WRAPPER_DIR/cargo" >&2; \
		echo "Wrapper script contents:" >&2; \
		while IFS= read -r line; do echo "  $$line" >&2; done < "$$WRAPPER_DIR/cargo"; \
		echo "Resolved cargo (via command -v): $$(command -v cargo)" >&2; \
		echo "cargo --version: $$(cargo --version)" >&2; \
		echo "RUSTUP_TOOLCHAIN: $$RUSTUP_TOOLCHAIN" >&2; \
		echo "===================================" >&2; \
		\
		if ! cargo dylint --version >/dev/null 2>&1; then \
			if [ "$$CARGO_HOME_WRITABLE" != "1" ]; then \
				echo "error: cargo-dylint is not installed and CARGO_HOME is not writable: $$CARGO_HOME" >&2; \
				echo "Set CARGO_HOME to a writable location or preinstall cargo-dylint." >&2; \
				exit 1; \
			fi; \
			echo "Installing cargo-dylint (and dylint-link)..." >&2; \
			if ! cargo install cargo-dylint dylint-link; then \
				echo "error: failed to install cargo-dylint." >&2; \
				echo "If you are offline, preinstall it into $$CARGO_HOME/bin." >&2; \
				echo "  cargo install cargo-dylint dylint-link" >&2; \
				exit 1; \
			fi; \
		fi; \
		\
		RUSTFLAGS="--cap-lints=deny" CARGO_TERM_QUIET=true cargo dylint -q --all -p ralph-workflow -- --lib --quiet >/dev/null 2>&1; \
	'

# Run the canonical verification contract.
ci:
	$(CARGO) xtask verify
	echo "All CI checks passed"

# GUI targets
# Build the Angular frontend and Tauri GUI binary
build-gui:
	cd ralph-gui/ui && bun install && bun run build
	$(CARGO) build -p ralph-gui $(RELEASE_FLAGS)
	echo "GUI build complete: target/release/ralph-gui"

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
	echo "  install       Install to $(INSTALL_BIN) (may need sudo)"
	echo "  install-local Install to ~/.local/bin (no sudo needed)"
	echo "  uninstall     Remove installed binary"
	echo "  check         Run type checks"
	echo "  fmt           Format source code"
	echo "  lint          Run clippy lints"
	echo "  dylint        Run custom dylint lints (lib only)"
	echo "  dylint-verbose Run custom dylint lints with debug output"
	echo "  ci            Run all CI checks"
	echo "  doc           Build and open documentation"
	echo "  version       Print version information"
	echo "  help          Show this help"
	echo ""
	echo "GUI targets:"
	echo "  build-gui       Build the Angular frontend and Tauri GUI binary"
	echo "  install-gui     Install GUI binary only (requires build-gui first)"
	echo "  install-gui-local Install GUI to ~/.local/bin (no sudo needed)"
	echo ""
	echo "Environment variables:"
	echo "  INSTALL_ROOT  Installation prefix (default: /usr/local)"
	echo ""
	echo "Examples:"
	echo "  make release && sudo make install"
	echo "  make install-local"
	echo "  INSTALL_ROOT=/opt make install"
	echo "  make build-gui && sudo make install-gui"
