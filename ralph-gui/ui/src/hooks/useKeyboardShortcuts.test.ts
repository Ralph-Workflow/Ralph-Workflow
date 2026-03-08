import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useKeyboardShortcuts } from "./useKeyboardShortcuts";

function fireKey(key: string, options: Partial<KeyboardEventInit> = {}) {
  document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...options }));
}

describe("useKeyboardShortcuts", () => {
  let onHelpOpen: ReturnType<typeof vi.fn>;
  let onNavigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onHelpOpen = vi.fn();
    onNavigate = vi.fn();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("pressing ? fires onHelpOpen callback", () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("?");
    expect(onHelpOpen).toHaveBeenCalledTimes(1);
  });

  it("pressing Escape does not fire onHelpOpen", () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("Escape");
    expect(onHelpOpen).not.toHaveBeenCalled();
  });

  it("g+h chord fires navigate('/') callback", async () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("g");
    // Small delay for chord window
    await new Promise((r) => setTimeout(r, 10));
    fireKey("h");
    await new Promise((r) => setTimeout(r, 50));
    expect(onNavigate).toHaveBeenCalledWith("/");
  });

  it("g+s chord fires navigate('/sessions') callback", async () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("g");
    await new Promise((r) => setTimeout(r, 10));
    fireKey("s");
    await new Promise((r) => setTimeout(r, 50));
    expect(onNavigate).toHaveBeenCalledWith("/sessions");
  });

  it("g+w chord fires navigate('/worktrees') callback", async () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("g");
    await new Promise((r) => setTimeout(r, 10));
    fireKey("w");
    await new Promise((r) => setTimeout(r, 50));
    expect(onNavigate).toHaveBeenCalledWith("/worktrees");
  });

  it("g+c chord fires navigate('/configuration') callback", async () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    fireKey("g");
    await new Promise((r) => setTimeout(r, 10));
    fireKey("c");
    await new Promise((r) => setTimeout(r, 50));
    expect(onNavigate).toHaveBeenCalledWith("/configuration");
  });

  it("does not fire navigation chord when event target is an INPUT", () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    const input = document.createElement("input");
    document.body.appendChild(input);
    document.dispatchEvent(
      new KeyboardEvent("keydown", { key: "g", bubbles: true }),
    );
    // Key from input target — use a real input event
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "h", bubbles: true }),
    );
    // Navigation should not fire since the second key was from an input
    expect(onNavigate).not.toHaveBeenCalled();
    document.body.removeChild(input);
  });

  it("does not fire ? shortcut when event target is TEXTAREA", () => {
    renderHook(() => useKeyboardShortcuts({ onHelpOpen, onNavigate }));
    const textarea = document.createElement("textarea");
    document.body.appendChild(textarea);
    textarea.dispatchEvent(
      new KeyboardEvent("keydown", { key: "?", bubbles: true }),
    );
    expect(onHelpOpen).not.toHaveBeenCalled();
    document.body.removeChild(textarea);
  });

  it("cleans up event listeners on unmount", () => {
    const removeEventListenerSpy = vi.spyOn(document, "removeEventListener");
    const { unmount } = renderHook(() =>
      useKeyboardShortcuts({ onHelpOpen, onNavigate }),
    );
    unmount();
    expect(removeEventListenerSpy).toHaveBeenCalled();
    removeEventListenerSpy.mockRestore();
  });
});
