import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { TauriService } from '../../services/tauri.service';
import type { AgentToolInfo } from '../../types';

const HEALTH_LABELS: Record<string, string> = {
  ready: 'Ready',
  needs_setup: 'Needs setup',
  not_installed: 'Not installed',
};

interface ToolTestResult { ok: boolean; message: string; }
type ToolTestResults = Record<string, ToolTestResult>;
type ToolTesting = Record<string, boolean>;

@Component({
  selector: 'app-agent-tools',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './agent-tools.component.html',
  styleUrl: './agent-tools.component.css',
})
export class AgentToolsComponent {
  private readonly tauri = inject(TauriService);

  /** Human-readable health label lookup (used directly in template). */
  readonly healthLabels: Record<string, string> = HEALTH_LABELS;

  /** All known agent tools with their current status. */
  private readonly _tools = signal<AgentToolInfo[]>([]);

  /** True while the initial load is in progress. */
  private readonly _isLoading = signal<boolean>(true);

  /**
   * Per-tool connection test results, keyed by tool name.
   * Contains ok flag and message.
   */
  private readonly _testResults = signal<ToolTestResults>({});

  /** Per-tool testing flag, keyed by tool name (true = in progress). */
  private readonly _testing = signal<ToolTesting>({});

  get tools() { return this._tools(); }
  get isLoading() { return this._isLoading(); }
  get testResults() { return this._testResults(); }
  get testing() { return this._testing(); }

  constructor() {
    void this.loadTools();
  }

  /** Run a connectivity test for the given tool and store the result. */
  async testConnection(toolName: string): Promise<void> {
    this._testing.update(s => ({ ...s, [toolName]: true }));
    this._testResults.update(m => {
      const copy = { ...m };
      delete copy[toolName];
      return copy;
    });

    try {
      const result = await this.tauri.testAgentToolConnection(toolName);
      this._testResults.update(m => ({ ...m, [toolName]: { ok: true, message: result } }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this._testResults.update(m => ({ ...m, [toolName]: { ok: false, message: `Error: ${msg}` } }));
    } finally {
      this._testing.update(s => {
        const copy = { ...s };
        delete copy[toolName];
        return copy;
      });
    }
  }

  /** Reload the tool list from the backend. */
  async refresh(): Promise<void> {
    await this.loadTools();
  }

  private async loadTools(): Promise<void> {
    this._isLoading.set(true);
    try {
      const tools = await this.tauri.getAgentTools();
      this._tools.set(tools);
    } catch (err) {
      console.warn('Failed to load agent tools:', err);
      this._tools.set([]);
    } finally {
      this._isLoading.set(false);
    }
  }
}
