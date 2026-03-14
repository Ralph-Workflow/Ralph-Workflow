import { Component, effect, inject, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import type { AgentToolInfo } from '../../../types';
import { TauriService } from '../../../services/tauri.service';

@Component({
  selector: 'app-agent-tools-inline',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    MatTooltipModule,
    MatChipsModule,
    MatProgressSpinnerModule,
  ],
  templateUrl: './agent-tools-inline.component.html',
  styleUrl: './agent-tools-inline.component.css',
})
export class AgentToolsInlineComponent {
  private readonly tauri = inject(TauriService);

  private readonly _tools = signal<AgentToolInfo[]>([]);
  private readonly _loading = signal(true);
  private readonly _testingTool = signal<string | null>(null);
  private readonly _testResults = signal<Record<string, string>>({});

  readonly tools = this._tools.asReadonly();
  readonly loading = this._loading.asReadonly();
  readonly testingTool = this._testingTool.asReadonly();

  constructor() {
    effect(() => {
      void this.loadTools();
    });
  }

  private async loadTools(): Promise<void> {
    this._loading.set(true);
    try {
      const tools = await this.tauri.getAgentTools();
      this._tools.set(tools);
    } catch (e) {
      console.error('Failed to load agent tools:', e);
    } finally {
      this._loading.set(false);
    }
  }

  async testConnection(toolName: string): Promise<void> {
    this._testingTool.set(toolName);
    try {
      const result = await this.tauri.testAgentToolConnection(toolName);
      this._testResults.update(results => ({
        ...results,
        [toolName]: result,
      }));
    } catch (e) {
      this._testResults.update(results => ({
        ...results,
        [toolName]: `Error: ${e instanceof Error ? e.message : String(e)}`,
      }));
    } finally {
      this._testingTool.set(null);
    }
  }

  async openCliSettings(toolName: string): Promise<void> {
    try {
      await this.tauri.openToolSettings(toolName);
    } catch (e) {
      console.error('Failed to open CLI settings:', e);
    }
  }

  getTestResult(toolName: string): string | null {
    return this._testResults()[toolName] ?? null;
  }
}
