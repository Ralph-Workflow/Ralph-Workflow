import { ChangeDetectionStrategy, Component, inject, signal, computed } from '@angular/core';
import { Router } from '@angular/router';
import { TauriService } from '../../services/tauri.service';
import { WorkspaceService } from '../../services/workspace.service';
import type { AgentToolInfo } from '../../types';

const TOTAL_STEPS = 3;

@Component({
  selector: 'app-onboarding',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  templateUrl: './onboarding.component.html',
  styleUrl: './onboarding.component.css',
})
export class OnboardingComponent {
  private readonly tauri = inject(TauriService);
  private readonly workspaceService = inject(WorkspaceService);
  private readonly router = inject(Router);

  readonly totalSteps = TOTAL_STEPS;
  readonly stepNumbers = Array.from({ length: TOTAL_STEPS }, (_, i) => i + 1);

  readonly currentStep = signal<number>(1);
  readonly agentTools = signal<AgentToolInfo[]>([]);
  readonly isLoadingTools = signal<boolean>(false);
  readonly selectedDirectory = signal<string | null>(null);
  readonly isOpeningWorkspace = signal<boolean>(false);
  readonly openWorkspaceError = signal<string | null>(null);

  readonly hasInstalledTool = computed(() => this.agentTools().some(t => t.installed));

  get currentStepValue(): number { return this.currentStep(); }
  get agentToolsList() { return this.agentTools(); }
  get isLoadingToolsValue(): boolean { return this.isLoadingTools(); }
  get hasInstalledToolValue(): boolean { return this.hasInstalledTool(); }
  get selectedDirectoryValue(): string | null { return this.selectedDirectory(); }
  get isOpeningWorkspaceValue(): boolean { return this.isOpeningWorkspace(); }
  get openWorkspaceErrorValue(): string | null { return this.openWorkspaceError(); }

  goToStep(step: number): void {
    this.currentStep.set(step);
    if (step === 2) {
      void this.loadAgentTools();
    }
  }

  next(): void {
    const next = this.currentStep() + 1;
    if (next <= TOTAL_STEPS) {
      this.goToStep(next);
    }
  }

  back(): void {
    const prev = this.currentStep() - 1;
    if (prev >= 1) {
      this.currentStep.set(prev);
    }
  }

  skip(): void {
    void this.router.navigate(['/']);
  }

  async browseDirectory(): Promise<void> {
    try {
      const dir = await this.tauri.openDirectoryDialog();
      if (dir) {
        this.selectedDirectory.set(dir);
        this.openWorkspaceError.set(null);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.openWorkspaceError.set(msg);
    }
  }

  async openWorkspace(): Promise<void> {
    const dir = this.selectedDirectory();
    if (!dir) return;

    this.isOpeningWorkspace.set(true);
    this.openWorkspaceError.set(null);
    try {
      await this.workspaceService.openWorkspace(dir);
      void this.router.navigate(['/']);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.openWorkspaceError.set(msg);
    } finally {
      this.isOpeningWorkspace.set(false);
    }
  }

  private async loadAgentTools(): Promise<void> {
    this.isLoadingTools.set(true);
    try {
      const tools = await this.tauri.getAgentTools();
      this.agentTools.set(tools);
    } catch (err) {
      console.error('Failed to load agent tools:', err);
      this.agentTools.set([]);
    } finally {
      this.isLoadingTools.set(false);
    }
  }
}
