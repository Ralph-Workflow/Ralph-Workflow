import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

// Suppress React's error logging during tests to keep output clean.
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

// A component that throws on render for testing.
function ThrowingChild({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error("Test render error");
  }
  return <div>Healthy child</div>;
}

describe("ErrorBoundary", () => {
  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary>
        <div>Safe content</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText("Safe content")).toBeInTheDocument();
  });

  it("renders fallback UI when a child throws", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
  });

  it("shows the error message in the fallback", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );
    // Use getAllByText because the stack trace <pre> also contains the message text
    expect(screen.getAllByText(/Test render error/i).length).toBeGreaterThanOrEqual(1);
  });

  it("renders a Reload page button in the fallback", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("button", { name: /reload page/i })).toBeInTheDocument();
  });

  it("clicking Reload page resets error state and shows children again", () => {
    // Use a controlled prop to simulate boundary recovery.
    const { rerender } = render(
      <ErrorBoundary key="test-boundary">
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );

    // Boundary should show fallback
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    // Swap children to non-throwing BEFORE clicking reset.
    // If we clicked reset first, React would re-render with the still-throwing
    // children, immediately re-entering error state.
    rerender(
      <ErrorBoundary key="test-boundary">
        <ThrowingChild shouldThrow={false} />
      </ErrorBoundary>,
    );

    // Click reload — resets error state; children are now non-throwing
    fireEvent.click(screen.getByRole("button", { name: /reload page/i }));

    expect(screen.getByText("Healthy child")).toBeInTheDocument();
  });

  it("shows expandable stack trace section", () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );
    // There should be a details element for stack trace
    const details = document.querySelector("details");
    expect(details).not.toBeNull();
  });

  it("does not show fallback when no error occurs", () => {
    render(
      <ErrorBoundary>
        <span>Normal render</span>
      </ErrorBoundary>,
    );
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });
});
