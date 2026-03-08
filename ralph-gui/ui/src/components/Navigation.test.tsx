import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Navigation } from "./Navigation";

function renderNav(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Navigation />
    </MemoryRouter>,
  );
}

describe("Navigation", () => {
  it("renders all four nav items", () => {
    renderNav();
    expect(screen.getByText("Home")).toBeInTheDocument();
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Worktrees")).toBeInTheDocument();
    expect(screen.getByText("Configuration")).toBeInTheDocument();
  });

  it("Configuration link points to /configuration (not /config)", () => {
    renderNav();
    const configLink = screen.getByText("Configuration").closest("a");
    expect(configLink).toHaveAttribute("href", "/configuration");
  });

  it("Sessions link points to /sessions", () => {
    renderNav();
    const link = screen.getByText("Sessions").closest("a");
    expect(link).toHaveAttribute("href", "/sessions");
  });

  it("Worktrees link points to /worktrees", () => {
    renderNav();
    const link = screen.getByText("Worktrees").closest("a");
    expect(link).toHaveAttribute("href", "/worktrees");
  });

  it("mouse hover events fire on nav links without errors", () => {
    renderNav();
    const homeLink = screen.getByText("Home").closest("a");
    expect(homeLink).not.toBeNull();
    // Fire hover events to cover onMouseEnter/onMouseLeave handlers
    if (homeLink === null) throw new Error("homeLink should not be null");
    fireEvent.mouseEnter(homeLink);
    fireEvent.mouseLeave(homeLink);
    // If no error thrown, handlers executed correctly
    expect(homeLink).toBeInTheDocument();
  });
});
