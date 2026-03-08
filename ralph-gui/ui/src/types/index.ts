// TypeScript interfaces matching Rust command return types.
// Each interface is linked to its Rust counterpart in comments.

// --- Session types ---
// Rust: ralph_gui::commands::session::SessionSummary

export type RunStatusString =
  | "pending"
  | "running"
  | "paused"
  | "interrupted"
  | "completed"
  | "failed";

export interface SessionSummary {
  run_id: string;
  status: RunStatusString;
  repo_path: string;
  worktree_path: string | null;
  created_at: string;
  description: string;
  developer_agent: string;
  reviewer_agent: string;
  phase: string;
}

// Rust: ralph_gui::commands::session::CreateSessionRequest
export interface CreateSessionRequest {
  repo_path: string;
  worktree_path: string | null;
  prompt_path: string;
  developer_iterations: number;
  reviewer_passes: number;
  developer_agent?: string;
  reviewer_agent?: string;
}

// --- Worktree types ---
// Rust: ralph_gui::commands::worktree::WorktreeInfo
export interface WorktreeInfo {
  path: string;
  branch: string;
  name: string;
  has_active_run: boolean;
  is_main: boolean;
}

// Rust: ralph_gui::commands::worktree::CreateWorktreeResult
export interface CreateWorktreeResult {
  worktree: WorktreeInfo;
}

// --- Config types ---
// Rust: ralph_gui::commands::config::ConfigView
export interface ConfigView {
  verbosity: number;
  developer_iters: number;
  reviewer_reviews: number;
  checkpoint_enabled: boolean;
  isolation_mode: boolean;
  interactive: boolean;
  review_depth: string;
  max_dev_continuations: number;
}

// --- Run management types ---
// Rust: ralph_gui::commands::run_management::RunStatus
export type RunStatus =
  | "Running"
  | "Paused"
  | "Completed"
  | "Failed"
  | "NotStarted";

// Rust: ralph_gui::commands::run_management::RunStatusSummary
export interface RunStatusSummary {
  status: RunStatus;
  run_id: string | null;
  current_phase: string | null;
  last_checkpoint: string | null;
}

// Rust: ralph_gui::commands::run_management::RunDetail
export interface RunDetail {
  run_id: string;
  status: RunStatus;
  current_phase: string;
  last_checkpoint: string | null;
  agent_profile: string;
  repo_path: string;
  worktree_path: string | null;
  created_at: string;
  description: string;
  /** Number of developer iterations completed. Defaults to 0 for older checkpoints. */
  iteration_count?: number;
  /** Last error message recorded in the checkpoint, if any. */
  last_error?: string | null;
  /** True when the run is operating in degraded conditions (retries exceeded, fallback used). */
  is_degraded?: boolean;
}

// --- App state types ---
export interface ActiveContext {
  repo_path: string | null;
  worktree_path: string | null;
}

// --- Agent profile types ---
// Rust: ralph_gui::commands::config::AgentProfile
export interface AgentProfile {
  name: string;
  developer_agent: string;
  reviewer_agent: string;
}

// --- AI prompt review types ---
// Rust: ralph_gui::commands::session_prompt::PromptReviewResult
export interface PromptReviewResult {
  suggestions: string[];
  improved_prompt: string | null;
}

// --- Session launch types ---
// Rust: ralph_gui::commands::session_launch::LaunchSessionArgs
export interface LaunchSessionArgs {
  repo_path: string;
  worktree_path: string | null;
  prompt_path: string;
  developer_iterations: number;
  reviewer_passes: number;
  developer_agent: string | null;
  reviewer_agent: string | null;
}
