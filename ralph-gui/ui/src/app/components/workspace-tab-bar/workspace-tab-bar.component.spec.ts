import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { WorkspaceTabBarComponent } from './workspace-tab-bar.component';
import { WorkspaceService } from '../../services/workspace.service';
import { NotificationService, NOTIFICATION_LISTEN_TOKEN } from '../../services/notification.service';
import { TauriService, TAURI_INVOKE } from '../../services/tauri.service';
import { signal, type Signal } from '@angular/core';
import type { Workspace } from '../../services/workspace.service';

describe('WorkspaceTabBarComponent', () => {
  let component: WorkspaceTabBarComponent;
  let fixture: ComponentFixture<WorkspaceTabBarComponent>;
  let mockWorkspaceService: {
    openWorkspace: ReturnType<typeof vi.fn>;
    closeWorkspace: ReturnType<typeof vi.fn>;
    switchWorkspace: ReturnType<typeof vi.fn>;
    reorderWorkspaces: ReturnType<typeof vi.fn>;
    workspaces: ReturnType<typeof signal<Workspace[]>>;
    activeWorkspaceId: ReturnType<typeof signal<string | null>>;
    activeWorkspace: ReturnType<typeof signal<Workspace | null>>;
    isLoading: ReturnType<typeof signal<boolean>>;
  };
  let mockTauriService: { openDirectoryDialog: ReturnType<typeof vi.fn> };
  let mockNotificationService: {
    add: ReturnType<typeof vi.fn>;
    togglePanel: ReturnType<typeof vi.fn>;
    closePanel: ReturnType<typeof vi.fn>;
    isPanelOpen: Signal<boolean>;
    unreadCount: () => number;
    notifications: Signal<unknown[]>;
  };

  const createMockWorkspace = (overrides: Partial<Workspace> = {}): Workspace => ({
    id: `ws-${Math.random().toString(36).substr(2, 9)}`,
    path: '/path/to/repo',
    label: 'repo',
    activeWorktree: null,
    runSummary: { running: 0, failed: 0, paused: 0 },
    navigationState: null,
    activeRunCount: 0,
    ...overrides,
  });

  beforeEach(async () => {
    mockWorkspaceService = {
      openWorkspace: vi.fn().mockReturnValue(Promise.resolve(createMockWorkspace())),
      closeWorkspace: vi.fn().mockReturnValue(Promise.resolve()),
      switchWorkspace: vi.fn(),
      reorderWorkspaces: vi.fn().mockReturnValue(Promise.resolve()),
      workspaces: signal<Workspace[]>([]),
      activeWorkspaceId: signal<string | null>(null),
      activeWorkspace: signal<Workspace | null>(null),
      isLoading: signal<boolean>(false),
    };

    mockTauriService = {
      openDirectoryDialog: vi.fn().mockReturnValue(Promise.resolve(null)),
    };

    mockNotificationService = {
      add: vi.fn(),
      togglePanel: vi.fn(),
      closePanel: vi.fn(),
      isPanelOpen: signal(false).asReadonly(),
      unreadCount: () => 1,
      notifications: signal([]).asReadonly(),
    };

    const mockInvoke = vi.fn().mockReturnValue(Promise.resolve([]));

    await TestBed.configureTestingModule({
      imports: [WorkspaceTabBarComponent],
      providers: [
        { provide: WorkspaceService, useValue: mockWorkspaceService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: NotificationService, useValue: mockNotificationService },
        { provide: TAURI_INVOKE, useValue: mockInvoke },
        {
          provide: NOTIFICATION_LISTEN_TOKEN,
          useValue: vi.fn().mockReturnValue(Promise.resolve(vi.fn())),
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(WorkspaceTabBarComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('tabs rendering', () => {
    it('should render tabs from workspaces signal', () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      const ws2 = createMockWorkspace({ id: 'ws-2', label: 'project-beta' });
      mockWorkspaceService.workspaces.set([ws1, ws2]);

      fixture.detectChanges();

      const tabs = fixture.nativeElement.querySelectorAll('.tab');
      expect(tabs.length).toBe(2);
      expect(tabs[0].textContent).toContain('project-alpha');
      expect(tabs[1].textContent).toContain('project-beta');
    });

    it('should show active tab with active class', () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      const ws2 = createMockWorkspace({ id: 'ws-2', label: 'project-beta' });
      mockWorkspaceService.workspaces.set([ws1, ws2]);
      mockWorkspaceService.activeWorkspaceId.set('ws-1');

      fixture.detectChanges();

      const tabs = fixture.nativeElement.querySelectorAll('.tab');
      expect(tabs[0].classList).toContain('active');
      expect(tabs[1].classList).not.toContain('active');
    });

    it('should show run count badge only when running > 0', () => {
      const ws1 = createMockWorkspace({
        id: 'ws-1',
        label: 'project-alpha',
        runSummary: { running: 3, failed: 0, paused: 0 },
      });
      const ws2 = createMockWorkspace({
        id: 'ws-2',
        label: 'project-beta',
        runSummary: { running: 0, failed: 0, paused: 0 },
      });
      mockWorkspaceService.workspaces.set([ws1, ws2]);

      fixture.detectChanges();

      const badges = fixture.nativeElement.querySelectorAll('.tab-badge');
      expect(badges.length).toBe(1);
      expect(badges[0].textContent).toContain('3');
    });

    it('should show empty state when no workspaces', () => {
      mockWorkspaceService.workspaces.set([]);

      fixture.detectChanges();

      const emptyState = fixture.nativeElement.querySelector('.empty-state');
      expect(emptyState).toBeTruthy();
      expect(emptyState.textContent).toContain('No workspaces open');
    });

    it('should show plus button', () => {
      fixture.detectChanges();

      // Plus button is identified by aria-label after Tailwind CSS conversion.
      const plusButton = fixture.nativeElement.querySelector('[aria-label="Add workspace"]');
      expect(plusButton).toBeTruthy();
    });
  });

  describe('interactions', () => {
    it('should call switchWorkspace when tab clicked', () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      const tab = fixture.nativeElement.querySelector('.tab');
      tab.click();

      expect(mockWorkspaceService.switchWorkspace).toHaveBeenCalledWith('ws-1');
    });

    it('should call closeWorkspace when close button clicked', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      // Close button identified by aria-label after Tailwind CSS conversion.
      const closeBtn = fixture.nativeElement.querySelector('[aria-label="Close workspace"]');
      closeBtn.click();

      await fixture.whenStable();
      // closeWorkspace is called with (id, force=false) — default non-forced close.
      expect(mockWorkspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1', false);
    });

    it('should close on middle-click (button === 1)', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      const tab = fixture.nativeElement.querySelector('.tab');
      const mouseupEvent = new MouseEvent('mouseup', { button: 1 });
      tab.dispatchEvent(mouseupEvent);

      await fixture.whenStable();
      // Middle-click close also calls with (id, force=false).
      expect(mockWorkspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1', false);
    });

    it('should not close on left-click mouseup (button === 0)', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      const tab = fixture.nativeElement.querySelector('.tab');
      const mouseupEvent = new MouseEvent('mouseup', { button: 0 });
      tab.dispatchEvent(mouseupEvent);

      await fixture.whenStable();
      expect(mockWorkspaceService.closeWorkspace).not.toHaveBeenCalled();
    });

    it('should call openDirectoryDialog when plus button clicked', async () => {
      fixture.detectChanges();

      // Plus button identified by aria-label after Tailwind CSS conversion.
      const plusBtn = fixture.nativeElement.querySelector('[aria-label="Add workspace"]');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockTauriService.openDirectoryDialog).toHaveBeenCalled();
    });

    it('should open workspace after dialog returns path', async () => {
      mockTauriService.openDirectoryDialog.mockReturnValue(Promise.resolve('/new/workspace'));

      fixture.detectChanges();

      const plusBtn = fixture.nativeElement.querySelector('[aria-label="Add workspace"]');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockWorkspaceService.openWorkspace).toHaveBeenCalledWith('/new/workspace');
    });

    it('should not open workspace if dialog cancelled', async () => {
      mockTauriService.openDirectoryDialog.mockReturnValue(Promise.resolve(null));

      fixture.detectChanges();

      const plusBtn = fixture.nativeElement.querySelector('[aria-label="Add workspace"]');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockWorkspaceService.openWorkspace).not.toHaveBeenCalled();
    });
  });

  describe('workspace tab tooltips', () => {
    it('should show full path in title attribute of each tab', () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'alpha', path: '/projects/alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      // Query by [title] attribute for robustness — survives future CSS class changes
      const tab = fixture.nativeElement.querySelector('[title="/projects/alpha"]') as HTMLElement;
      expect(tab).toBeTruthy();
      expect(tab.getAttribute('title')).toBe('/projects/alpha');
    });
  });

  describe('close with active runs', () => {
    it('should show confirmation dialog (not window.confirm) when closing workspace with active runs', async () => {
      const ws1 = createMockWorkspace({
        id: 'ws-1',
        label: 'busy-repo',
        runSummary: { running: 2, failed: 0, paused: 0 },
      });
      mockWorkspaceService.workspaces.set([ws1]);
      fixture.detectChanges();

      // Close button identified by aria-label after Tailwind CSS conversion.
      const closeBtn = fixture.nativeElement.querySelector('[aria-label="Close workspace"]') as HTMLButtonElement;
      closeBtn.click();
      await fixture.whenStable();
      fixture.detectChanges();

      // Should show the CancelConfirmation dialog, NOT call closeWorkspace yet
      const dialog = fixture.nativeElement.querySelector('app-cancel-confirmation');
      expect(dialog).toBeTruthy();
      expect(mockWorkspaceService.closeWorkspace).not.toHaveBeenCalled();
    });

    it('should call closeWorkspace with force=true when confirmation is confirmed', async () => {
      const ws1 = createMockWorkspace({
        id: 'ws-1',
        label: 'busy-repo',
        runSummary: { running: 1, failed: 0, paused: 0 },
      });
      mockWorkspaceService.workspaces.set([ws1]);
      fixture.detectChanges();

      // Trigger close which shows dialog
      const closeBtn = fixture.nativeElement.querySelector('[aria-label="Close workspace"]') as HTMLButtonElement;
      closeBtn.click();
      await fixture.whenStable();
      fixture.detectChanges();

      // Simulate confirmation — should call with force=true to bypass active-runs guard.
      component.onCloseConfirmed(true, 'ws-1');
      await fixture.whenStable();

      expect(mockWorkspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1', true);
    });

    it('should NOT call closeWorkspace when confirmation is cancelled', async () => {
      const ws1 = createMockWorkspace({
        id: 'ws-1',
        label: 'busy-repo',
        runSummary: { running: 1, failed: 0, paused: 0 },
      });
      mockWorkspaceService.workspaces.set([ws1]);
      fixture.detectChanges();

      const closeBtn = fixture.nativeElement.querySelector('[aria-label="Close workspace"]') as HTMLButtonElement;
      closeBtn.click();
      await fixture.whenStable();
      fixture.detectChanges();

      component.onCloseConfirmed(false, 'ws-1');
      await fixture.whenStable();

      expect(mockWorkspaceService.closeWorkspace).not.toHaveBeenCalled();
    });

    it('should use notification service instead of alert on error', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'repo' });
      mockWorkspaceService.workspaces.set([ws1]);
      mockWorkspaceService.closeWorkspace.mockReturnValue(Promise.reject(new Error('Backend fail')));
      fixture.detectChanges();

      const closeBtn = fixture.nativeElement.querySelector('[aria-label="Close workspace"]') as HTMLButtonElement;
      closeBtn.click();
      await fixture.whenStable();

      expect(mockNotificationService.add).toHaveBeenCalledWith(expect.objectContaining({ type: 'error' }));
    });
  });

  describe('drag-drop reordering', () => {
    it('should call reorderWorkspaces on drop', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1' });
      const ws2 = createMockWorkspace({ id: 'ws-2' });
      mockWorkspaceService.workspaces.set([ws1, ws2]);

      fixture.detectChanges();

      // Simulate drop event
      const dropEvent = {
        previousIndex: 0,
        currentIndex: 1,
        item: {} as never,
        container: {} as never,
        previousContainer: {} as never,
        isPointerOverContainer: true,
        distance: { x: 0, y: 0 },
        dropPoint: { x: 0, y: 0 },
      };

      await component.onDrop(dropEvent as never);

      expect(mockWorkspaceService.reorderWorkspaces).toHaveBeenCalled();
    });

    it('should not call reorderWorkspaces when dropped in same position', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      const dropEvent = {
        previousIndex: 0,
        currentIndex: 0,
        item: {} as never,
        container: {} as never,
        previousContainer: {} as never,
        isPointerOverContainer: true,
        distance: { x: 0, y: 0 },
        dropPoint: { x: 0, y: 0 },
      };

      await component.onDrop(dropEvent as never);

      expect(mockWorkspaceService.reorderWorkspaces).not.toHaveBeenCalled();
    });
  });
});
