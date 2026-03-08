import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import runReducer from "../store/slices/runSlice";
import sessionReducer from "../store/slices/sessionSlice";
import worktreeReducer from "../store/slices/worktreeSlice";
import configReducer from "../store/slices/configSlice";
import { RunDetail } from "./RunDetail";

vi.mock("../api/tauri", () => ({
  getRunDetail: vi.fn(),
  getRunStatus: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
  resumeSession: vi.fn(),
  getRunLogs: vi.fn().mockResolvedValue([]),
}));

import { getRunDetail, getRunLogs } from "../api/tauri";
import type { Mock } from "vitest";

const mockGetRunDetail = getRunDetail as Mock;
const mockGetRunLogs = getRunLogs as Mock;

const mockRun = {
  run_id: "run-detail-123",
  status: "Paused" as const,
  current_phase: "Review",
  last_checkpoint: "2024-01-01 12:00:00",
  agent_profile: "claude/codex",
  repo_path: "/my/repo",
  worktree_path: null,
  created_at: "2024-01-01 10:00:00",
  description: "Test run",
};

function makeStore() {
  return configureStore({
    reducer: {
      runs: runReducer,
      sessions: sessionReducer,
      worktrees: worktreeReducer,
      config: configReducer,
    },
  });
}

function renderDetail(runId = "run-detail-123") {
  return render(
    <Provider store={makeStore()}>
      <MemoryRouter initialEntries={[`/runs/${runId}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetail />} />
        </Routes>
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetRunLogs.mockResolvedValue([]);
});

describe("RunDetail", () => {
  it("shows loading state initially", () => {
    mockGetRunDetail.mockReturnValueOnce(new Promise(() => undefined));
    renderDetail();
    expect(screen.getByText(/Loading run/)).toBeInTheDocument();
  });

  it("renders run ID and phase after load", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("run-detail-123")).toBeInTheDocument(),
    );
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("shows Resume button for Paused run", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() =>
      expect(screen.getByText("Resume")).toBeInTheDocument(),
    );
  });

  it("does not show Resume for Completed run", async () => {
    mockGetRunDetail.mockResolvedValueOnce({
      ...mockRun,
      status: "Completed" as const,
    });
    renderDetail();
    await waitFor(() =>
      expect(screen.queryByText("Resume")).not.toBeInTheDocument(),
    );
  });

  it("shows error state when run is not found", async () => {
    mockGetRunDetail.mockRejectedValueOnce(new Error("Run not found"));
    renderDetail("nonexistent");
    await waitFor(() => {
      const matches = screen.getAllByText("Run not found");
      expect(matches.length).toBeGreaterThan(0);
    });
  });

  it("renders all detail row labels when run is loaded", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.getByText("status")).toBeInTheDocument();
    expect(screen.getByText("phase")).toBeInTheDocument();
    expect(screen.getByText("agent_profile")).toBeInTheDocument();
    expect(screen.getByText("repo_path")).toBeInTheDocument();
  });

  it("shows Back button when run is loaded", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    const backBtn = screen.getByText("← Back");
    expect(backBtn).toBeInTheDocument();
  });

  // --- Diagnostics field tests ---

  it("shows iteration_count row when run is loaded", async () => {
    // Use 42 to avoid collision with phase timeline step numbers (1–4)
    mockGetRunDetail.mockResolvedValueOnce({ ...mockRun, iteration_count: 42 });
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.getByText("iteration_count")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("shows iteration_count as 0 when field is absent", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.getByText("iteration_count")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("shows last_error row when run has a last_error", async () => {
    mockGetRunDetail.mockResolvedValueOnce({
      ...mockRun,
      last_error: "Agent timeout after 120s",
    });
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.getByText("last_error")).toBeInTheDocument();
    expect(screen.getByText("Agent timeout after 120s")).toBeInTheDocument();
  });

  it("does not show last_error row when last_error is absent", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.queryByText("last_error")).not.toBeInTheDocument();
  });

  it("shows degraded-condition banner when is_degraded is true", async () => {
    mockGetRunDetail.mockResolvedValueOnce({
      ...mockRun,
      is_degraded: true,
    });
    renderDetail();
    await waitFor(() => {
      expect(screen.getByTestId("degraded-banner")).toBeInTheDocument();
    });
    expect(screen.getByText(/degraded conditions/i)).toBeInTheDocument();
  });

  it("does not show degraded-condition banner when is_degraded is false or absent", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.queryByTestId("degraded-banner")).not.toBeInTheDocument();
  });

  // --- Run Log section tests ---

  it("shows Run Log section heading when run is loaded", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    expect(screen.getByText("Run Log")).toBeInTheDocument();
  });

  it("expanding Run Log section calls getRunLogs and displays lines", async () => {
    mockGetRunDetail.mockResolvedValueOnce(mockRun);
    mockGetRunLogs.mockResolvedValueOnce(["first log line", "second log line"]);
    renderDetail();
    await waitFor(() => expect(screen.getByText("run-detail-123")).toBeInTheDocument());
    const expandBtn = screen.getByTestId("toggle-run-log");
    fireEvent.click(expandBtn);
    await waitFor(() => {
      expect(mockGetRunLogs).toHaveBeenCalled();
    });
    await waitFor(() => {
      const content = screen.getByTestId("run-log-content");
      expect(content.textContent).toContain("first log line");
      expect(content.textContent).toContain("second log line");
    });
  });
});
