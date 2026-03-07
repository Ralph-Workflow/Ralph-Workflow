import { invoke } from "@tauri-apps/api/core";
import type {
  AgentProfile,
  ConfigView,
  CreateSessionRequest,
  CreateWorktreeResult,
  LaunchSessionArgs,
  PromptReviewResult,
  RunDetail,
  RunStatusSummary,
  SessionSummary,
  WorktreeInfo,
} from "../types";

// All Tauri backend communication goes through this module.
// No direct invoke() calls elsewhere in the codebase.

// --- Session commands ---

export async function getSessions(repoPath: string): Promise<SessionSummary[]> {
  return invoke<SessionSummary[]>("get_sessions", { repo_path: repoPath });
}

export async function createSession(
  request: CreateSessionRequest,
): Promise<SessionSummary> {
  return invoke<SessionSummary>("create_session", { request });
}

export async function getSessionDetail(
  runId: string,
): Promise<SessionSummary> {
  return invoke<SessionSummary>("get_session_detail", { run_id: runId });
}

export async function resumeSession(runId: string): Promise<SessionSummary> {
  return invoke<SessionSummary>("resume_session", { run_id: runId });
}

// --- Worktree commands ---

export async function listWorktrees(
  repoPath: string,
): Promise<WorktreeInfo[]> {
  return invoke<WorktreeInfo[]>("list_worktrees", { repo_path: repoPath });
}

export async function createWorktree(
  repoPath: string,
  branch: string,
  name: string,
  basePath?: string,
): Promise<CreateWorktreeResult> {
  return invoke<CreateWorktreeResult>("create_worktree", {
    repo_path: repoPath,
    branch,
    name,
    base_path: basePath ?? null,
  });
}

export async function switchContext(
  repoPath: string,
  worktreePath: string | null,
): Promise<void> {
  return invoke<void>("switch_context", {
    repo_path: repoPath,
    worktree_path: worktreePath,
  });
}

// --- Config commands ---

export async function getGlobalConfig(): Promise<ConfigView> {
  return invoke<ConfigView>("get_global_config");
}

export async function getProjectConfig(
  repoPath: string,
): Promise<ConfigView | null> {
  return invoke<ConfigView | null>("get_project_config", {
    repo_path: repoPath,
  });
}

export async function getEffectiveConfig(
  repoPath: string,
): Promise<ConfigView> {
  return invoke<ConfigView>("get_effective_config", { repo_path: repoPath });
}

export async function saveGlobalConfig(configToml: string): Promise<void> {
  return invoke<void>("save_global_config", { config_toml: configToml });
}

export async function saveProjectConfig(
  repoPath: string,
  configToml: string,
): Promise<void> {
  return invoke<void>("save_project_config", {
    repo_path: repoPath,
    config_toml: configToml,
  });
}

// --- Run management commands ---

export async function getRunStatus(
  repoPath: string,
  worktreePath: string | null,
): Promise<RunStatusSummary> {
  return invoke<RunStatusSummary>("get_run_status", {
    repo_path: repoPath,
    worktree_path: worktreePath,
  });
}

export async function getResumableRuns(
  repoPath: string,
): Promise<RunDetail[]> {
  return invoke<RunDetail[]>("get_resumable_runs", { repo_path: repoPath });
}

export async function getRunDetail(runId: string): Promise<RunDetail> {
  return invoke<RunDetail>("get_run_detail", { run_id: runId });
}

// --- Prompt file commands ---

export async function readPromptFile(promptPath: string): Promise<string> {
  return invoke<string>("read_prompt_file", { prompt_path: promptPath });
}

export async function savePromptFile(
  promptPath: string,
  content: string,
): Promise<void> {
  return invoke<void>("save_prompt_file", { prompt_path: promptPath, content });
}

export async function reviewPromptWithAi(
  promptContent: string,
): Promise<PromptReviewResult> {
  return invoke<PromptReviewResult>("review_prompt_with_ai", {
    prompt_content: promptContent,
  });
}

// --- Agent profile commands ---

export async function listAgentProfiles(
  repoPath?: string,
): Promise<AgentProfile[]> {
  return invoke<AgentProfile[]>("list_agent_profiles", {
    repo_path: repoPath ?? null,
  });
}

// --- Session launch commands ---

export async function launchRalphSession(
  args: LaunchSessionArgs,
): Promise<string> {
  return invoke<string>("launch_ralph_session", { args });
}

export async function resumeRalphSession(
  runId: string,
  repoPath: string,
): Promise<void> {
  return invoke<void>("resume_ralph_session", {
    run_id: runId,
    repo_path: repoPath,
  });
}
