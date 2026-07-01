# ---------------------------------------------------------------------------
# NON-PUBLISHED BUILD SCAFFOLD — NOT INSTALLABLE AS-IS.
#
# This formula is a contributor-only scaffold for the standalone PyInstaller
# binary (see `make dist-binary`). It is NOT in a Homebrew tap and there is
# no published release asset yet, so `url`/`sha256` are intentionally
# commented out and use placeholder values that do NOT assert a real version.
#
# To publish a real bottle when a release is cut:
#   1. Build the binary: `make dist-binary`
#   2. Rename for Homebrew: `mv dist/ralph-workflow dist/ralph-darwin-universal2`
#   3. Create tarball: `tar -czvf ralph-darwin-universal2.tar.gz -C dist ralph-darwin-universal2`
#   4. Compute sha256: `shasum -a 256 ralph-darwin-universal2.tar.gz`
#   5. Read the version from `ralph/__init__.py` (the canonical version source).
#   6. Uncomment the `url`/`sha256` lines below, replace `vVERSION` and
#      `SHA256` with the real values, upload the asset, and open a tap PR.
#   7. Validate the formula with `make formula-check` (runs `ruby -c`).
#
# All metadata values below are sourced from pyproject.toml — DO NOT
# introduce placeholder-org literals in any field.
# ---------------------------------------------------------------------------
class RalphWorkflow < Formula
  desc "Multi-agent AI orchestration CLI"
  homepage "https://ralphworkflow.com"
  license "AGPL-3.0-or-later"

  # TODO: Update URL and sha256 after first release (see banner above)
  # url "https://codeberg.org/RalphWorkflow/Ralph-Workflow/releases/download/vVERSION/ralph-darwin-universal2.tar.gz"
  # sha256 "SHA256"

  head "https://codeberg.org/RalphWorkflow/Ralph-Workflow.git", branch: "main"

  def install
    # Install the standalone binary (no Python required)
    bin.install "ralph-workflow" => "ralph"
  end

  test do
    # Verify the binary runs and shows help
    assert_match "Ralph", shell_output("#{bin}/ralph --help")
  end
end
