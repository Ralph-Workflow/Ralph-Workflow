import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RunStatusBadge } from "./RunStatusBadge";

describe("RunStatusBadge", () => {
  it("renders Running label when showLabel is true", () => {
    render(<RunStatusBadge status="Running" showLabel />);
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("renders Paused label", () => {
    render(<RunStatusBadge status="Paused" showLabel />);
    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  it("renders Completed label", () => {
    render(<RunStatusBadge status="Completed" showLabel />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("renders Failed label", () => {
    render(<RunStatusBadge status="Failed" showLabel />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders Not Started label for NotStarted status", () => {
    render(<RunStatusBadge status="NotStarted" showLabel />);
    expect(screen.getByText("Not Started")).toBeInTheDocument();
  });

  it("does not render label text when showLabel is false", () => {
    render(<RunStatusBadge status="Running" showLabel={false} />);
    expect(screen.queryByText("Running")).not.toBeInTheDocument();
  });

  it("sets title attribute to description for Running status", () => {
    const { container } = render(<RunStatusBadge status="Running" />);
    const badge = container.querySelector(
      '[title="Pipeline is actively executing"]',
    );
    expect(badge).toBeInTheDocument();
  });

  it("sets title attribute to description for Paused status", () => {
    const { container } = render(<RunStatusBadge status="Paused" />);
    const badge = container.querySelector(
      '[title="Pipeline is interrupted and can be resumed"]',
    );
    expect(badge).toBeInTheDocument();
  });

  it("sets title attribute to description for Completed status", () => {
    const { container } = render(<RunStatusBadge status="Completed" />);
    const badge = container.querySelector(
      '[title="Pipeline completed successfully"]',
    );
    expect(badge).toBeInTheDocument();
  });

  it("sets title attribute to description for Failed status", () => {
    const { container } = render(<RunStatusBadge status="Failed" />);
    const badge = container.querySelector(
      '[title="Pipeline encountered an unrecoverable error"]',
    );
    expect(badge).toBeInTheDocument();
  });

  it("sets title attribute to description for NotStarted status", () => {
    const { container } = render(<RunStatusBadge status="NotStarted" />);
    const badge = container.querySelector(
      '[title="No active pipeline in this repository"]',
    );
    expect(badge).toBeInTheDocument();
  });

  it("renders Degraded secondary badge when isDegraded is true", () => {
    render(<RunStatusBadge status="Running" showLabel isDegraded />);
    expect(screen.getByText("Degraded")).toBeInTheDocument();
  });

  it("does not render Degraded badge when isDegraded is false", () => {
    render(<RunStatusBadge status="Running" showLabel isDegraded={false} />);
    expect(screen.queryByText("Degraded")).not.toBeInTheDocument();
  });

  it("does not render Degraded badge when isDegraded is absent", () => {
    render(<RunStatusBadge status="Running" showLabel />);
    expect(screen.queryByText("Degraded")).not.toBeInTheDocument();
  });
});
