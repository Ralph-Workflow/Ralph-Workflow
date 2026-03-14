import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { RouterTestingModule } from '@angular/router/testing';
import { RecentCompletionsComponent } from './recent-completions.component';
import type { SessionSummary } from '../../types';

describe('RecentCompletionsComponent', () => {
  let component: RecentCompletionsComponent;
  let componentRef: ComponentFixture<RecentCompletionsComponent>['componentRef'];
  let fixture: ComponentFixture<RecentCompletionsComponent>;

  const createMockCompletion = (overrides: Partial<SessionSummary> = {}): SessionSummary => ({
    run_id: 'run-1234567890abcdef',
    status: 'completed',
    repo_path: '/repo',
    worktree_path: null,
    created_at: new Date().toISOString(),
    description: 'Test completion',
    developer_agent: 'default',
    reviewer_agent: 'default',
    phase: 'completed',
    ...overrides,
  });

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [RecentCompletionsComponent, RouterTestingModule],
    }).compileComponents();

    fixture = TestBed.createComponent(RecentCompletionsComponent);
    component = fixture.componentInstance;
    componentRef = fixture.componentRef;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('empty state', () => {
    it('should render empty state when no completions provided', () => {
      componentRef.setInput('completions', []);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('No completed runs yet');
    });
  });

  describe('completion rows', () => {
    it('should render completion rows with run_id', () => {
      componentRef.setInput('completions', [createMockCompletion({ run_id: 'test-completion-id' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('test-completion');
    });

    it('should render completion rows with description', () => {
      componentRef.setInput('completions', [createMockCompletion({ description: 'Fixed the bug' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Fixed the bug');
    });

    it('should render relative time', () => {
      const recentCompletion = createMockCompletion({
        created_at: new Date(Date.now() - 30000).toISOString(),
      });
      componentRef.setInput('completions', [recentCompletion]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('ago');
    });

    it('should show "just now" for very recent completions', () => {
      const justNow = createMockCompletion({
        created_at: new Date(Date.now() - 5000).toISOString(),
      });
      componentRef.setInput('completions', [justNow]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('just now');
    });

    it('should show minutes ago for completions within an hour', () => {
      const minutesAgo = createMockCompletion({
        created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
      });
      componentRef.setInput('completions', [minutesAgo]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('5m ago');
    });

    it('should show hours ago for completions within a day', () => {
      const hoursAgo = createMockCompletion({
        created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
      });
      componentRef.setInput('completions', [hoursAgo]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('3h ago');
    });

    it('should show days ago for older completions', () => {
      const daysAgo = createMockCompletion({
        created_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
      });
      componentRef.setInput('completions', [daysAgo]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('2d ago');
    });
  });

  describe('view all link', () => {
    it('should not show "View all" link when 5 or fewer completions', () => {
      const completions = Array(5).fill(null).map((_, i) => createMockCompletion({ run_id: `run-${i}` }));
      componentRef.setInput('completions', completions);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('View all');
    });

    it('should show "View all" link when more than 5 completions', () => {
      const completions = Array(6).fill(null).map((_, i) => createMockCompletion({ run_id: `run-${i}` }));
      componentRef.setInput('completions', completions);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const viewAllLink = compiled.querySelector('a[routerLink="/sessions"]');
      expect(viewAllLink).toBeTruthy();
      expect(viewAllLink?.textContent).toContain('View all');
    });

    it('should link to sessions with completed status filter', () => {
      const completions = Array(6).fill(null).map((_, i) => createMockCompletion({ run_id: `run-${i}` }));
      componentRef.setInput('completions', completions);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const viewAllLink = compiled.querySelector('a[routerLink="/sessions"]');
      expect(viewAllLink).toBeTruthy();
    });
  });

  describe('viewRun event', () => {
    it('should emit viewRun event on row click', () => {
      const emitSpy = vi.spyOn(component.viewRun, 'emit');
      componentRef.setInput('completions', [createMockCompletion({ run_id: 'test-run-id' })]);
      fixture.detectChanges();

      const row = (fixture.nativeElement as HTMLElement).querySelector('[role="button"]') as HTMLElement | null;
      row?.click();

      expect(emitSpy).toHaveBeenCalledWith('test-run-id');
    });

    it('should emit viewRun event on View button click', () => {
      const emitSpy = vi.spyOn(component.viewRun, 'emit');
      componentRef.setInput('completions', [createMockCompletion({ run_id: 'test-run-id' })]);
      fixture.detectChanges();

      const viewButton = (fixture.nativeElement as HTMLElement).querySelector('button');
      viewButton?.click();

      expect(emitSpy).toHaveBeenCalledWith('test-run-id');
    });
  });

  describe('displayCompletions computed', () => {
    it('should add run_id_short property', () => {
      const longRunId = 'run-abcdefghijklmnop12345678';
      componentRef.setInput('completions', [createMockCompletion({ run_id: longRunId })]);
      fixture.detectChanges();

      const displayCompletions = component.displayCompletionsValue;
      expect(displayCompletions.length).toBe(1);
      expect(displayCompletions[0]!.run_id_short).toBe('run-abcdefghijkl');
      expect(displayCompletions[0]!.run_id).toBe(longRunId);
    });

    it('should add relativeTime property', () => {
      componentRef.setInput('completions', [createMockCompletion()]);
      fixture.detectChanges();

      const displayCompletions = component.displayCompletionsValue;
      expect(displayCompletions.length).toBe(1);
      expect(displayCompletions[0]!.relativeTime).toBeDefined();
      expect(typeof displayCompletions[0]!.relativeTime).toBe('string');
    });
  });

  describe('showViewAll computed', () => {
    it('should return false when 5 or fewer completions', () => {
      componentRef.setInput('completions', Array(5).fill(null).map(() => createMockCompletion()));
      fixture.detectChanges();

      expect(component.showViewAllValue).toBe(false);
    });

    it('should return true when more than 5 completions', () => {
      componentRef.setInput('completions', Array(6).fill(null).map(() => createMockCompletion()));
      fixture.detectChanges();

      expect(component.showViewAllValue).toBe(true);
    });
  });
});
