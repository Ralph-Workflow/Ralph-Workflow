import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PreflightSummary } from "./PreflightSummary";

const defaultProps = {
  repoPath: "/my/repo",
  worktreePath: null,
  promptPath: "/my/repo/PROMPT.md",
  developerIterations: 5,
  reviewerPasses: 2,
  onConfirm: vi.fn(),
  onBack: vi.fn(),
  isLaunching: false,
};

describe("PreflightSummary", () => {
  it("renders Pre-flight summary section label", () => {
    render(<PreflightSummary {...defaultProps} />);
    expect(screen.getByText("Pre-flight summary")).toBeInTheDocument();
  });

  it("shows the repo_path value in the summary", () => {
    render(<PreflightSummary {...defaultProps} />);
    expect(screen.getByText("/my/repo")).toBeInTheDocument();
  });

  it("shows Direct repository when worktreePath is null", () => {
    render(<PreflightSummary {...defaultProps} worktreePath={null} />);
    expect(screen.getByText("Direct repository")).toBeInTheDocument();
  });

  it("shows worktree path when worktreePath is set", () => {
    render(
      <PreflightSummary
        {...defaultProps}
        worktreePath="/my/worktrees/wt-50-feature"
      />,
    );
    expect(
      screen.getByText("/my/worktrees/wt-50-feature"),
    ).toBeInTheDocument();
  });

  it("shows developer iterations count", () => {
    render(<PreflightSummary {...defaultProps} developerIterations={7} />);
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("shows reviewer passes count", () => {
    render(<PreflightSummary {...defaultProps} reviewerPasses={3} />);
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("calls onConfirm when Launch session button is clicked", () => {
    const onConfirm = vi.fn();
    render(<PreflightSummary {...defaultProps} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByText("Launch session"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("calls onBack when Back button is clicked", () => {
    const onBack = vi.fn();
    render(<PreflightSummary {...defaultProps} onBack={onBack} />);
    fireEvent.click(screen.getByText("Back"));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("shows Launching… and disables buttons when isLaunching is true", () => {
    render(<PreflightSummary {...defaultProps} isLaunching={true} />);
    expect(screen.getByText("Launching…")).toBeInTheDocument();
    expect(screen.getByText("Launching…")).toBeDisabled();
    expect(screen.getByText("Back")).toBeDisabled();
  });
});
