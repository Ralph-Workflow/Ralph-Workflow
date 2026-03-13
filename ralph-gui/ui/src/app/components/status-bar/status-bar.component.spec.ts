import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection, signal } from '@angular/core';
import { StatusBarComponent } from './status-bar.component';
import { WorkspaceService, Workspace } from '../../services/workspace.service';
import { NotificationService } from '../../services/notification.service';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';

describe('StatusBarComponent', () => {
  let fixture: ReturnType<typeof TestBed.createComponent<StatusBarComponent>>;
  let component: StatusBarComponent;
  let mockWorkspaces: ReturnType<typeof signal<Workspace[]>>;
  let mockActiveWorkspace: ReturnType<typeof signal<Workspace | null>>;
  let mockUnreadCount: ReturnType<typeof signal<number>>;
  let mockWorktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
  let mockActiveWorktreePath: ReturnType<typeof signal<string | null>>;
  let togglePanelSpy: jasmine.Spy;

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
    mockUnreadCount = signal<number>(0);
    mockWorktrees = signal<WorktreeInfo[]>([]);
    mockActiveWorktreePath = signal<string | null>(null);
    togglePanelSpy = jasmine.createSpy('togglePanel');

    const workspaceServiceSpy = {
      workspaces: mockWorkspaces,
      activeWorkspaceId: signal<string | null>(null),
      activeWorkspace: mockActiveWorkspace,
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
      expect(el.querySelector('.workspace-label')?.textContent?.trim()).toBe('my-project');
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
      expect(el.querySelector('.run-summary')?.textContent?.trim()).toBe('3 running');
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
      expect(el.querySelector('.notification-bell')).toBeTruthy();
    });

    it('should call NotificationService.togglePanel when bell is clicked', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const bell = el.querySelector<HTMLElement>('.notification-bell');
      bell?.click();
      expect(togglePanelSpy).toHaveBeenCalled();
    });

    it('should show badge when unreadCount > 0', () => {
      mockUnreadCount.set(5);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.notification-badge')).toBeTruthy();
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
      expect(el.querySelector('.branch-label')?.textContent?.trim()).toBe('develop');
    });
  });

  describe('connection status (right section)', () => {
    it('should expose connectionStatus signal as "Connected"', () => {
      fixture.detectChanges();
      expect(component.connectionStatus()).toBe('Connected');
    });

    it('should expose connectionStatusClass as "status-connected"', () => {
      fixture.detectChanges();
      expect(component.connectionStatusClass()).toBe('status-connected');
    });

    it('should render connection indicator in DOM', () => {
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('.connection-indicator')).toBeTruthy();
    });
  });
});
