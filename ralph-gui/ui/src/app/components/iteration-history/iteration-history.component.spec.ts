import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { IterationHistoryComponent } from './iteration-history.component';
import type { IterationSummary } from '../../types';

const MOCK_COMPLETE: IterationSummary = {
  iteration_number: 1,
  status: 'Complete',
  duration_secs: 252.0,
  files_changed: 3,
  tests_passed: 8,
  tests_total: 10,
};

const MOCK_RUNNING: IterationSummary = {
  iteration_number: 2,
  status: 'Running',
  duration_secs: null,
  files_changed: 0,
  tests_passed: null,
  tests_total: null,
};

const MOCK_FAILED: IterationSummary = {
  iteration_number: 3,
  status: 'Failed',
  duration_secs: 45.0,
  files_changed: 1,
  tests_passed: 2,
  tests_total: 5,
};

describe('IterationHistoryComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [IterationHistoryComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function createComponent(
    iterations: IterationSummary[] = [],
    currentIteration: number | null = null,
  ) {
    const fixture = TestBed.createComponent(IterationHistoryComponent);
    fixture.componentRef.setInput('iterations', iterations);
    fixture.componentRef.setInput('currentIteration', currentIteration);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance };
  }

  it('should create', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  describe('empty state', () => {
    it('should show empty state when no iterations provided', () => {
      const { fixture } = createComponent([]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="iteration-empty"]')).toBeTruthy();
    });

    it('should not render iteration rows when iterations is empty', () => {
      const { fixture } = createComponent([]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelectorAll('[data-testid="iteration-row"]').length).toBe(0);
    });
  });

  describe('renders correct number of rows', () => {
    it('should render one row per iteration', () => {
      const { fixture } = createComponent([MOCK_COMPLETE, MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const rows = el.querySelectorAll('[data-testid="iteration-row"]');
      expect(rows.length).toBe(2);
    });

    it('should render iteration number', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]);
      const el: HTMLElement = fixture.nativeElement;
      const row = el.querySelector('[data-testid="iteration-row"]');
      expect(row?.textContent).toContain('1');
    });
  });

  describe('duration formatting', () => {
    it('should display duration formatted as Xm Ys when duration_secs is provided', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]); // 252s = 4m 12s
      const el: HTMLElement = fixture.nativeElement;
      expect(el.textContent).toContain('4m 12s');
    });

    it('should not show duration when duration_secs is null', () => {
      const { fixture } = createComponent([MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const durationEl = el.querySelector('[data-testid="iteration-duration"]');
      expect(durationEl).toBeNull();
    });
  });

  describe('test results display', () => {
    it('should show test results as N of M pass when present', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.textContent).toContain('8 of 10');
    });

    it('should not show test results when tests_passed is null', () => {
      const { fixture } = createComponent([MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const testEl = el.querySelector('[data-testid="iteration-tests"]');
      expect(testEl).toBeNull();
    });
  });

  describe('status badges', () => {
    it('should show complete badge for Complete status', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="iteration-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('complete');
    });

    it('should show running badge for Running status', () => {
      const { fixture } = createComponent([MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="iteration-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('running');
    });

    it('should show failed badge for Failed status', () => {
      const { fixture } = createComponent([MOCK_FAILED]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="iteration-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('failed');
    });
  });

  describe('active iteration highlighting', () => {
    it('should apply active class to the current iteration row', () => {
      const { fixture } = createComponent([MOCK_COMPLETE, MOCK_RUNNING], 2);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const rows = el.querySelectorAll('[data-testid="iteration-row"]');
      // Only the second row (iteration 2) should have the active background class
      expect(rows[0]?.className).not.toContain('bg-[rgba(245,158,11,0.06)]');
      expect(rows[1]?.className).toContain('bg-[rgba(245,158,11,0.06)]');
    });

    it('should not apply active class when currentIteration is null', () => {
      const { fixture } = createComponent([MOCK_COMPLETE, MOCK_RUNNING], null);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const rows = el.querySelectorAll('[data-testid="iteration-row"]');
      // No rows should have the active background class
      rows.forEach(row => {
        expect(row.className).not.toContain('bg-[rgba(245,158,11,0.06)]');
      });
    });
  });

  describe('iterationClick output', () => {
    it('should emit iterationClick with iteration number when files badge is clicked', () => {
      const { fixture, component } = createComponent([MOCK_COMPLETE]);
      let emitted: number | undefined;
      component.iterationClick.subscribe((n: number) => emitted = n);

      const el: HTMLElement = fixture.nativeElement;
      const filesBadge = el.querySelector<HTMLElement>('[data-testid="iteration-files"]');
      filesBadge?.click();
      fixture.detectChanges();

      expect(emitted).toBe(1);
    });
  });
});
