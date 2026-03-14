import {
  Component,
  input,
  output,
  computed,
  ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { EffectiveChainsConfig } from '../../types';

export interface PreflightCheck {
  label: string;
  status: 'ok' | 'warning' | 'error';
  detail?: string;
  action?: 'reviewPrompt' | 'goToConfig';
}

export interface SessionRole {
  phase: string;
  chainName: string;
  agents: string;
}

export interface ConfigPreviewRow {
  label: string;
  value: string;
  source?: string;
}

export interface ResourceEstimate {
  level: 'low' | 'medium' | 'high';
  planningPasses: number;
  devIterations: number;
  reviewPasses: number;
  summary: string;
}

@Component({
  selector: 'app-preflight-summary',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './preflight-summary.component.html',
})
export class PreflightSummaryComponent {
  readonly repoPath = input('');
  readonly worktreePath = input<string | null>(null);
  readonly promptPath = input('');
  readonly promptContent = input('');
  readonly developerIterations = input(5);
  readonly reviewerPasses = input(2);
  readonly reviewDepth = input<string>('standard');
  readonly drainBindings = input<Record<string, string>>({});
  readonly checkpointEnabled = input(false);
  readonly isolationMode = input(false);
  readonly developerContext = input<string>('normal');
  readonly reviewerContext = input<string>('normal');
  readonly effectiveChainsConfig = input<EffectiveChainsConfig | null>(null);
  readonly configSourceLabel = input<string>('Default');
  readonly launchError = input<string | null>(null);
  readonly isLaunching = input(false);

  readonly confirmLaunch = output<void>();
  readonly goBack = output<void>();
  readonly editPrompt = output<void>();
  readonly editConfig = output<void>();
  readonly goToConfiguration = output<void>();

  readonly contextRows = computed<Array<{ label: string; value: string }>>(() => [
    { label: 'Repository', value: this.repoPath() },
    { label: 'Context', value: this.worktreePath() ?? 'Direct repository' },
  ]);

  readonly worktreeName = computed(() => {
    const path = this.worktreePath();
    if (!path) return 'Direct repository';
    const parts = path.split('/');
    return parts[parts.length - 1] || path;
  });

  readonly reviewDepthLabel = computed(() => {
    const depth = this.reviewDepth();
    const labels: Record<string, string> = {
      standard: 'Standard',
      comprehensive: 'Comprehensive',
      security: 'Security',
      incremental: 'Incremental',
    };
    return labels[depth] || depth;
  });

  readonly sessionRoles = computed<SessionRole[]>(() => {
    const config = this.effectiveChainsConfig();
    const bindings = this.drainBindings();

    if (!config) return [];

    const phases = ['planning', 'development', 'review'] as const;

    return phases.map((phase) => {
      const chainName = bindings[phase] || '';
      const chain = config.chains.find((c) => c.name === chainName);
      const agents = chain && chain.agents.length > 0
        ? chain.agents.join(' → ')
        : chainName || '—';

      return {
        phase: phase.charAt(0).toUpperCase() + phase.slice(1),
        chainName,
        agents,
      };
    });
  });

  readonly configPreviewRows = computed<ConfigPreviewRow[]>(() => {
    const rows: ConfigPreviewRow[] = [];

    rows.push({
      label: 'review_depth',
      value: this.reviewDepth(),
    });

    rows.push({
      label: 'checkpoint',
      value: this.checkpointEnabled() ? 'on' : 'off',
    });

    rows.push({
      label: 'isolation',
      value: this.isolationMode() ? 'on' : 'off',
    });

    const bindings = this.drainBindings();
    const bindingParts: string[] = [];
    const phases = ['planning', 'development', 'analysis', 'review', 'fix', 'commit'] as const;
    for (const phase of phases) {
      if (bindings[phase]) {
        bindingParts.push(`${phase}=${bindings[phase]}`);
      }
    }

    if (bindingParts.length > 0) {
      rows.push({
        label: 'drains',
        value: bindingParts.join(' · '),
      });
    }

    return rows;
  });

  readonly preflightChecks = computed<PreflightCheck[]>(() => {
    const checks: PreflightCheck[] = [];

    if (this.repoPath().trim()) {
      checks.push({ label: 'Workspace available', status: 'ok' });
    } else {
      checks.push({
        label: 'Workspace available',
        status: 'error',
        detail: 'Select a workspace',
      });
    }

    const config = this.effectiveChainsConfig();
    if (config && config.has_configured_chains) {
      checks.push({ label: 'Agent chain configured', status: 'ok' });
    } else {
      checks.push({
        label: 'Agent chain configured',
        status: 'error',
        detail: 'No agent chains configured',
        action: 'goToConfig',
      });
    }

    const prompt = this.promptContent();
    if (prompt.length > 20) {
      checks.push({ label: 'Prompt content', status: 'ok' });
    } else if (prompt.length > 0) {
      checks.push({
        label: 'Prompt content',
        status: 'warning',
        detail: 'Add more detail to your prompt',
        action: 'reviewPrompt',
      });
    } else {
      checks.push({
        label: 'Prompt content',
        status: 'warning',
        detail: 'Prompt is empty',
        action: 'reviewPrompt',
      });
    }

    return checks;
  });

  readonly canLaunch = computed(() => {
    return !this.preflightChecks().some((c) => c.status === 'error');
  });

  readonly resourceEstimate = computed<ResourceEstimate>(() => {
    const iters = this.developerIterations();
    const reviews = this.reviewerPasses();

    let level: 'low' | 'medium' | 'high';
    if (iters <= 2) {
      level = 'low';
    } else if (iters <= 5) {
      level = 'medium';
    } else {
      level = 'high';
    }

    const summary = `${level} · 1 planning pass · up to ${iters} dev iterations`;

    return {
      level,
      planningPasses: 1,
      devIterations: iters,
      reviewPasses: reviews,
      summary,
    };
  });

  readonly hasErrors = computed(() => {
    return this.preflightChecks().some((c) => c.status === 'error');
  });

  readonly hasWarnings = computed(() => {
    return this.preflightChecks().some((c) => c.status === 'warning');
  });
}
