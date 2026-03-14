import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * Workspace loading skeleton displayed during workspace switch loading state.
 * Mirrors the final sessions/worktrees list layout to preserve spatial context
 * while the new workspace data loads (prevents blank screen, per wireframe annotation).
 */
@Component({
  selector: 'app-workspace-loading-skeleton',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './workspace-loading-skeleton.component.html',
  styleUrls: ['./workspace-loading-skeleton.component.css'],
})
export class WorkspaceLoadingSkeletonComponent {
  /** Skeleton rows count — mirrors a typical sessions list height. */
  readonly skeletonRows = [1, 2, 3, 4, 5];

  /** Skeleton card count — mirrors the dashboard card layout. */
  readonly skeletonCards = [1, 2, 3];
}
