import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { StatusBarComponent } from './status-bar.component';
import { WorkspaceService, type Workspace } from '../../services/workspace.service';
import { NotificationService } from '../../services/notification.service';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { WritableSignal } from '@angular/core';
import { signal } from '@angular/core';

describe('StatusBarComponent', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<StatusBarComponent>>;
  let component: StatusBarComponent;
  let mockWorkspaces: WritableSignal<Workspace[]>;
  let mockActiveWorkspace: WritableSignal<Workspace | null>;
  let mockIsLoading: WritableSignal<boolean>;
  let mockUnreadCount: WritableSignal<number>;
  let mockWorktrees: WritableSignal<WorktreeInfo[]>;
  let mockActiveWorktreePath: WritableSignal<string | null>;
  let togglePanelSpy: ReturnType<typeof vi.fn>;

  const createMockWorkspace = (overrides: Partial<Workspace> = {}): Workspace => ({
    id: 'ws-1',
    label: 'test-repo',
    path: '/path',
    activeWorktree: null,
    runSummary: { running: 0, failed: 0, paused: 0 },
    navigationState: null,
    activeRunCount: 0,
    ...overrides,
  });

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  beforeEach(async () => {
    mockWorkspaces = signal<Workspace[]>([]);
    mockActiveWorkspace = signal<Workspace | null>(null);
    mockIsLoading = signal<boolean>(false);
    mockUnreadCount = signal<number>(0);
    mockWorktrees = signal<WorktreeInfo[]>([]);
    mockActiveWorktreePath = signal<string | null>(null);
    togglePanelSpy = vi.fn();

    const workspaceServiceSpy = {
      workspaces: mockWorkspaces.asReadonly(),
      activeWorkspaceId: signal<string | null>(null),
      activeWorkspace: mockActiveWorkspace,
      isLoading: mockIsLoading,
    };

    const notificationServiceSpy = {
      unreadCount: mockUnreadCount,
      togglePanel: togglePanelSpy,
    };

    const worktreesServiceSpy = {
      worktrees: mockWorktrees,
      activeWorktreePath: mockActiveWorktreePath,
    };

    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        { provide: WorkspaceService, useValue: workspaceServiceSpy },
        { provide: NotificationService, useValue: notificationServiceSpy },
        { provide: WorktreesService, useValue: worktreesServiceSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(StatusBarComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('workspace label (left section)', () => {
    it('should show workspace label from active workspace', () => {
      mockActiveWorkspace.set(createMockWorkspace({ label: 'test-repo' }));
      fixture.detectChanges();
      expect(component.workspaceLabel()).toBe('test-repo');
    });

    it('should show "No workspace" when no active workspace', () => {
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      expect(component.workspaceLabel()).toBe('No workspace');
    });

    it('should render workspace label text in DOM', () => {
      mockActiveWorkspace.set(createMockWorkspace({ label: 'my-project' }));
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const spans = el.querySelectorAll('span');
      const labelSpan = Array.from(spans).find(s => s.textContent?.trim() === 'my-project');
      expect(labelSpan).toBeTruthy();
    });
  });

  describe('run summary (center section)', () => {
    it('should aggregate running counts across all workspaces', () => {
      mockWorkspaces.set([
        createMockWorkspace({ id: 'ws-1', runSummary: { running: 2, failed: 0, paused: 0 } }),
        createMockWorkspace({ id: 'ws-2', runSummary: { running: 1, failed: 0, paused: 0 } }),
      ]);
      fixture.detectChanges();
      expect(component.runSummaryText()).toContain('3 running');
    });

    it('should aggregate paused counts across all workspaces', () => {
      mockWorkspaces.set([
        createMockWorkspace({ id: 'ws-1', runSummary: { running: 0, failed: 0, paused: 1 } }),
        createMockWorkspace({ id: 'ws-2', runSummary: { running: 0, failed: 0, paused: 2 } }),
      ]);
      fixture.detectChanges();
      expect(component.runSummaryText()).toContain('3 paused');
    });

    it('should show "N running, M paused" format', () => {
      mockWorkspaces.set([
        createMockWorkspace({ id: 'ws-1', runSummary: { running: 2, failed: 0, paused: 1 } }),
      ]);
      fixture.detectChanges();
      expect(component.runSummaryText()).toBe('2 running, 1 paused');
    });

    it('should return empty string when all workspaces idle', () => {
      mockWorkspaces.set([
        createMockWorkspace({ id: 'ws-1', runSummary: { running: 0, failed: 0, paused: 0 } }),
      ]);
      fixture.detectChanges();
      expect(component.runSummaryText()).toBe('');
    });

    it('should return empty string when no workspaces', () => {
      mockWorkspaces.set([]);
      fixture.detectChanges();
      expect(component.runSummaryText()).toBe('');
    });

    it('should render run summary text in DOM', () => {
      mockWorkspaces.set([
        createMockWorkspace({ id: 'ws-1', runSummary: { running: 3, failed: 0, paused: 0 } }),
      ]);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const spans = el.querySelectorAll('span');
      const runSpan = Array.from(spans).find(s => s.textContent?.trim() === '3 running');
      expect(runSpan).toBeTruthy();
    });
  });

  describe('notification bell (right section)', () => {
    it('should reflect unreadCount from NotificationService', () => {
      mockUnreadCount.set(3);
      fixture.detectChanges();
      expect(component.currentNotificationCount).toBe(3);
    });

    it('should render bell button in DOM', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[aria-label="Notifications"]')).toBeTruthy();
    });

    it('should call NotificationService.togglePanel when bell is clicked', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const bell = el.querySelector<HTMLElement>('[aria-label="Notifications"]');
      bell?.click();
      expect(togglePanelSpy).toHaveBeenCalled();
    });

    it('should show badge when unreadCount > 0', () => {
      mockUnreadCount.set(5);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const bell = el.querySelector('[aria-label="Notifications"]');
      const badge = bell?.querySelector('span[aria-label]');
      expect(badge).toBeTruthy();
    });

    it('should hide badge when unreadCount is 0', () => {
      mockUnreadCount.set(0);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.notification-badge')).toBeFalsy();
    });
  });

  describe('branch display (left section)', () => {
    it('should show branch of active worktree when set', () => {
      const worktree = createMockWorktree({ path: '/repo/wt-1', branch: 'feature/my-branch' });
      mockWorktrees.set([worktree]);
      mockActiveWorktreePath.set('/repo/wt-1');
      fixture.detectChanges();
      expect(component.currentBranch).toBe('feature/my-branch');
    });

    it('should show main worktree branch when no active worktree path is set', () => {
      const main = createMockWorktree({ path: '/repo', branch: 'main', is_main: true });
      mockWorktrees.set([main]);
      mockActiveWorktreePath.set(null);
      fixture.detectChanges();
      expect(component.currentBranch).toBe('main');
    });

    it('should return empty string when no worktrees exist', () => {
      mockWorktrees.set([]);
      mockActiveWorktreePath.set(null);
      fixture.detectChanges();
      expect(component.currentBranch).toBe('');
    });

    it('should render branch text in DOM', () => {
      const worktree = createMockWorktree({ path: '/repo', branch: 'develop' });
      mockWorktrees.set([worktree]);
      mockActiveWorktreePath.set('/repo');
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const spans = el.querySelectorAll('span');
      const branchSpan = Array.from(spans).find(s => s.textContent?.trim() === 'develop');
      expect(branchSpan).toBeTruthy();
    });
  });

  describe('connection status (right section)', () => {
    it('should show "Connecting" when workspace is loading', () => {
      mockIsLoading.set(true);
      mockWorkspaces.set([]);
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      expect(component.connectionState()).toBe('connecting');
      expect(component.connectionStatus()).toBe('Connecting');
      expect(component.connectionStatusClass()).toBe('status-connecting');
    });

    it('should show "Disconnected" when no workspaces exist', () => {
      mockIsLoading.set(false);
      mockWorkspaces.set([]);
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      expect(component.connectionState()).toBe('disconnected');
      expect(component.connectionStatus()).toBe('Disconnected');
      expect(component.connectionStatusClass()).toBe('status-disconnected');
    });

    it('should show "Disconnected" when workspaces exist but no active workspace', () => {
      mockIsLoading.set(false);
      mockWorkspaces.set([createMockWorkspace({ id: 'ws-1' })]);
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      expect(component.connectionState()).toBe('disconnected');
      expect(component.connectionStatus()).toBe('Disconnected');
      expect(component.connectionStatusClass()).toBe('status-disconnected');
    });

    it('should show "Connected" when active workspace exists', () => {
      mockIsLoading.set(false);
      mockWorkspaces.set([createMockWorkspace({ id: 'ws-1' })]);
      mockActiveWorkspace.set(createMockWorkspace({ id: 'ws-1' }));
      fixture.detectChanges();
      expect(component.connectionState()).toBe('connected');
      expect(component.connectionStatus()).toBe('Connected');
      expect(component.connectionStatusClass()).toBe('status-connected');
    });

    it('should prioritize connecting state over other states', () => {
      mockIsLoading.set(true);
      mockWorkspaces.set([createMockWorkspace({ id: 'ws-1' })]);
      mockActiveWorkspace.set(createMockWorkspace({ id: 'ws-1' }));
      fixture.detectChanges();
      expect(component.connectionState()).toBe('connecting');
    });

    it('should render connection indicator in DOM', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.connection-indicator')).toBeTruthy();
    });

    it('should render connection indicator with role="status"', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const indicator = el.querySelector('.connection-indicator');
      expect(indicator?.getAttribute('role')).toBe('status');
    });

    it('should render connection indicator with aria-label', () => {
      mockIsLoading.set(false);
      mockWorkspaces.set([createMockWorkspace({ id: 'ws-1' })]);
      mockActiveWorkspace.set(createMockWorkspace({ id: 'ws-1' }));
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const indicator = el.querySelector('.connection-indicator');
      expect(indicator?.getAttribute('aria-label')).toBe('Connection status: connected');
    });

    it('should show disconnected aria-label when disconnected', () => {
      mockIsLoading.set(false);
      mockWorkspaces.set([]);
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const indicator = el.querySelector('.connection-indicator');
      expect(indicator?.getAttribute('aria-label')).toBe('Connection status: disconnected');
    });

    it('should show connecting aria-label when connecting', () => {
      mockIsLoading.set(true);
      mockWorkspaces.set([]);
      mockActiveWorkspace.set(null);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const indicator = el.querySelector('.connection-indicator');
      expect(indicator?.getAttribute('aria-label')).toBe('Connection status: connecting');
    });
  });
});
