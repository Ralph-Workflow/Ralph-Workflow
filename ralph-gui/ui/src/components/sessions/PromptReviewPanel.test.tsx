import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../../api/tauri", () => ({
  reviewPromptWithAi: vi.fn(),
}));

import { reviewPromptWithAi } from "../../api/tauri";
import type { Mock } from "vitest";
import { PromptReviewPanel } from "./PromptReviewPanel";

const mockReview = reviewPromptWithAi as Mock;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PromptReviewPanel", () => {
  it("shows idle state initially with review button", () => {
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={vi.fn()}
      />,
    );
    expect(screen.getByTestId("review-button")).toBeInTheDocument();
    expect(screen.getByText(/Click/)).toBeInTheDocument();
  });

  it("shows loading state while review is pending", async () => {
    let resolvePromise!: (val: unknown) => void;
    mockReview.mockReturnValueOnce(
      new Promise((res) => {
        resolvePromise = res;
      }),
    );
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("review-button"));
    await waitFor(() => {
      expect(screen.getByTestId("loading-indicator")).toBeInTheDocument();
    });
    // Clean up the promise
    resolvePromise({ suggestions: [], improved_prompt: null });
  });

  it("renders suggestions on success", async () => {
    mockReview.mockResolvedValueOnce({
      suggestions: ["Add acceptance criteria", "Specify file scope"],
      improved_prompt: null,
    });
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("review-button"));
    await waitFor(() => {
      expect(screen.getByTestId("suggestions-list")).toBeInTheDocument();
    });
    expect(screen.getByText("Add acceptance criteria")).toBeInTheDocument();
    expect(screen.getByText("Specify file scope")).toBeInTheDocument();
  });

  it("shows apply button when improved_prompt is present", async () => {
    mockReview.mockResolvedValueOnce({
      suggestions: ["Improve clarity"],
      improved_prompt: "# Better prompt",
    });
    const onApply = vi.fn();
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={onApply}
      />,
    );
    fireEvent.click(screen.getByTestId("review-button"));
    await waitFor(() => {
      expect(screen.getByTestId("apply-improved-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("apply-improved-button"));
    expect(onApply).toHaveBeenCalledWith("# Better prompt");
  });

  it("shows error message on failure", async () => {
    mockReview.mockRejectedValueOnce(new Error("Network error"));
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("review-button"));
    await waitFor(() => {
      expect(screen.getByTestId("error-message")).toBeInTheDocument();
    });
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  it("shows no-key message when ANTHROPIC_API_KEY error is returned", async () => {
    mockReview.mockRejectedValueOnce(
      new Error("ANTHROPIC_API_KEY not set. Set it in environment"),
    );
    render(
      <PromptReviewPanel
        promptContent="# My prompt"
        onApplyImprovedPrompt={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("review-button"));
    await waitFor(() => {
      expect(screen.getByTestId("no-key-message")).toBeInTheDocument();
    });
  });
});
