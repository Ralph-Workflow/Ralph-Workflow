import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { RouterModule } from '@angular/router';
import { CdkDragDrop, CdkDropList, CdkDrag, CdkDragHandle, CdkDragPlaceholder, moveItemInArray } from '@angular/cdk/drag-drop';
import { MatDialog } from '@angular/material/dialog';
import { ContextualHelpComponent } from '../../../components/contextual-help/contextual-help.component';
import { TauriService } from '../../../services/tauri.service';
import type { AgentToolInfo } from '../../../types';
import {
  parseAgentChainsConfig,
  serializeAgentChainsConfig,
  serializeAgentDefinition,
  removeAgentDefinition,
} from './agent-chains-editor.utils';
import {
  DRAIN_DESCRIPTIONS,
  DRAIN_LABELS,
  DRAIN_PHASES,
  type AgentChainsConfig,
  type ConfiguredAgent,
  type DrainPhase,
} from './agent-chains-editor.types';
import {
  AddAgentDialogComponent,
  type AgentDefinition,
} from '../add-agent-dialog/add-agent-dialog.component';
import type { AgentDefinitionEntry } from './agent-chains-editor.types';

@Component({
  selector: 'app-agent-chains-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  standalone: true,
  imports: [
    RouterModule,
    ContextualHelpComponent,
    CdkDropList,
    CdkDrag,
    CdkDragHandle,
    CdkDragPlaceholder,
  ],
  templateUrl: './agent-chains-editor.component.html',
  styleUrl: './agent-chains-editor.component.css',
})
export class AgentChainsEditorComponent implements OnInit {
  private readonly dialog = inject(MatDialog);
  private readonly tauri = inject(TauriService);

  /** Current raw TOML content (read from the config file). */
  readonly toml = input.required<string>();

  /** Emitted when any chain/drain change is made. Carries the updated TOML. */
  readonly tomlChange = output<string>();

  /** The 6 drain phases, in display order. */
  readonly drainPhases = DRAIN_PHASES;
  readonly drainLabels = DRAIN_LABELS;
  readonly drainDescriptions = DRAIN_DESCRIPTIONS;

  /** Parsed chains and drains from the TOML input. */
  private readonly _config = computed<AgentChainsConfig>(() =>
    parseAgentChainsConfig(this.toml()),
  );

  /** Current editable state (starts from parsed config, updated on user changes). */
  private readonly _pendingConfig = signal<AgentChainsConfig | null>(null);

  /** Active config: pending changes take priority over parsed TOML. */
  private readonly _activeConfig = computed<AgentChainsConfig>(() =>
    this._pendingConfig() ?? this._config(),
  );

  /** Loaded agent tools for cross-referencing agent names with tool/model metadata. */
  private readonly _agentTools = signal<AgentToolInfo[]>([]);

  get chainNames() { return Object.keys(this._activeConfig().chains); }
  get hasChainsConfigured() { return this.chainNames.length > 0; }
  /** Map of chain name → agent list. Use in template as `chainsMap[chainName]`. */
  get chainsMap(): Record<string, string[]> { return this._activeConfig().chains; }
  /** Map of drain phase → bound chain name. Use in template as `drainsMap[drain]`. */
  get drainsMap(): Record<string, string> { return this._activeConfig().drains; }

  /**
   * Derived list of all unique agent names used across all chains,
   * shown in the Configured Agents subsection.
   * Cross-references agent names against installed tools to populate
   * tool and model metadata.
   */
  readonly configuredAgents = computed<ConfiguredAgent[]>(() => {
    const allAgents = new Set<string>();
    for (const agents of Object.values(this._activeConfig().chains)) {
      for (const a of agents) allAgents.add(a);
    }
    const tools = this._agentTools();
    return Array.from(allAgents).map(name => {
      // Match agent name against tool binary or tool name (case-insensitive substring).
      const matchedTool = tools.find(t =>
        name.toLowerCase().includes(t.binary.toLowerCase()) ||
        t.binary.toLowerCase().includes(name.toLowerCase()) ||
        name.toLowerCase().includes(t.name.toLowerCase().replace(/\s+/g, '-')),
      );
      return {
        name,
        tool: matchedTool?.name ?? '',
        model: matchedTool?.available_models[0] ?? '',
      };
    });
  });

