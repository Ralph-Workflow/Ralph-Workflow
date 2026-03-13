import { Injectable, InjectionToken, inject } from '@angular/core';
import { invoke as tauriInvoke } from '@tauri-apps/api/core';
import type {
  AgentProfile,
  AgentToolInfo,
  ConfigView,
  CreateSessionRequest,
  CreateWorktreeResult,
  GuiPreferences,
  LaunchSessionArgs,
  PromptReviewResult,
  RunChanges,
  RunDetail,
  RunStatusSummary,
  SessionSummary,
  TemplateInfo,
  WorkspaceEntry,
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

  // --- Workspace commands ---

  async getWorkspaces(): Promise<WorkspaceEntry[]> {
    return this.invoke<WorkspaceEntry[]>('get_workspaces');
  }

  async openWorkspace(path: string): Promise<WorkspaceEntry> {
    return this.invoke<WorkspaceEntry>('open_workspace', { path });
  }

  async closeWorkspace(id: string): Promise<void> {
    return this.invoke<void>('close_workspace', { id });
  }

  async reorderWorkspaces(ids: string[]): Promise<void> {
    return this.invoke<void>('reorder_workspaces', { ids });
  }

  async setWorkspaceNav(id: string, nav: string): Promise<void> {
    return this.invoke<void>('set_workspace_nav', { id, nav });
  }

  async getRecentWorkspaces(): Promise<string[]> {
    return this.invoke<string[]>('get_recent_workspaces');
  }

  async updateWorkspaceRunCount(id: string, count: number): Promise<void> {
    return this.invoke<void>('update_workspace_run_count', { id, count });
  }

  // --- Run log streaming ---

  async subscribeRunLogs(
    runId: string,
    repoPath: string,
    worktreePath: string | null,
  ): Promise<void> {
    return this.invoke<void>('subscribe_run_logs', {
      run_id: runId,
      repo_path: repoPath,
      worktree_path: worktreePath,
    });
  }

  async unsubscribeRunLogs(runId: string): Promise<void> {
    return this.invoke<void>('unsubscribe_run_logs', { run_id: runId });
  }

  // --- Run changes / diff ---

  async getRunChanges(
    repoPath: string,
    worktreePath: string | null,
    iteration?: number,
  ): Promise<RunChanges> {
    return this.invoke<RunChanges>('get_run_changes', {
      repo_path: repoPath,
      worktree_path: worktreePath,
      iteration: iteration ?? null,
    });
  }

  async cancelRun(repoPath: string, worktreePath: string | null): Promise<void> {
    return this.invoke<void>('cancel_run', {
      repo_path: repoPath,
      worktree_path: worktreePath,
    });
  }

  // --- GUI Preferences ---

  async getGuiPreferences(): Promise<GuiPreferences> {
    return this.invoke<GuiPreferences>('get_gui_preferences');
  }

  async saveGuiPreferences(prefs: GuiPreferences): Promise<void> {
    return this.invoke<void>('save_gui_preferences', { prefs });
  }

  // --- Agent tools ---

  async getAgentTools(): Promise<AgentToolInfo[]> {
    return this.invoke<AgentToolInfo[]>('get_agent_tools');
  }

  async testAgentToolConnection(name: string): Promise<string> {
    return this.invoke<string>('test_agent_tool_connection', { name });
  }

  // --- AI prompt assistance ---

  async assistPromptDescribe(description: string, repoPath: string): Promise<string> {
    return this.invoke<string>('assist_prompt_describe', {
      description,
      repo_path: repoPath,
    });
  }

  async assistPromptRefine(currentPrompt: string, repoPath: string): Promise<PromptReviewResult> {
    return this.invoke<PromptReviewResult>('assist_prompt_refine', {
      current_prompt: currentPrompt,
      repo_path: repoPath,
    });
  }

  // --- Prompt templates ---

  async listTemplates(templatesDir: string): Promise<TemplateInfo[]> {
    return this.invoke<TemplateInfo[]>('list_templates', { templates_dir: templatesDir });
  }

  async saveTemplate(
    name: string,
    description: string,
    content: string,
    tags: string[],
    templatesDir: string,
  ): Promise<void> {
    return this.invoke<void>('save_template', {
      name,
      description,
      content,
      tags,
      templates_dir: templatesDir,
    });
  }

  async deleteTemplate(name: string, templatesDir: string): Promise<void> {
    return this.invoke<void>('delete_template', { name, templates_dir: templatesDir });
  }

  // --- Resumable runs ---

  async getResumableRunsForPath(repoPath: string): Promise<RunDetail[]> {
    return this.invoke<RunDetail[]>('get_resumable_runs', { repo_path: repoPath });
  }

}
