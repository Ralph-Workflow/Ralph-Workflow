import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { TauriService } from '../../services/tauri.service';
import type { AgentToolInfo, ToolUpdateInfo } from '../../types';

const HEALTH_LABELS: Record<string, string> = {
  ready: 'Ready to use',
  needs_setup: 'Needs setup',
  not_installed: 'Not installed',
};

interface ToolTestResult {
  ok: boolean;
  message: string;
}

interface ToolTestResults {
  [toolName: string]: ToolTestResult;
}

interface ToolTesting {
  [toolName: string]: boolean;
}

interface ToolRefreshing {
  [toolName: string]: boolean;
}

interface ToolInstalling {
  [toolName: string]: boolean;
}

interface ToolViewModel {
  tool: AgentToolInfo;
  updateInfo: ToolUpdateInfo | undefined;
  isInstalling: boolean;
  isRefreshing: boolean;
  isTesting: boolean;
  testResult: ToolTestResult | undefined;
  modelsDisplay: string;
}

const INSTALL_COMMANDS: Record<string, Record<string, string>> = {
  claude: {
    homebrew: 'brew install claude',
    bun: 'bun install -g @anthropic-ai/claude',
    manual: 'https://docs.anthropic.com/en/docs/claude-cli',
  },
  opencode: {
    homebrew: 'brew install opencode',
    bun: 'bun install -g opencode',
    manual: 'https://github.com/opencode-ai/opencode',
  },
  aider: {
    homebrew: 'brew install aider',
    bun: 'pip install aider-chat',
    manual: 'https://aider.chat/docs/install.html',
  },
};

const DEFAULT_COMMANDS: Record<string, string> = {
  homebrew: 'brew install <tool>',
  bun: 'bun install -g <tool>',
  manual: 'See documentation for installation instructions',
};

@Component({
  selector: 'app-agent-tools',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './agent-tools.component.html',
  styleUrl: './agent-tools.component.css',
})
export class AgentToolsComponent {
  private readonly tauri = inject(TauriService);

  readonly healthLabels = HEALTH_LABELS;

  private readonly _tools = signal<AgentToolInfo[]>([]);
  private readonly _isLoading = signal(true);
  private readonly _testResults = signal<ToolTestResults>({});
  private readonly _testing = signal<ToolTesting>({});
  private readonly _updateInfo = signal<ToolUpdateInfo[]>([]);
  private readonly _checkingUpdates = signal(false);
  private readonly _refreshingModels = signal<ToolRefreshing>({});
  private readonly _installing = signal<ToolInstalling>({});
  private readonly _installDialogTool = signal<string | null>(null);
  private readonly _installMethod = signal<string>('homebrew');
  private readonly _updateDialogTool = signal<ToolUpdateInfo | null>(null);

  readonly toolViewModels = computed<ToolViewModel[]>(() => {
    const tools = this._tools();
    const updateInfo = this._updateInfo();
    const installing = this._installing();
    const refreshing = this._refreshingModels();
    const testing = this._testing();
    const testResults = this._testResults();

    return tools.map((tool) => ({
      tool,
      updateInfo: updateInfo.find((u) => u.name === tool.name),
      isInstalling: installing[tool.name] ?? false,
      isRefreshing: refreshing[tool.name] ?? false,
      isTesting: testing[tool.name] ?? false,
      testResult: testResults[tool.name],
      modelsDisplay: tool.available_models.length > 0 ? tool.available_models.join(', ') : '',
    }));
  });

  readonly installCommandPreview = computed(() => {
    const tool = this._installDialogTool();
    const method = this._installMethod();
    if (!tool) return '';
    const toolCommands = INSTALL_COMMANDS[tool.toLowerCase()];
    if (toolCommands && toolCommands[method]) {
      return toolCommands[method];
    }
    return DEFAULT_COMMANDS[method] || `Install ${tool}`;
  });

  get isLoading(): boolean {
    return this._isLoading();
  }

  get tools(): AgentToolInfo[] {
    return this._tools();
  }

  get testResults(): ToolTestResults {
    return this._testResults();
  }

  get updateInfo(): ToolUpdateInfo[] {
    return this._updateInfo();
  }

  get checkingUpdates(): boolean {
    return this._checkingUpdates();
  }

