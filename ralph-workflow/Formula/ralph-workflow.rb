class RalphWorkflow < Formula
  desc "Multi-agent AI orchestration CLI"
  homepage "https://ralphworkflow.com"
  license "AGPL-3.0-or-later"

  head "https://github.com/Ralph-Workflow/Ralph-Workflow.git", branch: "main"

  def install
    bin.install "ralph-workflow" => "ralph"
  end

  test do
    assert_match "Ralph", shell_output("#{bin}/ralph --help")
  end
end
