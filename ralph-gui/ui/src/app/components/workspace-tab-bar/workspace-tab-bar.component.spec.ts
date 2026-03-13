import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkspaceTabBarComponent } from './workspace-tab-bar.component';
import { WorkspaceService } from '../../services/workspace.service';
import { TauriService, TAURI_INVOKE } from '../../services/tauri.service';
import { signal } from '@angular/core';
import type { Workspace } from '../../services/workspace.service';

describe('WorkspaceTabBarComponent', () => {
  let component: WorkspaceTabBarComponent;
  let fixture: ComponentFixture<WorkspaceTabBarComponent>;
  let mockWorkspaceService: jasmine.SpyObj<WorkspaceService>;
  let mockTauriService: jasmine.SpyObj<TauriService>;

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
    mockWorkspaceService = jasmine.createSpyObj(
      'WorkspaceService',
      ['openWorkspace', 'closeWorkspace', 'switchWorkspace', 'reorderWorkspaces'],
      {
        workspaces: signal<Workspace[]>([]),
        activeWorkspaceId: signal<string | null>(null),
        activeWorkspace: signal<Workspace | null>(null),
        isLoading: signal<boolean>(false),
      },
    );
    mockWorkspaceService.openWorkspace.and.returnValue(Promise.resolve(createMockWorkspace()));
    mockWorkspaceService.closeWorkspace.and.returnValue(Promise.resolve());
    mockWorkspaceService.reorderWorkspaces.and.returnValue(Promise.resolve());

    mockTauriService = jasmine.createSpyObj(
      'TauriService',
      ['openDirectoryDialog'],
    );
    mockTauriService.openDirectoryDialog.and.returnValue(Promise.resolve(null));

    const mockInvoke = jasmine.createSpy('invoke').and.returnValue(Promise.resolve([]));

    await TestBed.configureTestingModule({
      imports: [WorkspaceTabBarComponent],
      providers: [
        { provide: WorkspaceService, useValue: mockWorkspaceService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: TAURI_INVOKE, useValue: mockInvoke },
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

      const plusButton = fixture.nativeElement.querySelector('.add-tab-btn');
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

      const closeBtn = fixture.nativeElement.querySelector('.tab-close');
      closeBtn.click();

      await fixture.whenStable();
      expect(mockWorkspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1');
    });

    it('should close on middle-click (button === 1)', async () => {
      const ws1 = createMockWorkspace({ id: 'ws-1', label: 'project-alpha' });
      mockWorkspaceService.workspaces.set([ws1]);

      fixture.detectChanges();

      const tab = fixture.nativeElement.querySelector('.tab');
      const mouseupEvent = new MouseEvent('mouseup', { button: 1 });
      tab.dispatchEvent(mouseupEvent);

      await fixture.whenStable();
      expect(mockWorkspaceService.closeWorkspace).toHaveBeenCalledWith('ws-1');
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

      const plusBtn = fixture.nativeElement.querySelector('.add-tab-btn');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockTauriService.openDirectoryDialog).toHaveBeenCalled();
    });

    it('should open workspace after dialog returns path', async () => {
      mockTauriService.openDirectoryDialog.and.returnValue(Promise.resolve('/new/workspace'));

      fixture.detectChanges();

      const plusBtn = fixture.nativeElement.querySelector('.add-tab-btn');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockWorkspaceService.openWorkspace).toHaveBeenCalledWith('/new/workspace');
    });

    it('should not open workspace if dialog cancelled', async () => {
      mockTauriService.openDirectoryDialog.and.returnValue(Promise.resolve(null));

      fixture.detectChanges();

      const plusBtn = fixture.nativeElement.querySelector('.add-tab-btn');
      plusBtn.click();

      await fixture.whenStable();
      expect(mockWorkspaceService.openWorkspace).not.toHaveBeenCalled();
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
