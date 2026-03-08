import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { KeyboardShortcutsHelp } from "./KeyboardShortcutsHelp";

describe("KeyboardShortcutsHelp", () => {
  it("renders the help overlay heading", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    // Use getByRole to avoid matching the shortcut description "Open keyboard shortcuts help"
    expect(screen.getByRole("heading", { name: /keyboard shortcuts/i })).toBeInTheDocument();
  });

  it("renders all defined shortcut entries", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    expect(screen.getByText(/g \+ h/i)).toBeInTheDocument();
    expect(screen.getByText(/g \+ s/i)).toBeInTheDocument();
    expect(screen.getByText(/g \+ w/i)).toBeInTheDocument();
    expect(screen.getByText(/g \+ c/i)).toBeInTheDocument();
  });

  it("renders the ? shortcut entry", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    expect(screen.getAllByText("?").length).toBeGreaterThanOrEqual(1);
  });

  it("renders a close button", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<KeyboardShortcutsHelp onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape key is pressed", () => {
    const onClose = vi.fn();
    render(<KeyboardShortcutsHelp onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders shortcuts grouped by category", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    expect(screen.getByText(/navigation/i)).toBeInTheDocument();
    expect(screen.getByText(/general/i)).toBeInTheDocument();
  });

  it("renders Cmd/Ctrl+N shortcut for new session", () => {
    render(<KeyboardShortcutsHelp onClose={vi.fn()} />);
    expect(screen.getByText(/⌘N|Ctrl\+N/i)).toBeInTheDocument();
  });
});
