import type { AgentChainsConfig, AgentDefinitionEntry } from './agent-chains-editor.types';

/**
 * Parses [agent_chains] and [agent_drains] sections from a TOML string.
 *
 * Uses simple line-by-line parsing — does not require a full TOML parser.
 * Handles the format used by ralph-workflow:
 *   [agent_chains]
 *   mychain = ["agent1", "agent2"]
 *
 *   [agent_drains]
 *   development = "mychain"
 */
export function parseAgentChainsConfig(toml: string): AgentChainsConfig {
  const chains: Record<string, string[]> = {};
  const drains: Record<string, string> = {};

  if (!toml.trim()) return { chains, drains };

  type Section = 'none' | 'agent_chains' | 'agent_drains';
  let currentSection: Section = 'none';

  for (const rawLine of toml.split('\n')) {
    const line = rawLine.trim();

    // Section headers
    if (line === '[agent_chains]') {
      currentSection = 'agent_chains';
      continue;
    }
    if (line === '[agent_drains]') {
      currentSection = 'agent_drains';
      continue;
    }
    // Any other section header resets the current section
    if (line.startsWith('[') && line.endsWith(']')) {
      currentSection = 'none';
      continue;
    }

    // Skip empty lines and comments
    if (!line || line.startsWith('#')) continue;

    // Parse key = value pairs within relevant sections
    const eqIdx = line.indexOf('=');
    if (eqIdx === -1) continue;
    const key = line.slice(0, eqIdx).trim();
    const valueStr = line.slice(eqIdx + 1).trim();

    if (currentSection === 'agent_chains') {
      // Value is an array: ["agent1", "agent2"]
      const agents = parseStringArray(valueStr);
      if (agents !== null) {
        chains[key] = agents;
      }
    } else if (currentSection === 'agent_drains') {
      // Value is a quoted string: "chain_name"
      const chainName = parseQuotedString(valueStr);
      if (chainName !== null) {
        drains[key] = chainName;
      }
    }
  }

  return { chains, drains };
}

/**
 * Serializes an AgentChainsConfig back into TOML, merging with existing TOML content.
 *
 * Replaces [agent_chains] and [agent_drains] sections in the existing TOML with the
 * new values, preserving all other sections (e.g., [defaults], [retry], [git]).
 * If chains/drains are empty, removes those sections entirely.
 */
export function serializeAgentChainsConfig(
  config: AgentChainsConfig,
  existingToml: string,
): string {
  const hasChains = Object.keys(config.chains).length > 0;
  const hasDrains = Object.keys(config.drains).length > 0;

  // Remove existing [agent_chains] and [agent_drains] sections from TOML
  const withoutChainsAndDrains = removeSectionsByName(existingToml, [
    'agent_chains',
    'agent_drains',
  ]);

  const parts: string[] = [withoutChainsAndDrains.trimEnd()];

  if (hasChains) {
    const chainLines = ['', '[agent_chains]'];
    for (const [name, agents] of Object.entries(config.chains)) {
      const agentList = agents.map(a => `"${a}"`).join(', ');
      chainLines.push(`${name} = [${agentList}]`);
    }
    parts.push(chainLines.join('\n'));
  }

  if (hasDrains) {
    const drainLines = ['', '[agent_drains]'];
    for (const [drain, chain] of Object.entries(config.drains)) {
      drainLines.push(`${drain} = "${chain}"`);
    }
    parts.push(drainLines.join('\n'));
  }

  const result = parts.join('\n').trimEnd();
  return result ? result + '\n' : '';
}

/**
 * Parses `[agents.NAME]` sections from a TOML string.
 *
 * Each section is expected to have `tool = "..."` and optionally `model = "..."` fields.
 * Other fields within the agent section are ignored.
 */
