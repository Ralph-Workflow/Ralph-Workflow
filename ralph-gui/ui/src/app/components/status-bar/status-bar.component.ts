import { Component, inject, ChangeDetectionStrategy, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WorkspaceService } from '../../services/workspace.service';

@Component({
  selector: 'app-status-bar',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  templateUrl: './status-bar.component.html',
  styleUrls: ['./status-bar.component.css'],
})
export class StatusBarComponent {
  readonly workspaceService = inject(WorkspaceService);

  readonly workspaceLabel = computed(() => {
    const ws = this.workspaceService.activeWorkspace();
    return ws?.label ?? 'No workspace';
  });

  readonly runSummaryText = computed(() => {
    const ws = this.workspaceService.activeWorkspace();
    if (!ws) return '';
    const s = ws.runSummary;
    const parts: string[] = [];
    if (s.running > 0) parts.push(`${s.running} running`);
    if (s.paused > 0) parts.push(`${s.paused} paused`);
    if (s.failed > 0) parts.push(`${s.failed} failed`);
    return parts.join(', ');
  });

  readonly connectionStatus = computed(() => 'Connected');
  readonly connectionStatusClass = computed(() => 'status-connected');
}
