import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { signal } from '@angular/core';
import { Router } from '@angular/router';
import { WorktreesComponent } from './worktrees.component';
import { WorktreesService } from '../../services/worktrees.service';
import { TauriService } from '../../services/tauri.service';
import type { WorktreeInfo } from '../../types';

const makeWorktrees = (): WorktreeInfo[] => [
  {
    path: '/repo/main',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
  },
  {
    path: '/repo/wt-51-feature',
    branch: 'wt-51-feature',
    name: 'wt-51-feature',
    has_active_run: true,
    is_main: false,
  },
  {
    path: '/repo/wt-52-bugfix',
    branch: 'wt-52-bugfix',
    name: 'wt-52-bugfix',
    has_active_run: false,
    is_main: false,
  },
];

describe('WorktreesComponent', () => {
  let component: WorktreesComponent;
  let fixture: ComponentFixture<WorktreesComponent>;
  let mockWorktreesService: {
    worktrees: ReturnType<typeof signal<WorktreeInfo[]>>;
    status: ReturnType<typeof signal<'idle' | 'loading' | 'succeeded' | 'failed'>>;
    error: ReturnType<typeof signal<string | null>>;
    activeWorktreePath: ReturnType<typeof signal<string | null>>;
    fetchWorktrees: ReturnType<typeof vi.fn>;
    createWorktree: ReturnType<typeof vi.fn>;
  };
  let mockTauriService: {
    openInFileManager: ReturnType<typeof vi.fn>;
  };
  let mockRouter: { navigate: ReturnType<typeof vi.fn> };

  beforeEach(async () => {
    mockWorktreesService = {
      worktrees: signal<WorktreeInfo[]>([]),
      status: signal<'idle' | 'loading' | 'succeeded' | 'failed'>('idle'),
      error: signal<string | null>(null),
      activeWorktreePath: signal<string | null>(null),
      fetchWorktrees: vi.fn(),
      createWorktree: vi.fn(),
    };

    mockTauriService = {
      openInFileManager: vi.fn().mockResolvedValue(undefined),
    };

    mockRouter = { navigate: vi.fn() };

    await TestBed.configureTestingModule({
      imports: [WorktreesComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
        { provide: TauriService, useValue: mockTauriService },
        { provide: Router, useValue: mockRouter },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(WorktreesComponent);
    component = fixture.componentInstance;
  });

  describe('worktree list rendering', () => {
    it('should show empty state when no repo path', () => {
      fixture.detectChanges();
      expect(component.repoPathValue).toBe('');
    });

    it('should show loading state', () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      mockWorktreesService.status.set('loading');
      fixture.detectChanges();

      expect(component.worktreesStatus).toBe('loading');
    });

    it('should show error state', () => {
      mockWorktreesService.error.set('Failed to load worktrees');
      mockWorktreesService.status.set('failed');
      fixture.detectChanges();

      expect(component.worktreesStatus).toBe('failed');
      expect(component.worktreesError).toBe('Failed to load worktrees');
    });

    it('should compute worktreesWithMeta with active state', () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      mockWorktreesService.activeWorktreePath.set('/repo/wt-51-feature');
      fixture.detectChanges();

      const list = component.worktreesList;
      expect(list.length).toBe(3);
      expect(list.find(w => w.name === 'wt-51-feature')?.active).toBe(true);
      expect(list.find(w => w.name === 'main')?.active).toBe(false);
    });

    it('should mark main worktree as active when activeWorktreePath is null', () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      mockWorktreesService.activeWorktreePath.set(null);
      fixture.detectChanges();

      const list = component.worktreesList;
      expect(list.find(w => w.name === 'main')?.active).toBe(true);
    });

    it('should compute mainWorktree from service', () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      fixture.detectChanges();

      expect(component.mainWorktree()?.name).toBe('main');
    });
  });

  describe('context menu', () => {
    it('should set contextMenuWorktree on right-click', () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      fixture.detectChanges();

      const worktree = makeWorktrees()[1]!;
      const event = new MouseEvent('contextmenu', { bubbles: true });

      component.contextMenuTrigger = { openMenu: vi.fn() } as unknown as typeof component.contextMenuTrigger;

      component.onContextMenu(event, worktree);

      expect(component.contextMenuWorktreeValue).toBe(worktree);
    });

    it('should prevent default on context menu event', () => {
      const event = {
        preventDefault: vi.fn(),
        stopPropagation: vi.fn(),
      } as unknown as MouseEvent;

      component.contextMenuTrigger = { openMenu: vi.fn() } as unknown as typeof component.contextMenuTrigger;

      component.onContextMenu(event, makeWorktrees()[0]!);

      expect(event.preventDefault).toHaveBeenCalled();
      expect(event.stopPropagation).toHaveBeenCalled();
    });

    it('contextMenuCanStartSession should be false for main worktree', () => {
      const mainWorktree = makeWorktrees()[0]!;
      component.contextMenuWorktree.set(mainWorktree);

      expect(component.contextMenuCanStartSession).toBe(false);
    });

    it('contextMenuCanStartSession should be true for non-main worktree', () => {
      const featureWorktree = makeWorktrees()[1]!;
      component.contextMenuWorktree.set(featureWorktree);

      expect(component.contextMenuCanStartSession).toBe(true);
    });

    it('onContextMenuStartSession should navigate to sessions for non-main worktree', () => {
      const featureWorktree = makeWorktrees()[1]!;
      component.contextMenuWorktree.set(featureWorktree);

      component.onContextMenuStartSession();

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/sessions'], {
        queryParams: { new: 'true', worktree: featureWorktree.path }
      });
      expect(component.contextMenuWorktree()).toBe(null);
    });

    it('onContextMenuStartSession should not navigate for main worktree', () => {
      const mainWorktree = makeWorktrees()[0]!;
      component.contextMenuWorktree.set(mainWorktree);

      component.onContextMenuStartSession();

      expect(mockRouter.navigate).not.toHaveBeenCalled();
      expect(component.contextMenuWorktree()).toBe(null);
    });

    it('onContextMenuOpenInFileManager should call tauri service', async () => {
      const featureWorktree = makeWorktrees()[1]!;
      component.contextMenuWorktree.set(featureWorktree);

      await component.onContextMenuOpenInFileManager();

      expect(mockTauriService.openInFileManager).toHaveBeenCalledWith(featureWorktree.path);
      expect(component.contextMenuWorktree()).toBe(null);
    });

    it('onContextMenuViewDiff should navigate to sessions with worktree query param', () => {
      const featureWorktree = makeWorktrees()[2]!;
      component.contextMenuWorktree.set(featureWorktree);

      component.onContextMenuViewDiff();

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/sessions'], {
        queryParams: { new: 'true', worktree: featureWorktree.path }
      });
      expect(component.contextMenuWorktree()).toBe(null);
    });
  });

  describe('create worktree form', () => {
    it('should start with showCreate false', () => {
      expect(component.showCreateValue).toBe(false);
    });

    it('startCreate should set showCreate to true', () => {
      component.startCreate();
      expect(component.showCreateValue).toBe(true);
      expect(component.createErrorValue).toBe(null);
    });

    it('cancelCreate should hide form and reset state', () => {
      component.startCreate();
      component.form.update(f => ({ ...f, branch: 'test-branch' }));

      component.cancelCreate();

      expect(component.showCreateValue).toBe(false);
      expect(component.createErrorValue).toBe(null);
      expect(component.formValue).toEqual({ branch: '', name: '' });
    });

    it('handleCreate should show error when branch is empty', async () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      component.form.set({ branch: '', name: 'test-name' });

      await component.handleCreate();

      expect(component.createErrorValue).toBe('Branch and worktree name are required.');
    });

    it('handleCreate should show error when name is empty', async () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      component.form.set({ branch: 'test-branch', name: '' });

      await component.handleCreate();

      expect(component.createErrorValue).toBe('Branch and worktree name are required.');
    });

    it('handleCreate should call createWorktree service', async () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      mockWorktreesService.createWorktree.mockResolvedValue({
        path: '/repo/wt-53-new',
        branch: 'wt-53-new',
        name: 'wt-53-new',
        has_active_run: false,
        is_main: false,
      });
      component.form.set({ branch: 'wt-53-new', name: 'wt-53-new' });
      component.startCreate();

      await component.handleCreate();

      expect(mockWorktreesService.createWorktree).toHaveBeenCalledWith(
        '/repo/main',
        'wt-53-new',
        'wt-53-new'
      );
      expect(component.showCreateValue).toBe(false);
    });

    it('handleCreate should show error on service failure', async () => {
      mockWorktreesService.worktrees.set(makeWorktrees());
      mockWorktreesService.createWorktree.mockRejectedValue(new Error('Worktree already exists'));
      component.form.set({ branch: 'wt-51-feature', name: 'wt-51-feature' });
      component.startCreate();

      await component.handleCreate();

      expect(component.createErrorValue).toBe('Worktree already exists');
      expect(component.showCreateValue).toBe(true);
    });

    it('onBranchInput should update form branch', () => {
      const event = { target: { value: 'new-branch' } } as unknown as Event;
      component.onBranchInput(event);
      expect(component.formValue.branch).toBe('new-branch');
    });

    it('onNameInput should update form name', () => {
      const event = { target: { value: 'wt-99-feature' } } as unknown as Event;
      component.onNameInput(event);
      expect(component.formValue.name).toBe('wt-99-feature');
    });

    it('autoFillName should set name from branch if name is empty', () => {
      component.form.set({ branch: 'wt-55-feature', name: '' });
      component.autoFillName();
      expect(component.formValue.name).toBe('wt-55-feature');
    });

    it('autoFillName should not overwrite existing name', () => {
      component.form.set({ branch: 'wt-55-feature', name: 'custom-name' });
      component.autoFillName();
      expect(component.formValue.name).toBe('custom-name');
    });
  });

  describe('start session from row', () => {
    it('startSession should navigate to sessions page', () => {
      const worktree = makeWorktrees()[1]!;

      component.startSession(worktree);

      expect(mockRouter.navigate).toHaveBeenCalledWith(['/sessions'], {
        queryParams: { new: 'true', worktree: worktree.path }
      });
    });
  });

  describe('isActive helper', () => {
    it('should return true when worktree path matches activeWorktreePath', () => {
      mockWorktreesService.activeWorktreePath.set('/repo/wt-51-feature');
      const worktree = makeWorktrees()[1]!;

      expect(component.isActive(worktree)).toBe(true);
    });

    it('should return true for main worktree when activeWorktreePath is null', () => {
      mockWorktreesService.activeWorktreePath.set(null);
      const mainWorktree = makeWorktrees()[0]!;

      expect(component.isActive(mainWorktree)).toBe(true);
    });

    it('should return false for non-active worktree', () => {
      mockWorktreesService.activeWorktreePath.set('/repo/wt-51-feature');
      const otherWorktree = makeWorktrees()[2]!;

      expect(component.isActive(otherWorktree)).toBe(false);
    });
  });
});
