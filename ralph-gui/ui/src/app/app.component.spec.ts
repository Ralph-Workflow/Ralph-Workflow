import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RouterModule } from '@angular/router';
import { AppComponent } from './app.component';
import { WorktreesService } from './services/worktrees.service';
import { signal, WritableSignal } from '@angular/core';
import type { WorktreeInfo } from './types';

describe('AppComponent', () => {
  let component: AppComponent;
  let fixture: ComponentFixture<AppComponent>;
  let worktreesSignal: WritableSignal<WorktreeInfo[]>;
  let activeWorktreePathSignal: WritableSignal<string | null>;
  let lastRepoPathSignal: WritableSignal<string | null>;

  const createMockWorktreesService = () => ({
    worktrees: worktreesSignal.asReadonly(),
    activeWorktreePath: activeWorktreePathSignal.asReadonly(),
    lastRepoPath: lastRepoPathSignal.asReadonly(),
    switchContext: jasmine.createSpy('switchContext'),
  });

  beforeEach(async () => {
    worktreesSignal = signal<WorktreeInfo[]>([]);
    activeWorktreePathSignal = signal<string | null>(null);
    lastRepoPathSignal = signal<string | null>(null);

    await TestBed.configureTestingModule({
      imports: [AppComponent, RouterModule.forRoot([])],
      providers: [
        { provide: WorktreesService, useFactory: createMockWorktreesService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    component = fixture.componentInstance;
  });

  describe('contextDisplay', () => {
    it('should show "Select repository..." when no context is set', () => {
      expect(component.contextDisplay).toBe('Select repository...');
    });

    it('should show worktree name when active worktree is set', () => {
      activeWorktreePathSignal.set('/path/to/worktree');
      worktreesSignal.set([
        { path: '/path/to/worktree', name: 'feature-branch', branch: 'feature-branch', is_main: false, has_active_run: false },
      ]);
      lastRepoPathSignal.set('/path/to/repo');

      // Trigger change detection
      fixture.detectChanges();

      expect(component.contextDisplay).toBe('feature-branch');
    });

    it('should show repo folder name when last repo path is set', () => {
      lastRepoPathSignal.set('/Users/test/projects/my-repo');
      activeWorktreePathSignal.set(null);

      fixture.detectChanges();

      expect(component.contextDisplay).toBe('my-repo');
    });
  });

  describe('keyboard shortcuts', () => {
    it('should toggle help on "?" key', () => {
      expect(component.showHelp()).toBe(false);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: '?' }));

      expect(component.showHelp()).toBe(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: '?' }));

      expect(component.showHelp()).toBe(false);
    });

    it('should close help on Escape key', () => {
      component.showHelp.set(true);

      component.handleKeyboard(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(component.showHelp()).toBe(false);
    });

    it('should ignore shortcuts when focus is on input', () => {
      const mockTarget = { tagName: 'INPUT', isContentEditable: false } as HTMLElement;
      const inputEvent = { key: '?', target: mockTarget, preventDefault: () => {} } as unknown as KeyboardEvent;

      component.handleKeyboard(inputEvent);

      expect(component.showHelp()).toBe(false);
    });

    it('should ignore shortcuts when focus is on textarea', () => {
      const mockTarget = { tagName: 'TEXTAREA', isContentEditable: false } as HTMLElement;
      const textareaEvent = { key: '?', target: mockTarget, preventDefault: () => {} } as unknown as KeyboardEvent;

      component.handleKeyboard(textareaEvent);

      expect(component.showHelp()).toBe(false);
    });
  });

  describe('selectContext', () => {
    it('should call switchContext when path is provided', () => {
      lastRepoPathSignal.set('/repo');
      const mockService = TestBed.inject(WorktreesService);

      component.selectContext('/worktree');

      expect(mockService.switchContext).toHaveBeenCalledWith('/repo', '/worktree');
    });
  });

  describe('closeHelp', () => {
    it('should close help modal', () => {
      component.showHelp.set(true);

      component.closeHelp();

      expect(component.showHelp()).toBe(false);
    });
  });
});
