import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ActiveRunsListComponent } from './active-runs-list.component';
import { RunStatusBadgeComponent } from '../run-status-badge/run-status-badge.component';
import type { SessionSummary } from '../../types';

describe('ActiveRunsListComponent', () => {
  let component: ActiveRunsListComponent;
  let componentRef: ComponentFixture<ActiveRunsListComponent>['componentRef'];
  let fixture: ComponentFixture<ActiveRunsListComponent>;

  const createMockRun = (overrides: Partial<SessionSummary> = {}): SessionSummary => ({
    run_id: 'run-1234567890abcdef',
    status: 'running',
    repo_path: '/repo',
    worktree_path: null,
    created_at: '2024-01-01T00:00:00Z',
    description: 'Test run',
    developer_agent: 'default',
    reviewer_agent: 'default',
    phase: 'development',
    ...overrides,
  });

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ActiveRunsListComponent, RunStatusBadgeComponent],
    }).compileComponents();

    fixture = TestBed.createComponent(ActiveRunsListComponent);
    component = fixture.componentInstance;
    componentRef = fixture.componentRef;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('empty state', () => {
    it('should render empty state when no active runs provided', () => {
      componentRef.setInput('runs', []);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('No runs currently active');
    });
  });

  describe('run cards', () => {
    it('should render correct number of run cards for given input', () => {
      const runs = [
        createMockRun({ run_id: 'run-1' }),
        createMockRun({ run_id: 'run-2' }),
        createMockRun({ run_id: 'run-3' }),
      ];
      componentRef.setInput('runs', runs);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const cards = compiled.querySelectorAll('[role="button"]');
      expect(cards.length).toBe(3);
    });

    it('should display truncated run_id (16 chars)', () => {
      const longRunId = 'run-1234567890abcdefghijklmnop';
      componentRef.setInput('runs', [createMockRun({ run_id: longRunId })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('run-1234567890ab');
      expect(compiled.textContent).not.toContain('mnop');
    });

    it('should display description', () => {
      componentRef.setInput('runs', [createMockRun({ description: 'Implement feature X' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Implement feature X');
    });

    it('should display phase and agent', () => {
      componentRef.setInput('runs', [createMockRun({ phase: 'review', developer_agent: 'claude-3' })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Review');
      expect(compiled.textContent).toContain('claude-3');
    });

    it('should show degraded indicator when is_degraded is true', () => {
      componentRef.setInput('runs', [createMockRun({ is_degraded: true })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('degraded');
    });

    it('should not show degraded indicator when is_degraded is false', () => {
      componentRef.setInput('runs', [createMockRun({ is_degraded: false })]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).not.toContain('degraded');
    });
  });

  describe('viewRun event', () => {
    it('should emit viewRun event with correct run_id on button click', () => {
      const emitSpy = vi.spyOn(component.viewRun, 'emit');
      componentRef.setInput('runs', [createMockRun({ run_id: 'test-run-id' })]);
      fixture.detectChanges();

      const viewButton = (fixture.nativeElement as HTMLElement).querySelector('button');
      viewButton?.click();

      expect(emitSpy).toHaveBeenCalledWith('test-run-id');
    });

    it('should emit viewRun event on row click', () => {
      const emitSpy = vi.spyOn(component.viewRun, 'emit');
      componentRef.setInput('runs', [createMockRun({ run_id: 'test-run-id' })]);
      fixture.detectChanges();

      const row = (fixture.nativeElement as HTMLElement).querySelector('[role="button"]') as HTMLElement | null;
      row?.click();

      expect(emitSpy).toHaveBeenCalledWith('test-run-id');
    });
  });

  describe('count badge', () => {
    it('should show count badge with correct number', () => {
      const runs = [
        createMockRun({ run_id: 'run-1' }),
        createMockRun({ run_id: 'run-2' }),
      ];
      componentRef.setInput('runs', runs);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const badge = compiled.querySelector('.rounded-full');
      expect(badge?.textContent?.trim()).toBe('2');
    });

    it('should show count badge with 1 for single run', () => {
      componentRef.setInput('runs', [createMockRun()]);
      fixture.detectChanges();

      const compiled = fixture.nativeElement as HTMLElement;
      const badge = compiled.querySelector('.rounded-full');
      expect(badge?.textContent?.trim()).toBe('1');
    });
  });

  describe('runCount computed', () => {
    it('should return correct count', () => {
      expect(component.runCountValue).toBe(0);

      componentRef.setInput('runs', [createMockRun(), createMockRun()]);
      fixture.detectChanges();

      expect(component.runCountValue).toBe(2);
    });
  });

  describe('displayRuns computed', () => {
    it('should add run_id_short property', () => {
      const longRunId = 'run-abcdefghijklmnop12345678';
      componentRef.setInput('runs', [createMockRun({ run_id: longRunId })]);
      fixture.detectChanges();

      const displayRuns = component.displayRunsValue;
      expect(displayRuns.length).toBe(1);
      expect(displayRuns[0]!.run_id_short).toBe('run-abcdefghijkl');
      expect(displayRuns[0]!.run_id).toBe(longRunId);
    });
  });
});
