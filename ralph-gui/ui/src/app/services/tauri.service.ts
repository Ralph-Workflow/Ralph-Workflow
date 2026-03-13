import { Injectable, InjectionToken, inject } from '@angular/core';
import { invoke as tauriInvoke } from '@tauri-apps/api/core';
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
} from '../types';

/**
 * Injection token for Tauri's invoke function.
 * Allows mocking in tests by providing a custom implementation.
 */
export const TAURI_INVOKE = new InjectionToken<typeof tauriInvoke>('TAURI_INVOKE', {
  providedIn: 'root',
  factory: () => tauriInvoke,
});

/**
 * All Tauri backend communication goes through this service.
 * No direct invoke() calls elsewhere in the codebase.
 */
@Injectable({ providedIn: 'root' })
export class TauriService {
  private readonly invoke = inject(TAURI_INVOKE);
  // --- Session commands ---

  async getSessions(repoPath: string): Promise<SessionSummary[]> {
    return this.invoke<SessionSummary[]>('get_sessions', { repo_path: repoPath });
  }

  async createSession(request: CreateSessionRequest): Promise<SessionSummary> {
    return this.invoke<SessionSummary>('create_session', { request });
  }

  async getSessionDetail(runId: string): Promise<SessionSummary> {
    return this.invoke<SessionSummary>('get_session_detail', { run_id: runId });
  }

  // --- Worktree commands ---

  async listWorktrees(repoPath: string): Promise<WorktreeInfo[]> {
    return this.invoke<WorktreeInfo[]>('list_worktrees', { repo_path: repoPath });
  }

  async createWorktree(
    repoPath: string,
    branch: string,
    name: string,
    basePath?: string,
  ): Promise<CreateWorktreeResult> {
    return this.invoke<CreateWorktreeResult>('create_worktree', {
      repo_path: repoPath,
      branch,
      name,
      base_path: basePath ?? null,
    });
  }

  async switchContext(
    repoPath: string,
    worktreePath: string | null,
  ): Promise<void> {
    return this.invoke<void>('switch_context', {
      repo_path: repoPath,
      worktree_path: worktreePath,
    });
  }

  // --- Config commands ---

  async getGlobalConfig(): Promise<ConfigView> {
    return this.invoke<ConfigView>('get_global_config');
  }

  async getProjectConfig(repoPath: string): Promise<ConfigView | null> {
    return this.invoke<ConfigView | null>('get_project_config', {
      repo_path: repoPath,
    });
  }

  async getEffectiveConfig(repoPath: string): Promise<ConfigView> {
    return this.invoke<ConfigView>('get_effective_config', { repo_path: repoPath });
  }

  async saveGlobalConfig(configToml: string): Promise<void> {
    return this.invoke<void>('save_global_config', { config_toml: configToml });
  }

  async saveProjectConfig(
    repoPath: string,
    configToml: string,
  ): Promise<void> {
    return this.invoke<void>('save_project_config', {
      repo_path: repoPath,
      config_toml: configToml,
    });
  }

  async getRawGlobalConfigToml(): Promise<string> {
    return this.invoke<string>('get_raw_global_config_toml');
  }

  async getRawProjectConfigToml(repoPath: string): Promise<string> {
    return this.invoke<string>('get_raw_project_config_toml', { repo_path: repoPath });
  }

  // --- Run management commands ---

  async getRunStatus(
    repoPath: string,
    worktreePath: string | null,
  ): Promise<RunStatusSummary> {
    return this.invoke<RunStatusSummary>('get_run_status', {
      repo_path: repoPath,
      worktree_path: worktreePath,
    });
  }

  async getResumableRuns(repoPath: string): Promise<RunDetail[]> {
    return this.invoke<RunDetail[]>('get_resumable_runs', { repo_path: repoPath });
  }

  async getRunDetail(runId: string): Promise<RunDetail> {
    return this.invoke<RunDetail>('get_run_detail', { run_id: runId });
  }

  async getRunLogs(
    repoPath: string,
    worktreePath: string | null,
    maxLines?: number,
  ): Promise<string[]> {
    return this.invoke<string[]>('get_run_logs', {
      repo_path: repoPath,
      worktree_path: worktreePath,
      max_lines: maxLines ?? 500,
    });
  }

  // --- Prompt file commands ---

  async readPromptFile(promptPath: string): Promise<string> {
    return this.invoke<string>('read_prompt_file', { prompt_path: promptPath });
  }

  async savePromptFile(promptPath: string, content: string): Promise<void> {
    return this.invoke<void>('save_prompt_file', { prompt_path: promptPath, content });
  }

  async reviewPromptWithAi(promptContent: string): Promise<PromptReviewResult> {
    return this.invoke<PromptReviewResult>('review_prompt_with_ai', {
      prompt_content: promptContent,
    });
  }

  // --- AI API key commands ---

  async getAiApiKey(): Promise<string> {
    return this.invoke<string>('get_ai_api_key');
  }

  async saveAiApiKey(apiKey: string): Promise<void> {
    return this.invoke<void>('save_ai_api_key', { api_key: apiKey });
  }

  // --- Notification commands ---

  async notifyRunStatusChange(
    status: string,
    runId: string,
    context: string,
  ): Promise<void> {
    // Graceful no-op if the notification plugin is unavailable.
    try {
      await this.invoke<void>('notify_run_status_change', {
        status,
        run_id: runId,
        context,
      });
    } catch {
      // Notifications are non-critical — swallow errors silently.
    }
  }

  // --- Config validation ---

  async validateConfigToml(configToml: string): Promise<string | null> {
    return this.invoke<string | null>('validate_config_toml', {
      config_toml: configToml,
    });
  }

  // --- Agent profile commands ---

  async listAgentProfiles(repoPath?: string): Promise<AgentProfile[]> {
    return this.invoke<AgentProfile[]>('list_agent_profiles', {
      repo_path: repoPath ?? null,
    });
  }

  // --- Session launch commands ---

  async launchRalphSession(args: LaunchSessionArgs): Promise<string> {
    return this.invoke<string>('launch_ralph_session', { args });
  }

  async resumeRalphSession(runId: string, repoPath: string): Promise<void> {
    return this.invoke<void>('resume_ralph_session', {
      run_id: runId,
      repo_path: repoPath,
    });
  }

  async openDirectoryDialog(): Promise<string | null> {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const selected = await open({
      directory: true,
      multiple: false,
      title: 'Open Workspace',
    });
    return typeof selected === 'string' ? selected : null;
  }
}
