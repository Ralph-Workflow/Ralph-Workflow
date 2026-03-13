/** The 6 built-in Ralph pipeline drain phases. */
export const DRAIN_PHASES = [
  'planning',
  'development',
  'review',
  'fix',
  'commit',
  'analysis',
] as const;

export type DrainPhase = typeof DRAIN_PHASES[number];

/** Display labels for each drain phase. */
export const DRAIN_LABELS: Record<DrainPhase, string> = {
  planning: 'Planning',
  development: 'Development',
  review: 'Review',
  fix: 'Fix',
  commit: 'Commit',
  analysis: 'Analysis',
};

/** Drain tooltip descriptions for contextual help. */
export const DRAIN_DESCRIPTIONS: Record<DrainPhase, string> = {
  planning: 'Creates the implementation plan from your prompt.',
  development: 'Writes and modifies code to implement the plan.',
  review: 'Reviews code changes for quality and correctness.',
  fix: 'Addresses issues found during review.',
  commit: 'Generates commit messages for completed work.',
  analysis: 'Checks code against the plan after each dev iteration. GPT models recommended.',
};

/** Parsed representation of [agent_chains] and [agent_drains] TOML sections. */
export interface AgentChainsConfig {
  /** Map of chain name → ordered list of agent CLI names. */
  chains: Record<string, string[]>;
  /** Map of drain phase name → chain name. */
  drains: Record<string, string>;
}

/** A configured agent (name + CLI tool used in chains). */
export interface ConfiguredAgent {
  /** The agent identifier used in chains (e.g. "claude-code"). */
  name: string;
  /** CLI tool name inferred from the agent name or available tools. */
  tool: string;
  /** Model name if available. */
  model: string;
}

/**
 * An explicit agent definition entry parsed from or serialized to a
 * `[agents.NAME]` TOML section in the config file.
 */
export interface AgentDefinitionEntry {
  /** The agent name — becomes the `[agents.NAME]` key. */
  name: string;
  /** CLI tool binary or name (stored as `tool = "..."` in TOML). */
  tool: string;
  /** Model identifier (stored as `model = "..."` in TOML, omitted if empty). */
  model: string;
}
