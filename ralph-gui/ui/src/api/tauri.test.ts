import { describe, it, expect, vi, beforeEach } from "vitest";
import type { Mock } from "vitest";

// Mock the @tauri-apps/api/core module before imports
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import {
  getSessions,
  createSession,
  getSessionDetail,
  resumeSession,
  listWorktrees,
  createWorktree,
  switchContext,
  getGlobalConfig,
  getProjectConfig,
  getEffectiveConfig,
  saveGlobalConfig,
  saveProjectConfig,
  getRunStatus,
  getResumableRuns,
  getRunDetail,
} from "./tauri";

const mockInvoke = invoke as Mock;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Session API", () => {
  it("getSessions calls get_sessions with correct repo_path", async () => {
    mockInvoke.mockResolvedValueOnce([]);
    await getSessions("/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("get_sessions", {
      repo_path: "/my/repo",
    });
  });

  it("createSession calls create_session with correct request shape", async () => {
    const request = {
      repo_path: "/my/repo",
      worktree_path: null,
      prompt_path: "/my/repo/PROMPT.md",
      developer_iterations: 3,
      reviewer_passes: 2,
    };
    mockInvoke.mockResolvedValueOnce({
      run_id: "abc",
      status: "pending",
      repo_path: "/my/repo",
      worktree_path: null,
      created_at: "2024-01-01",
      description: "test",
      developer_agent: "",
      reviewer_agent: "",
      phase: "Pending",
    });
    await createSession(request);
    expect(mockInvoke).toHaveBeenCalledWith("create_session", { request });
  });

  it("getSessionDetail calls get_session_detail with run_id", async () => {
    mockInvoke.mockRejectedValueOnce(new Error("Session not found: test-id"));
    await expect(getSessionDetail("test-id")).rejects.toThrow();
    expect(mockInvoke).toHaveBeenCalledWith("get_session_detail", {
      run_id: "test-id",
    });
  });

  it("resumeSession calls resume_session with run_id", async () => {
    mockInvoke.mockRejectedValueOnce(new Error("not resumable"));
    await expect(resumeSession("test-id")).rejects.toThrow();
    expect(mockInvoke).toHaveBeenCalledWith("resume_session", {
      run_id: "test-id",
    });
  });
});

describe("Worktree API", () => {
  it("listWorktrees calls list_worktrees with repo_path", async () => {
    mockInvoke.mockResolvedValueOnce([]);
    await listWorktrees("/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("list_worktrees", {
      repo_path: "/my/repo",
    });
  });

  it("createWorktree calls create_worktree with correct arguments", async () => {
    mockInvoke.mockResolvedValueOnce({
      worktree: {
        path: "/my/repo/../wt-50-feature",
        branch: "wt-50-feature",
        name: "wt-50-feature",
        has_active_run: false,
        is_main: false,
      },
    });
    await createWorktree("/my/repo", "wt-50-feature", "wt-50-feature");
    expect(mockInvoke).toHaveBeenCalledWith("create_worktree", {
      repo_path: "/my/repo",
      branch: "wt-50-feature",
      name: "wt-50-feature",
      base_path: null,
    });
  });

  it("createWorktree passes base_path when provided", async () => {
    mockInvoke.mockResolvedValueOnce({
      worktree: {
        path: "/projects/wt-50-feature",
        branch: "wt-50-feature",
        name: "wt-50-feature",
        has_active_run: false,
        is_main: false,
      },
    });
    await createWorktree(
      "/my/repo",
      "wt-50-feature",
      "wt-50-feature",
      "/projects/wt-50-feature",
    );
    expect(mockInvoke).toHaveBeenCalledWith("create_worktree", {
      repo_path: "/my/repo",
      branch: "wt-50-feature",
      name: "wt-50-feature",
      base_path: "/projects/wt-50-feature",
    });
  });

  it("switchContext calls switch_context with repo_path and worktree_path", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await switchContext("/my/repo", "/my/wt-50");
    expect(mockInvoke).toHaveBeenCalledWith("switch_context", {
      repo_path: "/my/repo",
      worktree_path: "/my/wt-50",
    });
  });

  it("switchContext passes null worktree_path for direct-repo mode", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await switchContext("/my/repo", null);
    expect(mockInvoke).toHaveBeenCalledWith("switch_context", {
      repo_path: "/my/repo",
      worktree_path: null,
    });
  });
});

describe("Config API", () => {
  it("getGlobalConfig calls get_global_config with no arguments", async () => {
    mockInvoke.mockResolvedValueOnce({
      verbosity: 2,
      developer_iters: 5,
      reviewer_reviews: 2,
      checkpoint_enabled: true,
      isolation_mode: true,
      interactive: true,
      review_depth: "standard",
      max_dev_continuations: 2,
    });
    await getGlobalConfig();
    expect(mockInvoke).toHaveBeenCalledWith("get_global_config");
  });

  it("getProjectConfig calls get_project_config with repo_path", async () => {
    mockInvoke.mockResolvedValueOnce(null);
    await getProjectConfig("/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("get_project_config", {
      repo_path: "/my/repo",
    });
  });

  it("getEffectiveConfig calls get_effective_config with repo_path", async () => {
    mockInvoke.mockResolvedValueOnce({
      verbosity: 3,
      developer_iters: 7,
      reviewer_reviews: 2,
      checkpoint_enabled: true,
      isolation_mode: true,
      interactive: true,
      review_depth: "standard",
      max_dev_continuations: 2,
    });
    await getEffectiveConfig("/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("get_effective_config", {
      repo_path: "/my/repo",
    });
  });

  it("saveGlobalConfig calls save_global_config with config_toml", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await saveGlobalConfig("[general]\nverbosity = 3\n");
    expect(mockInvoke).toHaveBeenCalledWith("save_global_config", {
      config_toml: "[general]\nverbosity = 3\n",
    });
  });

  it("saveProjectConfig calls save_project_config with correct arguments", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await saveProjectConfig("/my/repo", "[general]\ndeveloper_iters = 5\n");
    expect(mockInvoke).toHaveBeenCalledWith("save_project_config", {
      repo_path: "/my/repo",
      config_toml: "[general]\ndeveloper_iters = 5\n",
    });
  });
});

describe("Run Management API", () => {
  it("getRunStatus calls get_run_status with repo_path and worktree_path", async () => {
    mockInvoke.mockResolvedValueOnce({
      status: "NotStarted",
      run_id: null,
      current_phase: null,
      last_checkpoint: null,
    });
    await getRunStatus("/my/repo", null);
    expect(mockInvoke).toHaveBeenCalledWith("get_run_status", {
      repo_path: "/my/repo",
      worktree_path: null,
    });
  });

  it("getResumableRuns calls get_resumable_runs with repo_path", async () => {
    mockInvoke.mockResolvedValueOnce([]);
    await getResumableRuns("/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("get_resumable_runs", {
      repo_path: "/my/repo",
    });
  });

  it("getRunDetail calls get_run_detail with run_id", async () => {
    mockInvoke.mockRejectedValueOnce(new Error("Run not found: test-id"));
    await expect(getRunDetail("test-id")).rejects.toThrow();
    expect(mockInvoke).toHaveBeenCalledWith("get_run_detail", {
      run_id: "test-id",
    });
  });
});
