import { Component, ChangeDetectionStrategy, HostListener, inject, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatDividerModule } from '@angular/material/divider';
import { WorktreesService } from './services/worktrees.service';
import { WorkspaceService } from './services/workspace.service';
import { WorkspaceTabBarComponent } from './components/workspace-tab-bar/workspace-tab-bar.component';
import { StatusBarComponent } from './components/status-bar/status-bar.component';

interface NavItem {
  path: string;
  label: string;
  icon: string;
}

const NAV_ITEMS: NavItem[] = [
  { path: '/', label: 'Home', icon: 'home' },
  { path: '/sessions', label: 'Sessions', icon: 'play_arrow' },
  { path: '/worktrees', label: 'Worktrees', icon: 'account_tree' },
  { path: '/configuration', label: 'Configuration', icon: 'settings' },
];

@Component({
  selector: 'app-root',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterModule,
    MatSidenavModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    MatDividerModule,
    WorkspaceTabBarComponent,
    StatusBarComponent,
  ],
  templateUrl: './app.component.html',
  styleUrls: ['./app.component.css'],
})
export class AppComponent {
  readonly worktreesService = inject(WorktreesService);
  readonly workspaceService = inject(WorkspaceService);
  readonly navItems = NAV_ITEMS;
  readonly showHelp = signal(false);

  private pendingNavKey: string | null = null;
  private keyTimeout: ReturnType<typeof setTimeout> | null = null;

  get contextDisplay(): string {
    const activePath = this.worktreesService.activeWorktreePath();
    if (activePath) {
      const wt = this.worktreesService.worktrees().find(w => w.path === activePath);
      return wt?.name ?? activePath;
    }
    const repoPath = this.worktreesService.lastRepoPath();
    if (repoPath) {
      return repoPath.split('/').pop() ?? repoPath;
    }
    return 'Select repository...';
  }

  isActive(_path: string): boolean {
    return false;
  }

  selectContext(path: string | null): void {
    if (path === null) {
      return;
    }
    void this.worktreesService.switchContext(this.worktreesService.lastRepoPath() ?? '', path);
  }

  closeHelp(): void {
    this.showHelp.set(false);
  }

  @HostListener('window:keydown', ['$event'])
  handleKeyboard(event: KeyboardEvent): void {
    const target = event.target as HTMLElement | null;
    if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) {
      return;
    }

    if (event.key === '?') {
      this.showHelp.update(v => !v);
      event.preventDefault();
      return;
    }

    if (event.key === 'Escape') {
      this.showHelp.set(false);
      return;
    }

    if (event.ctrlKey && event.key === 'Tab') {
      this.cycleWorkspace(event.shiftKey ? -1 : 1);
      event.preventDefault();
      return;
    }

    if (this.pendingNavKey === 'g') {
      this.clearKeyTimeout();
      this.pendingNavKey = null;

      const routes: Record<string, string> = {
        h: '/',
        s: '/sessions',
        w: '/worktrees',
        c: '/configuration',
      };

      const route = routes[event.key];
      if (route) {
        window.location.hash = route;
      }
      event.preventDefault();
      return;
    }

    if (event.key === 'g') {
      this.pendingNavKey = 'g';
      this.keyTimeout = setTimeout(() => {
        this.pendingNavKey = null;
      }, 500);
      event.preventDefault();
    }
  }

  private clearKeyTimeout(): void {
    if (this.keyTimeout) {
      clearTimeout(this.keyTimeout);
      this.keyTimeout = null;
    }
  }

  private cycleWorkspace(direction: number): void {
    const workspaces = this.workspaceService.workspaces();
    if (workspaces.length <= 1) return;

    const activeId = this.workspaceService.activeWorkspaceId();
    const currentIndex = workspaces.findIndex(w => w.id === activeId);
    if (currentIndex === -1) return;

    let nextIndex = currentIndex + direction;
    if (nextIndex < 0) nextIndex = workspaces.length - 1;
    if (nextIndex >= workspaces.length) nextIndex = 0;

    const nextWorkspace = workspaces[nextIndex];
    if (nextWorkspace) {
      this.workspaceService.switchWorkspace(nextWorkspace.id);
    }
  }
}