  get hasConfiguredAgents() { return this.configuredAgents().length > 0; }
  get configuredAgentsList(): ConfiguredAgent[] { return this.configuredAgents(); }

  ngOnInit(): void {
    void this.tauri.getAgentTools().then(tools => this._agentTools.set(tools)).catch(() => void 0);
  }

  getChainAgents(chainName: string): string[] {
    return this._activeConfig().chains[chainName] ?? [];
  }

  getDrainBinding(drain: DrainPhase): string {
    return this._activeConfig().drains[drain] ?? '';
  }

  setDrainBinding(drain: string, chainName: string): void {
    const current = this._activeConfig();
    const updatedDrains = { ...current.drains };
    if (chainName) {
      updatedDrains[drain] = chainName;
    } else {
      delete updatedDrains[drain];
    }
    const updated: AgentChainsConfig = { ...current, drains: updatedDrains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  onDrainSelectChange(drain: string, event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    this.setDrainBinding(drain, value);
  }

  addChain(name: string): void {
    if (!name.trim()) return;
    const current = this._activeConfig();
    if (current.chains[name]) return; // already exists
    const updatedChains = { ...current.chains, [name]: [] };
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  removeChain(chainName: string): void {
    const current = this._activeConfig();
    const updatedChains = { ...current.chains };
    delete updatedChains[chainName];
    // Remove drain bindings that reference this chain
    const updatedDrains = { ...current.drains };
    for (const drain of Object.keys(updatedDrains)) {
      if (updatedDrains[drain] === chainName) {
        delete updatedDrains[drain];
      }
    }
    const updated: AgentChainsConfig = { chains: updatedChains, drains: updatedDrains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  addAgentToChain(chainName: string, agentName: string): void {
    if (!agentName.trim()) return;
    const current = this._activeConfig();
    const existingAgents = current.chains[chainName] ?? [];
    if (existingAgents.includes(agentName)) return;
    const updatedChains = {
      ...current.chains,
      [chainName]: [...existingAgents, agentName],
    };
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  removeAgentFromChain(chainName: string, agentName: string): void {
    const current = this._activeConfig();
    const updatedAgents = (current.chains[chainName] ?? []).filter(a => a !== agentName);
    const updatedChains = { ...current.chains, [chainName]: updatedAgents };
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  /**
   * Handle drag-drop reorder within a chain.
   * Uses CDK DragDrop's moveItemInArray utility.
   */
  onAgentDrop(chainName: string, event: CdkDragDrop<string[]>): void {
    if (event.previousIndex === event.currentIndex) return;
    const current = this._activeConfig();
    const agents = [...(current.chains[chainName] ?? [])];
    moveItemInArray(agents, event.previousIndex, event.currentIndex);
    const updatedChains = { ...current.chains, [chainName]: agents };
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  // ── Remove agent confirmation ──

  /** Holds the name of the agent pending removal confirmation (null = no confirmation pending). */
  private readonly _pendingRemoveAgentName = signal<string | null>(null);

  /** Public getter for template and test access. */
  get pendingRemoveAgentName(): string | null { return this._pendingRemoveAgentName(); }

  /**
   * Request removal of an agent.
   * If the agent is used in any chain, sets pendingRemoveAgentName (triggers confirmation UI).
   * If the agent is not in any chain, this is a no-op (caller should use removeAgentFromAllChains directly).
   */
  requestRemoveAgent(agentName: string): void {
    const usedInChains = Object.values(this._activeConfig().chains).some(agents =>
      agents.includes(agentName),
    );
    if (usedInChains) {
      this._pendingRemoveAgentName.set(agentName);
    }
    // If not in any chain, nothing to confirm and nothing to remove from chains.
  }

  /** Confirms the pending agent removal and removes from all chains. */
  confirmRemoveAgent(): void {
    const name = this._pendingRemoveAgentName();
    if (name) {
      this.removeAgentFromAllChains(name);
      this._pendingRemoveAgentName.set(null);
    }
  }

  /** Cancels the pending agent removal without making any changes. */
  cancelRemoveAgent(): void {
    this._pendingRemoveAgentName.set(null);
  }

  /**
   * Persists an agent definition to the TOML by adding/replacing the
   * `[agents.NAME]` section and emitting `tomlChange`.
   *
   * Public to allow test access.
   */
  persistAgentDefinition(agent: AgentDefinitionEntry): void {
    const updatedToml = serializeAgentDefinition(agent, this.toml());
    this.tomlChange.emit(updatedToml);
  }

  /**
   * Removes an `[agents.NAME]` section from the TOML and emits `tomlChange`.
   *
   * Public to allow test access.
   */
  removeAgentDefinitionFromToml(agentName: string): void {
    const updatedToml = removeAgentDefinition(agentName, this.toml());
    this.tomlChange.emit(updatedToml);
  }

  /** Open the Add Agent dialog to define a new agent. */
  openAddAgentDialog(): void {
    const existingAgents = this.configuredAgents().map(a => a.name);
    const dialogRef = this.dialog.open(AddAgentDialogComponent, {
      data: { existingNames: existingAgents },
      panelClass: 'ralph-dialog',
    });
    dialogRef.afterClosed().subscribe((result: AgentDefinition | null) => {
      if (result) {
        // Serialize the new agent definition as a [agents.NAME] TOML section
        this.persistAgentDefinition({ name: result.name, tool: result.tool, model: result.model });
      }
    });
  }

  /** Open the Edit Agent dialog for an existing agent. */
  openEditAgentDialog(agentName: string): void {
    const existingAgents = this.configuredAgents()
      .map(a => a.name)
      .filter(n => n !== agentName);
    const dialogRef = this.dialog.open(AddAgentDialogComponent, {
      data: { agentName, existingNames: existingAgents },
      panelClass: 'ralph-dialog',
    });
    dialogRef.afterClosed().subscribe((result: AgentDefinition | null) => {
      if (result && result.name !== agentName) {
        // Rename agent across all chains
        this.renameAgentInAllChains(agentName, result.name);
      }
    });
  }

  /** Remove an agent from all chains AND remove its [agents.NAME] TOML section. */
  removeAgentFromAllChains(agentName: string): void {
    const current = this._activeConfig();
    const updatedChains: Record<string, string[]> = {};
    for (const [chainName, agents] of Object.entries(current.chains)) {
      updatedChains[chainName] = agents.filter(a => a !== agentName);
    }
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    // First serialize the chain update, then remove the [agents.NAME] section
    const tomlWithChainsUpdated = serializeAgentChainsConfig(updated, this.toml());
    const tomlWithAgentRemoved = removeAgentDefinition(agentName, tomlWithChainsUpdated);
    this.tomlChange.emit(tomlWithAgentRemoved);
  }

  private renameAgentInAllChains(oldName: string, newName: string): void {
    const current = this._activeConfig();
    const updatedChains: Record<string, string[]> = {};
    for (const [chainName, agents] of Object.entries(current.chains)) {
      updatedChains[chainName] = agents.map(a => (a === oldName ? newName : a));
    }
    const updated: AgentChainsConfig = { ...current, chains: updatedChains };
    this._pendingConfig.set(updated);
    this.tomlChange.emit(serializeAgentChainsConfig(updated, this.toml()));
  }

  // ── New chain form ──

  private readonly _newChainName = signal('');
  get newChainName() { return this._newChainName(); }

  onNewChainNameInput(event: Event): void {
    this._newChainName.set((event.target as HTMLInputElement).value);
  }

  submitNewChain(): void {
    const name = this._newChainName().trim();
    if (name) {
      this.addChain(name);
      this._newChainName.set('');
    }
  }

  // ── Per-chain new agent forms ──

  private readonly _newAgentInputs = signal<Record<string, string>>({});
  /** Map of chain name → current new-agent input value. Use in template as `newAgentInputsMap[chainName]`. */
  get newAgentInputsMap(): Record<string, string> { return this._newAgentInputs(); }

  getNewAgentInput(chainName: string): string {
    return this._newAgentInputs()[chainName] ?? '';
  }

  onNewAgentInput(chainName: string, event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this._newAgentInputs.update(m => ({ ...m, [chainName]: value }));
  }

  submitNewAgent(chainName: string): void {
    const agentName = (this._newAgentInputs()[chainName] ?? '').trim();
    if (agentName) {
      this.addAgentToChain(chainName, agentName);
      this._newAgentInputs.update(m => ({ ...m, [chainName]: '' }));
    }
  }
}
