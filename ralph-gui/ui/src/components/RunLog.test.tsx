import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RunLog } from "./RunLog";

describe("RunLog", () => {
  it("renders empty state when no lines provided", () => {
    render(<RunLog lines={[]} />);
    expect(screen.getByTestId("run-log-empty")).toBeInTheDocument();
  });

  it("renders log lines in a scrollable container", () => {
    const lines = ["alpha", "beta", "gamma", "delta", "epsilon"];
    render(<RunLog lines={lines} />);
    const content = screen.getByTestId("run-log-content");
    expect(content).toBeInTheDocument();
    for (const line of lines) {
      expect(content.textContent).toContain(line);
    }
  });

  it("shows loading state when isLoading is true", () => {
    render(<RunLog lines={[]} isLoading={true} />);
    expect(screen.getByTestId("run-log-loading")).toBeInTheDocument();
  });
});