export function parseAgentDefinitions(toml: string): AgentDefinitionEntry[] {
  const agents: AgentDefinitionEntry[] = [];
  if (!toml.trim()) return agents;

  let currentAgentName: string | null = null;
  let currentTool = '';
  let currentModel = '';

  const flushAgent = (): void => {
    if (currentAgentName !== null) {
      agents.push({ name: currentAgentName, tool: currentTool, model: currentModel });
    }
  };

  for (const rawLine of toml.split('\n')) {
    const line = rawLine.trim();

    // Check for a section header
    if (line.startsWith('[') && line.endsWith(']')) {
      flushAgent();
      currentAgentName = null;
      currentTool = '';
      currentModel = '';

      // Match [agents.NAME] (but not [agent_chains], [agent_drains], etc.)
      const match = /^\[agents\.([^\]]+)\]$/.exec(line);
      if (match) {
        currentAgentName = match[1]!;
      }
      continue;
    }

    // Skip empty lines, comments, and lines outside an [agents.*] section
    if (!line || line.startsWith('#') || currentAgentName === null) continue;

    const eqIdx = line.indexOf('=');
    if (eqIdx === -1) continue;
    const key = line.slice(0, eqIdx).trim();
    const rawValue = line.slice(eqIdx + 1).trim();
    const value = parseQuotedString(rawValue);

    if (key === 'tool' && value !== null) {
      currentTool = value;
    } else if (key === 'model' && value !== null) {
      currentModel = value;
    }
  }

  flushAgent();
  return agents;
}

/**
 * Adds or replaces an `[agents.NAME]` section in existing TOML.
 *
 * If a section with the same name already exists it is replaced.
 * All other sections are preserved.
 */
export function serializeAgentDefinition(
  agent: AgentDefinitionEntry,
  existingToml: string,
): string {
  // Remove any existing section for this agent name
  const withoutAgent = removeAgentDefinition(agent.name, existingToml);

  // Build the new agent section
  const lines: string[] = [`[agents.${agent.name}]`, `tool = "${agent.tool}"`];
  if (agent.model) {
    lines.push(`model = "${agent.model}"`);
  }

  const trimmed = withoutAgent.trimEnd();
  const separator = trimmed ? '\n\n' : '';
  return `${trimmed}${separator}${lines.join('\n')}\n`;
}

/**
 * Removes an `[agents.NAME]` section from existing TOML.
 *
 * All other content is preserved.
 */
export function removeAgentDefinition(agentName: string, existingToml: string): string {
  const sectionHeader = `[agents.${agentName}]`;
  return removeSectionsByExactHeader(existingToml, [sectionHeader]);
}

/**
 * Removes sections matched by their bare names (e.g. "agent_chains" → removes "[agent_chains]").
 * Used for flat top-level sections like `[agent_chains]` and `[agent_drains]`.
 */
function removeSectionsByName(toml: string, sectionNames: string[]): string {
  const headerPatterns = sectionNames.map(n => `[${n}]`);
  return removeSectionsByExactHeader(toml, headerPatterns);
}

/**
 * Core section-removal helper. Removes all sections whose header exactly matches
 * one of the provided header strings (e.g. `"[agents.my-agent]"`).
 *
 * When a new section header is encountered that is NOT in the removal list,
 * the removal state is reset so subsequent lines are kept.
 */
function removeSectionsByExactHeader(toml: string, exactHeaders: string[]): string {
  const lines = toml.split('\n');
  const output: string[] = [];
  let inRemovedSection = false;

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      inRemovedSection = exactHeaders.includes(trimmed);
    }

    if (!inRemovedSection) {
      output.push(line);
    }
  }

  // Remove trailing blank lines caused by removing sections
  while (output.length > 0 && output[output.length - 1]?.trim() === '') {
    output.pop();
  }

  return output.join('\n');
}

/** Parses a TOML array string like `["a", "b"]` into `["a", "b"]`. Returns null on parse failure. */
function parseStringArray(value: string): string[] | null {
  if (!value.startsWith('[') || !value.endsWith(']')) return null;
  const inner = value.slice(1, -1).trim();
  if (!inner) return [];

  const items: string[] = [];
  for (const part of inner.split(',')) {
    const item = parseQuotedString(part.trim());
    if (item === null) return null;
    items.push(item);
  }
  return items;
}

/** Parses a TOML quoted string like `"value"`. Returns null on parse failure. */
function parseQuotedString(value: string): string | null {
  if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
    return value.slice(1, -1);
  }
  return null;
}
