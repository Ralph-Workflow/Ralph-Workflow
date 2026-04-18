class RalphWorkflow < Formula
  desc "Multi-agent AI orchestration CLI"
  homepage "https://github.com/your-org/ralph-workflow"
  license "MIT"

  # TODO: Update URL and sha256 after first GitHub release
  # The release asset should be named: ralph-darwin-universal2.tar.gz
  # Build it with: make dist-binary
  url "https://github.com/your-org/ralph-workflow/releases/download/v0.1.0/ralph-darwin-universal2.tar.gz"
  sha256 "TODO: Run `shasum -a 256 ralph-darwin-universal2.tar.gz` after downloading the release asset"

  head "https://github.com/your-org/ralph-workflow.git", branch: "main"

  def install
    # Install the standalone binary (no Python required)
    bin.install "ralph-workflow" => "ralph"
  end

  test do
    # Verify the binary runs and shows help
    assert_match "Ralph", shell_output("#{bin}/ralph --help")
  end
end
