import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { configureStore } from "@reduxjs/toolkit";
import { Provider } from "react-redux";
import { MemoryRouter } from "react-router-dom";
import sessionReducer from "../../store/slices/sessionSlice";
import worktreeReducer from "../../store/slices/worktreeSlice";
import runReducer from "../../store/slices/runSlice";
import configReducer from "../../store/slices/configSlice";
import { SessionList } from "./SessionList";

const mockNavigate = vi.fn();

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("../../api/tauri", () => ({
  getSessions: vi.fn(),
  resumeSession: vi.fn(),
  resumeRalphSession: vi.fn(),
  getSessionDetail: vi.fn(),
}));

import { getSessions } from "../../api/tauri";
import type { Mock } from "vitest";

const mockGetSessions = getSessions as Mock;

function makeStore() {
  return configureStore({
    reducer: {
      sessions: sessionReducer,
      worktrees: worktreeReducer,
      runs: runReducer,
      config: configReducer,
    },
  });
}

function renderList(repoPath = "/my/repo", onResume = vi.fn()) {
  return render(
    <Provider store={makeStore()}>
      <MemoryRouter>
        <SessionList repoPath={repoPath} onResume={onResume} />
      </MemoryRouter>
    </Provider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockNavigate.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SessionList", () => {
  it("shows empty state when no sessions exist", async () => {
    mockGetSessions.mockResolvedValueOnce([]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText("No sessions yet")).toBeInTheDocument(),
    );
  });

  it("shows loading state while fetching", () => {
    mockGetSessions.mockReturnValueOnce(new Promise(() => undefined));
    renderList();
    expect(screen.getByText(/Loading sessions/)).toBeInTheDocument();
  });

  it("renders session rows when sessions exist", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Test session",
        developer_agent: "claude",
        reviewer_agent: "codex",
        phase: "Review",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/abc-123/)).toBeInTheDocument(),
    );
  });

  it("shows Resume button for paused sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
    ]);
    const onResume = vi.fn();
    renderList("/my/repo", onResume);
    await waitFor(() =>
      expect(screen.getByText("Resume")).toBeInTheDocument(),
    );
  });

  it("does not show Resume button for completed sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-456",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.queryByText("Resume")).not.toBeInTheDocument(),
    );
  });

  it("shows empty filter message when filterStatus excludes all sessions", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-123",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={["running"]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/No sessions match/)).toBeInTheDocument(),
    );
  });

  it("filters sessions by status, showing only matching rows", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "run-paused",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
      {
        run_id: "run-completed",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={["paused"]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/run-paused/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/run-completed/)).not.toBeInTheDocument();
  });

  it("shows all sessions when filterStatus is empty (no filter applied)", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "run-paused",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
      {
        run_id: "run-completed",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Done run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <SessionList
          repoPath="/my/repo"
          filterStatus={[]}
        />
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/run-paused/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/run-completed/)).toBeInTheDocument();
  });

  it("filters sessions by worktree path", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "wt-run",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: "/my/wt-50",
        created_at: "2024-01-01",
        description: "Worktree run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
      {
        run_id: "direct-run",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Direct run",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
    ]);
    render(
      <Provider store={makeStore()}>
        <MemoryRouter>
          <SessionList
            repoPath="/my/repo"
            filterWorktreePath="/my/wt-50"
          />
        </MemoryRouter>
      </Provider>,
    );
    await waitFor(() =>
      expect(screen.getByText(/wt-run/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/direct-run/)).not.toBeInTheDocument();
  });

  it("clicking a session row navigates to /runs/:runId", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "abc-nav-test",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Nav test",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/abc-nav-test/)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/abc-nav-test/));
    expect(mockNavigate).toHaveBeenCalledWith("/runs/abc-nav-test");
  });

  it("clicking Resume button calls onResume without triggering navigation", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "paused-nav-test",
        status: "paused",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Paused nav test",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Review",
      },
    ]);
    const onResume = vi.fn();
    renderList("/my/repo", onResume);
    await waitFor(() =>
      expect(screen.getByText("Resume")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText("Resume"));
    expect(onResume).toHaveBeenCalledWith("paused-nav-test");
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("running and completed sessions do not show a Resume button", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "run-running",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Running",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
      {
        run_id: "run-completed",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Completed",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/run-running/)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Resume")).not.toBeInTheDocument();
  });

  it("renders degraded indicator badge when session is_degraded is true", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "degraded-run-abc",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Running degraded",
        developer_agent: "claude",
        reviewer_agent: "codex",
        phase: "Development",
        is_degraded: true,
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/degraded-run-abc/)).toBeInTheDocument(),
    );
    // The degraded badge renders a ⚠ icon in the RunStatusBadge with title attribute
    const degradedIcon = screen.getByTitle(
      /degraded conditions/i,
    );
    expect(degradedIcon).toBeInTheDocument();
  });

  it("does not render degraded indicator when session is_degraded is false", async () => {
    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "healthy-run-xyz",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Running healthy",
        developer_agent: "claude",
        reviewer_agent: "codex",
        phase: "Development",
        is_degraded: false,
      },
    ]);
    renderList();
    await waitFor(() =>
      expect(screen.getByText(/healthy-run-xyz/)).toBeInTheDocument(),
    );
    expect(
      screen.queryByTitle(/degraded conditions/i),
    ).not.toBeInTheDocument();
  });

  it("polls get_sessions every 5 seconds when a running session exists", async () => {
    // Only fake interval APIs so waitFor (which uses setTimeout) still works
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });

    mockGetSessions.mockResolvedValue([
      {
        run_id: "poll-run-123",
        status: "running",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Running poll test",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Development",
      },
    ]);

    renderList("/my/repo");

    // Wait for the initial fetch to complete
    await waitFor(() => {
      expect(mockGetSessions).toHaveBeenCalledTimes(1);
    });

    const callsAfterMount = mockGetSessions.mock.calls.length;

    // Advance time past the 5-second polling interval
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5001);
    });

    expect(mockGetSessions.mock.calls.length).toBeGreaterThan(callsAfterMount);
  });

  it("does not poll when no sessions are running", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });

    mockGetSessions.mockResolvedValueOnce([
      {
        run_id: "done-run-456",
        status: "completed",
        repo_path: "/my/repo",
        worktree_path: null,
        created_at: "2024-01-01",
        description: "Completed",
        developer_agent: "",
        reviewer_agent: "",
        phase: "Complete",
      },
    ]);

    renderList("/my/repo");

    await waitFor(() => {
      expect(mockGetSessions).toHaveBeenCalledTimes(1);
    });

    const callsAfterMount = mockGetSessions.mock.calls.length;

    // Advance time 10 seconds — no interval should fire for non-running sessions
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10001);
    });

    expect(mockGetSessions.mock.calls.length).toBe(callsAfterMount);
  });
});
