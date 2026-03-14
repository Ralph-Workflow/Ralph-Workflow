import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { InlineWorktreeCreateComponent } from './inline-worktree-create.component';
import { WorktreesService } from '../../services/worktrees.service';
import type { WorktreeInfo } from '../../types';

describe('InlineWorktreeCreateComponent', () => {
  let component: InlineWorktreeCreateComponent;
  let fixture: ComponentFixture<InlineWorktreeCreateComponent>;
  let mockWorktreesService: {
    fetchWorktrees: ReturnType<typeof vi.fn>;
    createWorktree: ReturnType<typeof vi.fn>;
  };

  const createMockWorktree = (overrides: Partial<WorktreeInfo> = {}): WorktreeInfo => ({
    path: '/repo',
    branch: 'main',
    name: 'main',
    has_active_run: false,
    is_main: true,
    ...overrides,
  });

  beforeEach(async () => {
    mockWorktreesService = {
      fetchWorktrees: vi.fn(),
      createWorktree: vi.fn(),
    };

    await TestBed.configureTestingModule({
      imports: [InlineWorktreeCreateComponent],
      providers: [
        { provide: WorktreesService, useValue: mockWorktreesService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(InlineWorktreeCreateComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('form validation', () => {
    it('should disable button when no repo path', () => {
      component.repoPath = '';
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should disable button when missing branch', () => {
      component.repoPath = '/repo';
      component.branch.set('');
      component.name.set('wt-51-feature');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should disable button when missing name', () => {
      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(true);
    });

    it('should enable button when all fields are filled', () => {
      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-51-feature');
      fixture.detectChanges();
      const button = fixture.debugElement.nativeElement.querySelector('button[data-testid="wt-create-button"]');
      expect(button?.disabled).toBe(false);
    });
  });

  describe('branch auto-fill', () => {
    it('should auto-fill name from branch on blur when name is empty', () => {
      component.repoPath = '/repo';
      component.branch.set('wt-52-my-feature');
      component.name.set('');
      
      component.onBranchBlur();
      
      expect(component.name()).toBe('wt-52-my-feature');
    });

    it('should not overwrite existing name on blur', () => {
      component.repoPath = '/repo';
      component.branch.set('wt-52-my-feature');
      component.name.set('custom-name');
      
      component.onBranchBlur();
      
      expect(component.name()).toBe('custom-name');
    });
  });

  describe('submission', () => {
    it('should emit created event on create', async () => {
      const newWorktree = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1' });
      mockWorktreesService.createWorktree.mockResolvedValue(newWorktree);
      mockWorktreesService.fetchWorktrees.mockResolvedValue(undefined);

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');
      fixture.detectChanges();

      const emittedWorktree: WorktreeInfo[] = [];
      component.created.subscribe((wt) => emittedWorktree.push(wt));
      await component.handleCreate();

      expect(mockWorktreesService.createWorktree).toHaveBeenCalledWith(
        '/repo',
        'feature-branch',
        'wt-1'
      );
      expect(mockWorktreesService.fetchWorktrees).toHaveBeenCalledWith('/repo');
      expect(emittedWorktree.length).toBe(1);
      expect(emittedWorktree[0]).toEqual(newWorktree);
    });

    it('should display error on failure', async () => {
      mockWorktreesService.createWorktree.mockRejectedValue(new Error('Failed to create'));

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');
      fixture.detectChanges();

      await component.handleCreate();
      fixture.detectChanges();

      expect(component.error()).toBe('Failed to create');
      const errorEl = fixture.nativeElement.querySelector('[data-testid="wt-create-error"]');
      expect(errorEl?.textContent).toContain('Failed to create');
    });

    it('should set creating state during submission', async () => {
      const newWorktree = createMockWorktree({ path: '/repo/wt-1', name: 'wt-1' });
      mockWorktreesService.createWorktree.mockResolvedValue(newWorktree);
      mockWorktreesService.fetchWorktrees.mockResolvedValue(undefined);

      component.repoPath = '/repo';
      component.branch.set('feature-branch');
      component.name.set('wt-1');

      expect(component.creating()).toBe(false);
      
      const promise = component.handleCreate();
      expect(component.creating()).toBe(true);
      
      await promise;
      expect(component.creating()).toBe(false);
    });
  });
});
