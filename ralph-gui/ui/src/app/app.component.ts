import { Component, ChangeDetectionStrategy, HostListener, inject, signal } from '@angular/core';
import { RouterModule } from '@angular/router';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatDividerModule } from '@angular/material/divider';
import { WorktreesService } from './services/worktrees.service';

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
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterModule, MatSidenavModule, MatListModule, MatIconModule, MatButtonModule, MatMenuModule, MatDividerModule],
  template: `
    <div class="app-container">
      <!-- Sidebar -->
      <aside class="sidebar">
        <!-- Amber top accent stripe -->
        <div class="sidebar-accent"></div>

        <!-- Logo / Brand -->
        <div class="brand">
          <div class="brand-row">
            <div class="brand-icon">R</div>
            <span class="brand-name">Ralph</span>
          </div>
          <div class="brand-subtitle">workflow</div>
        </div>

        <!-- Context Switcher -->
        <div class="context-section">
          <button mat-button class="context-btn" [matMenuTriggerFor]="contextMenu">
            <mat-icon class="context-icon">folder</mat-icon>
            <span class="context-text">{{ contextDisplay }}</span>
            <mat-icon class="context-arrow">arrow_drop_down</mat-icon>
          </button>
          <mat-menu #contextMenu="matMenu">
            <button mat-menu-item (click)="selectContext(null)">
              <mat-icon>folder_open</mat-icon>
              <span>Select repository...</span>
            </button>
            @if (worktreesService.worktrees().length > 0) {
              <mat-divider></mat-divider>
              @for (wt of worktreesService.worktrees(); track wt.path) {
                <button mat-menu-item (click)="selectContext(wt.path)">
                  <mat-icon>{{ wt.is_main ? 'folder_special' : 'folder' }}</mat-icon>
                  <span>{{ wt.name }}</span>
                </button>
              }
            }
          </mat-menu>
        </div>

        <div class="sidebar-divider"></div>

        <!-- Navigation -->
        <nav class="nav-section">
          @for (item of navItems; track item.path) {
            <a
              [routerLink]="item.path"
              routerLinkActive="active"
              [routerLinkActiveOptions]="{exact: item.path === '/'}"
              class="nav-item"
            >
              @if (isActive(item.path)) {
                <span class="nav-accent"></span>
              }
              <mat-icon [class.active-icon]="isActive(item.path)">{{ item.icon }}</mat-icon>
              <span>{{ item.label }}</span>
            </a>
          }
        </nav>

        <!-- Footer -->
        <div class="sidebar-footer">v0.1.0</div>
      </aside>

      <!-- Main content -->
      <main class="main-content">
        <router-outlet></router-outlet>
      </main>
    </div>

    <!-- Keyboard shortcuts help modal -->
    @if (showHelp()) {
      <div class="modal-overlay" (click)="closeHelp()">
        <div class="modal-content" (click)="$event.stopPropagation()">
          <div class="modal-header">
            <h2>Keyboard Shortcuts</h2>
            <button mat-icon-button (click)="closeHelp()">
              <mat-icon>close</mat-icon>
            </button>
          </div>
          <div class="modal-body">
            <div class="shortcut-row">
              <kbd>?</kbd>
              <span>Show this help</span>
            </div>
            <div class="shortcut-row">
              <kbd>g</kbd> then <kbd>h</kbd>
              <span>Go to Home</span>
            </div>
            <div class="shortcut-row">
              <kbd>g</kbd> then <kbd>s</kbd>
              <span>Go to Sessions</span>
            </div>
            <div class="shortcut-row">
              <kbd>g</kbd> then <kbd>w</kbd>
              <span>Go to Worktrees</span>
            </div>
            <div class="shortcut-row">
              <kbd>g</kbd> then <kbd>c</kbd>
              <span>Go to Configuration</span>
            </div>
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    :host {
      display: block;
      height: 100vh;
      width: 100vw;
      overflow: hidden;
    }

    .app-container {
      display: flex;
      height: 100%;
      width: 100%;
      background: var(--bg-base);
    }

    .sidebar {
      width: var(--sidebar-width, 220px);
      min-width: var(--sidebar-width, 220px);
      height: 100%;
      background: var(--sidebar-bg, #1a1a1e);
      border-right: 1px solid var(--border-subtle, #2a2a30);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      position: relative;
    }

    .sidebar-accent {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      background: linear-gradient(90deg, var(--accent, #f59e0b) 0%, transparent 100%);
      opacity: 0.7;
      pointer-events: none;
    }

    .brand {
      padding: 22px 16px 16px;
      border-bottom: 1px solid var(--border-subtle, #2a2a30);
    }

    .brand-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 2px;
    }

    .brand-icon {
      width: 26px;
      height: 26px;
      background: var(--accent, #f59e0b);
      border-radius: var(--radius-sm, 4px);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      font-weight: 700;
      color: #000;
      flex-shrink: 0;
      font-family: var(--font-mono, monospace);
      box-shadow: 0 0 10px var(--accent-glow, rgba(245, 158, 11, 0.3));
    }

    .brand-name {
      font-family: var(--font-display, system-ui);
      font-size: 15px;
      font-weight: 600;
      color: var(--text-primary, #fff);
      letter-spacing: -0.02em;
    }

    .brand-subtitle {
      font-family: var(--font-mono, monospace);
      font-size: 10px;
      color: var(--text-muted, #888);
      padding-left: 34px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .context-section {
      padding: 12px 8px 8px;
    }

    .context-btn {
      width: 100%;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: rgba(255, 255, 255, 0.03);
      border-radius: var(--radius-md, 6px);
      color: var(--text-secondary, #aaa);
      font-size: 12px;
      text-align: left;
    }

    .context-icon {
      font-size: 18px;
      width: 18px;
      height: 18px;
      color: var(--text-muted, #666);
    }

    .context-text {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .context-arrow {
      font-size: 18px;
      width: 18px;
      height: 18px;
    }

    .sidebar-divider {
      height: 1px;
      background: var(--border-subtle, #2a2a30);
      margin: 0 8px;
    }

    .nav-section {
      flex: 1;
      padding: 8px 0;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 7px 10px 7px 8px;
      margin: 0 8px;
      border-radius: var(--radius-md, 6px);
      font-size: 13px;
      font-weight: 400;
      color: var(--text-secondary, #aaa);
      text-decoration: none;
      transition: all var(--transition-fast, 150ms);
      cursor: pointer;
      position: relative;
      overflow: hidden;
      border: 1px solid transparent;
    }

    .nav-item:hover {
      color: var(--text-primary, #fff);
      background: rgba(255, 255, 255, 0.03);
    }

    .nav-item.active {
      font-weight: 500;
      color: var(--text-primary, #fff);
      background: var(--bg-elevated, #252530);
      border: 1px solid var(--border-default, #3a3a40);
    }

    .nav-accent {
      position: absolute;
      left: 0;
      top: 20%;
      bottom: 20%;
      width: 2px;
      background: var(--accent, #f59e0b);
      border-radius: 2px;
    }

    .nav-item mat-icon {
      font-size: 18px;
      width: 18px;
      height: 18px;
      text-align: center;
      color: var(--text-muted, #666);
      flex-shrink: 0;
    }

    .nav-item mat-icon.active-icon {
      color: var(--accent, #f59e0b);
    }

    .sidebar-footer {
      padding: 12px 16px;
      border-top: 1px solid var(--border-subtle, #2a2a30);
      font-size: 10px;
      color: var(--text-muted, #666);
      font-family: var(--font-mono, monospace);
      letter-spacing: 0.04em;
    }

    .main-content {
      flex: 1;
      height: 100%;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    /* Modal styles */
    .modal-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }

    .modal-content {
      background: var(--bg-surface, #1e1e22);
      border-radius: var(--radius-lg, 8px);
      border: 1px solid var(--border-default, #3a3a40);
      max-width: 400px;
      width: 90%;
    }

    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 20px;
      border-bottom: 1px solid var(--border-subtle, #2a2a30);
    }

    .modal-header h2 {
      font-family: var(--font-display, system-ui);
      font-size: 16px;
      font-weight: 600;
      color: var(--text-primary, #fff);
      margin: 0;
    }

    .modal-body {
      padding: 16px 20px;
    }

    .shortcut-row {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 8px 0;
    }

    .shortcut-row kbd {
      background: var(--bg-elevated, #252530);
      border: 1px solid var(--border-default, #3a3a40);
      border-radius: var(--radius-sm, 4px);
      padding: 2px 8px;
      font-family: var(--font-mono, monospace);
      font-size: 11px;
      color: var(--text-secondary, #aaa);
    }

    .shortcut-row span {
      color: var(--text-secondary, #aaa);
      font-size: 13px;
    }
  `],
})
export class AppComponent {
  readonly worktreesService = inject(WorktreesService);
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
    // This will be handled by routerLinkActive
    return false;
  }

  selectContext(path: string | null): void {
    if (path === null) {
      // Open repo selector dialog
      return;
    }
    void this.worktreesService.switchContext(this.worktreesService.lastRepoPath() ?? '', path);
  }

  closeHelp(): void {
    this.showHelp.set(false);
  }

  @HostListener('window:keydown', ['$event'])
  handleKeyboard(event: KeyboardEvent): void {
    // Ignore if in input/textarea
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

    // 'g' chord navigation
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
}
