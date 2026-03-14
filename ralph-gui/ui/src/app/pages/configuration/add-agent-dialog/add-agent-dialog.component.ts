import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatRadioModule } from '@angular/material/radio';
import { MatTooltipModule } from '@angular/material/tooltip';
import { TauriService } from '../../../services/tauri.service';
import type { AgentToolInfo } from '../../../types';

interface ProviderInfo {
  name: string;
  isMulti: boolean;
}

const TOOL_PROVIDER_MAP: Record<string, ProviderInfo> = {
  'Claude Code': { name: 'Anthropic', isMulti: false },
  'Codex': { name: 'OpenAI', isMulti: false },
  'OpenCode': { name: '', isMulti: true },
};

/** Data passed into the dialog when opened. */
export interface AddAgentDialogData {
  /** Pre-fill agent name (edit mode). */
  agentName?: string;
  /** List of existing agent names to prevent duplicates. */
  existingNames?: string[];
}

/** The result emitted on successful submit. */
export interface AgentDefinition {
  name: string;
  tool: string;
  model: string;
}

/**
 * Dialog for adding or editing an agent definition.
 *
 * Adaptive UI:
 * - CLI tools: radio buttons (installed tools only)
 * - Model: dropdown (≤15) or searchable combo (>15)
 */
@Component({
  selector: 'app-add-agent-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatDialogModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatRadioModule,
    MatTooltipModule,
  ],
  templateUrl: './add-agent-dialog.component.html',
})
export class AddAgentDialogComponent implements OnInit {
  private readonly tauri = inject(TauriService);
  private readonly dialogRef = inject<MatDialogRef<AddAgentDialogComponent>>(MatDialogRef);
  /** Dialog data (public for template access). */
  readonly data = inject<AddAgentDialogData>(MAT_DIALOG_DATA);

  // ── Form state ──────────────────────────────────────────────────────────────

  readonly agentName = signal(this.data?.agentName ?? '');
  readonly selectedTool = signal<string>('');
  readonly selectedModel = signal<string>('');
  readonly modelSearch = signal('');

  // ── Data state ──────────────────────────────────────────────────────────────

  private readonly _tools = signal<AgentToolInfo[]>([]);
  private readonly _loading = signal(true);

  /** Only tools that are installed. */
  readonly installedTools = computed(() =>
    this._tools().filter(t => t.installed),
  );

  /** Models for the currently selected tool. */
  readonly availableModels = computed(() => {
    const tool = this._tools().find(t => t.name === this.selectedTool());
    return tool?.available_models ?? [];
  });

  /** True when model list is large enough to warrant a search combo. */
  readonly useModelSearch = computed(() => this.availableModels().length > 15);

  /** Filtered models for the search combo. */
  readonly filteredModels = computed(() => {
    const search = this.modelSearch().toLowerCase();
    return search
      ? this.availableModels().filter(m => m.toLowerCase().includes(search))
      : this.availableModels();
  });

  /** Provider info for the currently selected tool. */
  readonly providerInfo = computed<ProviderInfo | null>(() => {
    const toolName = this.selectedTool();
    if (!toolName) return null;
    return TOOL_PROVIDER_MAP[toolName] ?? null;
  });

  /** Whether the selected tool is a multi-provider tool (e.g., OpenCode). */
  readonly isMultiProviderTool = computed(() => {
    const info = this.providerInfo();
    return info?.isMulti ?? false;
  });

  /** Provider name for single-provider tools (read-only display). */
  readonly singleProviderName = computed(() => {
    const info = this.providerInfo();
    if (!info || info.isMulti) return '';
    return info.name;
  });

  /** Available providers for multi-provider tools (OpenCode). */
  readonly availableProviders = computed(() => {
    if (!this.isMultiProviderTool()) return [];
    return [
      { name: 'Anthropic', authStatus: 'configured' },
      { name: 'OpenAI', authStatus: 'configured' },
      { name: 'Google', authStatus: 'not-configured' },
    ];
  });

  readonly selectedProvider = signal<string>('');

  /** Whether the current agent name is a duplicate of an existing name. */
  readonly isDuplicate = computed(() => {
    const names = this.data?.existingNames ?? [];
    const trimmed = this.agentName().trim();
    if (!trimmed) return false;
    // In edit mode (pre-filled name), allow the original name.
    if (this.data?.agentName && trimmed === this.data.agentName) return false;
    return names.includes(trimmed);
  });

  /** Whether the submit button should be disabled. */
  readonly submitDisabled = computed(() => {
    const name = this.agentName().trim();
    return (
      !name ||
      !this.selectedTool() ||
      !this.selectedModel() ||
      this.isDuplicate()
    );
  });

  readonly isLoading = computed(() => this._loading());

  // ── Template-safe getters (no-call-expression lint) ─────────────────────────

  get agentNameValue(): string { return this.agentName(); }
  get selectedToolValue(): string { return this.selectedTool(); }
  get selectedModelValue(): string { return this.selectedModel(); }
  get modelSearchValue(): string { return this.modelSearch(); }
  get isLoadingValue(): boolean { return this.isLoading(); }
  get installedToolsList() { return this.installedTools(); }
  get isDuplicateValue(): boolean { return this.isDuplicate(); }
  get submitDisabledValue(): boolean { return this.submitDisabled(); }
  get useModelSearchValue(): boolean { return this.useModelSearch(); }
  get filteredModelsList(): string[] { return this.filteredModels(); }
  get availableModelsList(): string[] { return this.availableModels(); }
  get isMultiProviderToolValue(): boolean { return this.isMultiProviderTool(); }
  get selectedProviderValue(): string { return this.selectedProvider(); }
  get availableProvidersList() { return this.availableProviders(); }
  get singleProviderNameValue(): string { return this.singleProviderName(); }

  // ── Lifecycle ───────────────────────────────────────────────────────────────

  ngOnInit(): void {
    void this.loadTools();
  }

  private async loadTools(): Promise<void> {
    this._loading.set(true);
    try {
      const tools = await this.tauri.getAgentTools();
      this._tools.set(tools);
      // Auto-select if only one tool is installed.
      const installed = tools.filter(t => t.installed);
      if (installed.length === 1) {
        const firstTool = installed[0];
        if (firstTool) {
          this.selectedTool.set(firstTool.name);
          const firstModel = firstTool.available_models[0];
          if (firstModel !== undefined) {
            this.selectedModel.set(firstModel);
          }
        }
      }
    } finally {
      this._loading.set(false);
    }
  }

  // ── Handlers ────────────────────────────────────────────────────────────────

  onToolChange(toolName: string): void {
    this.selectedTool.set(toolName);
    this.selectedModel.set('');
    this.modelSearch.set('');
    // Auto-select first model.
    const tool = this._tools().find(t => t.name === toolName);
    if (tool?.available_models && tool.available_models.length > 0) {
      const firstModel = tool.available_models[0];
      if (firstModel !== undefined) {
        this.selectedModel.set(firstModel);
      }
    }
  }

  onModelSearch(value: string): void {
    this.modelSearch.set(value);
  }

  onModelSelect(model: string): void {
    this.selectedModel.set(model);
    this.modelSearch.set('');
  }

  submit(): void {
    if (this.submitDisabled()) return;
    const result: AgentDefinition = {
      name: this.agentName().trim(),
      tool: this.selectedTool(),
      model: this.selectedModel(),
    };
    this.dialogRef.close(result);
  }

  cancel(): void {
    this.dialogRef.close(null);
  }
}
