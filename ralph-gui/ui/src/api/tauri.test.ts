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
  readPromptFile,
  savePromptFile,
  reviewPromptWithAi,
  listAgentProfiles,
  launchRalphSession,
  resumeRalphSession,
  getAiApiKey,
  saveAiApiKey,
  notifyRunStatusChange,
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

describe("Prompt File API", () => {
  it("readPromptFile calls read_prompt_file with prompt_path", async () => {
    mockInvoke.mockResolvedValueOnce("# My prompt");
    const result = await readPromptFile("/my/repo/PROMPT.md");
    expect(result).toBe("# My prompt");
    expect(mockInvoke).toHaveBeenCalledWith("read_prompt_file", {
      prompt_path: "/my/repo/PROMPT.md",
    });
  });

  it("savePromptFile calls save_prompt_file with path and content", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await savePromptFile("/my/repo/PROMPT.md", "# Content");
    expect(mockInvoke).toHaveBeenCalledWith("save_prompt_file", {
      prompt_path: "/my/repo/PROMPT.md",
      content: "# Content",
    });
  });

  it("reviewPromptWithAi calls review_prompt_with_ai with content", async () => {
    const mockResult = { suggestions: ["Add more detail"], improved_prompt: null };
    mockInvoke.mockResolvedValueOnce(mockResult);
    const result = await reviewPromptWithAi("# My prompt");
    expect(result).toEqual(mockResult);
    expect(mockInvoke).toHaveBeenCalledWith("review_prompt_with_ai", {
      prompt_content: "# My prompt",
    });
  });
});

describe("Agent Profile API", () => {
  it("listAgentProfiles calls list_agent_profiles with repo_path", async () => {
    const profiles = [{ name: "claude-solo", developer_agent: "claude", reviewer_agent: "claude" }];
    mockInvoke.mockResolvedValueOnce(profiles);
    const result = await listAgentProfiles("/my/repo");
    expect(result).toEqual(profiles);
    expect(mockInvoke).toHaveBeenCalledWith("list_agent_profiles", {
      repo_path: "/my/repo",
    });
  });

  it("listAgentProfiles passes null when no repo path", async () => {
    mockInvoke.mockResolvedValueOnce([]);
    await listAgentProfiles();
    expect(mockInvoke).toHaveBeenCalledWith("list_agent_profiles", {
      repo_path: null,
    });
  });
});

describe("Session Launch API", () => {
  it("launchRalphSession calls launch_ralph_session with args", async () => {
    mockInvoke.mockResolvedValueOnce("run-id-abc");
    const args = {
      repo_path: "/my/repo",
      worktree_path: null,
      prompt_path: "/my/repo/PROMPT.md",
      developer_iterations: 5,
      reviewer_passes: 2,
      developer_agent: null,
      reviewer_agent: null,
    };
    const result = await launchRalphSession(args);
    expect(result).toBe("run-id-abc");
    expect(mockInvoke).toHaveBeenCalledWith("launch_ralph_session", { args });
  });

  it("resumeRalphSession calls resume_ralph_session with run_id and repo_path", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await resumeRalphSession("run-id-xyz", "/my/repo");
    expect(mockInvoke).toHaveBeenCalledWith("resume_ralph_session", {
      run_id: "run-id-xyz",
      repo_path: "/my/repo",
    });
  });
});

describe("AI API Key API", () => {
  it("getAiApiKey calls get_ai_api_key with no arguments", async () => {
    mockInvoke.mockResolvedValueOnce("sk-test-key");
    const result = await getAiApiKey();
    expect(result).toBe("sk-test-key");
    expect(mockInvoke).toHaveBeenCalledWith("get_ai_api_key");
  });

  it("getAiApiKey returns empty string when no key is set", async () => {
    mockInvoke.mockResolvedValueOnce("");
    const result = await getAiApiKey();
    expect(result).toBe("");
  });

  it("saveAiApiKey calls save_ai_api_key with api_key", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await saveAiApiKey("sk-new-key-123");
    expect(mockInvoke).toHaveBeenCalledWith("save_ai_api_key", {
      api_key: "sk-new-key-123",
    });
  });

  it("saveAiApiKey rejects when backend returns error", async () => {
    mockInvoke.mockRejectedValueOnce(new Error("API key must not be empty"));
    await expect(saveAiApiKey("")).rejects.toThrow("API key must not be empty");
  });
});

describe("Notification API", () => {
  it("notifyRunStatusChange calls notify_run_status_change with correct args", async () => {
    mockInvoke.mockResolvedValueOnce(undefined);
    await notifyRunStatusChange("Failed", "run-id-abc", "My Repo");
    expect(mockInvoke).toHaveBeenCalledWith("notify_run_status_change", {
      status: "Failed",
      run_id: "run-id-abc",
      context: "My Repo",
    });
  });

  it("notifyRunStatusChange is silent when backend rejects (graceful no-op)", async () => {
    mockInvoke.mockRejectedValueOnce(new Error("Plugin unavailable"));
    // Should not throw — notifications are non-critical
    await expect(notifyRunStatusChange("Failed", "run-id", "ctx")).resolves.toBeUndefined();
  });
});
