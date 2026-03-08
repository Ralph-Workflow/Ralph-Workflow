import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { App } from "./App";

vi.mock("./api/tauri", () => ({
  listWorktrees: vi.fn().mockResolvedValue([]),
  getResumableRuns: vi.fn().mockResolvedValue([]),
  switchContext: vi.fn().mockResolvedValue(undefined),
  getGlobalConfig: vi.fn().mockResolvedValue({
    verbosity: 1,
    developer_iters: 3,
    reviewer_reviews: 2,
    checkpoint_enabled: true,
    isolation_mode: false,
    interactive: false,
    review_depth: "standard",
    max_dev_continuations: 2,
  }),
  getEffectiveConfig: vi.fn().mockResolvedValue(null),
  getRawGlobalConfigToml: vi.fn().mockResolvedValue(""),
  getRawProjectConfigToml: vi.fn().mockResolvedValue(""),
  getSessions: vi.fn().mockResolvedValue([]),
}));

describe("App", () => {
  it("renders the app without crashing", () => {
    render(<App />);
    // App renders the navigation sidebar with the Ralph brand
    expect(screen.getByText("Ralph")).toBeInTheDocument();
  });

  it("shows the workflow label in the sidebar", () => {
    render(<App />);
    expect(screen.getByText("workflow")).toBeInTheDocument();
  });
});