  get installDialogTool(): string | null {
    return this._installDialogTool();
  }

  get installMethod(): string {
    return this._installMethod();
  }

  get updateDialogTool(): ToolUpdateInfo | null {
    return this._updateDialogTool();
  }

  get toolViewModelsList(): ToolViewModel[] {
    return this.toolViewModels();
  }

  get installCommandPreviewText(): string {
    return this.installCommandPreview();
  }

  getUpdateInfo(name: string): ToolUpdateInfo | undefined {
    return this._updateInfo().find((u) => u.name === name);
  }

  isToolInstalling(name: string): boolean {
    return this._installing()[name] ?? false;
  }

  isToolRefreshingModels(name: string): boolean {
    return this._refreshingModels()[name] ?? false;
  }

  getInstallCommand(toolName: string, method: string): string {
    const toolCommands = INSTALL_COMMANDS[toolName.toLowerCase()];
    if (toolCommands && toolCommands[method]) {
      return toolCommands[method];
    }
    return DEFAULT_COMMANDS[method] || `Install ${toolName}`;
  }

  constructor() {
    void this.loadTools();
  }

  async loadTools(): Promise<void> {
    try {
      this._isLoading.set(true);
      const tools = await this.tauri.getAgentTools();
      this._tools.set(tools);
    } catch (error) {
      console.error('Failed to load agent tools:', error);
      this._tools.set([]);
    } finally {
      this._isLoading.set(false);
    }
  }

  async refresh(): Promise<void> {
    await this.loadTools();
  }

  async testConnection(name: string): Promise<void> {
    this._testing.update((t) => ({ ...t, [name]: true }));
    this._testResults.update((r) => ({ ...r, [name]: undefined as unknown as ToolTestResult }));

    try {
      const result = await this.tauri.testAgentToolConnection(name);
      this._testResults.update((r) => ({
        ...r,
        [name]: { ok: true, message: result || 'Connection successful' },
      }));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this._testResults.update((r) => ({
        ...r,
        [name]: { ok: false, message: `Connection failed: ${message}` },
      }));
    } finally {
      this._testing.update((t) => ({ ...t, [name]: false }));
    }
  }

  async checkForUpdates(): Promise<void> {
    this._checkingUpdates.set(true);

    try {
      const updates = await this.tauri.checkToolUpdates();
      this._updateInfo.set(updates);

      const firstUpdate = updates.find((u) => u.update_available);
      if (firstUpdate) {
        this._updateDialogTool.set(firstUpdate);
      }
    } catch (error) {
      console.error('Failed to check for updates:', error);
    } finally {
      this._checkingUpdates.set(false);
    }
  }

  async openToolSettings(name: string): Promise<void> {
    try {
      await this.tauri.openToolSettings(name);
    } catch (error) {
      console.error('Failed to open tool settings:', error);
    }
  }

  async refreshModels(name: string): Promise<void> {
    this._refreshingModels.update((r) => ({ ...r, [name]: true }));

    try {
      const models = await this.tauri.refreshToolModels(name);
      this._tools.update((tools) =>
        tools.map((t) => (t.name === name ? { ...t, available_models: models } : t)),
      );
    } catch (error) {
      console.error('Failed to refresh models:', error);
    } finally {
      this._refreshingModels.update((r) => ({ ...r, [name]: false }));
    }
  }

  async installTool(name: string): Promise<void> {
    this._installing.update((i) => ({ ...i, [name]: true }));
    this._installDialogTool.set(null);
    this._updateDialogTool.set(null);

    try {
      await this.tauri.installAgentTool(name);
      await this.loadTools();
    } catch (error) {
      console.error('Failed to install tool:', error);
    } finally {
      this._installing.update((i) => ({ ...i, [name]: false }));
    }
  }

  openInstallDialog(name: string): void {
    this._installDialogTool.set(name);
  }

  closeInstallDialog(): void {
    this._installDialogTool.set(null);
  }

  setInstallMethod(method: string): void {
    this._installMethod.set(method);
  }

  openUpdateDialog(info: ToolUpdateInfo): void {
    this._updateDialogTool.set(info);
  }

  closeUpdateDialog(): void {
    this._updateDialogTool.set(null);
  }
}
