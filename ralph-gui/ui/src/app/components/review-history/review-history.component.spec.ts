import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { ReviewHistoryComponent } from './review-history.component';
import type { ReviewSummary } from '../../types';

const MOCK_COMPLETE: ReviewSummary = {
  review_number: 1,
  status: 'Complete',
  duration_secs: 45.0,
  findings_count: 2,
};

const MOCK_RUNNING: ReviewSummary = {
  review_number: 2,
  status: 'Running',
  duration_secs: null,
  findings_count: 1,
};

const MOCK_FAILED: ReviewSummary = {
  review_number: 3,
  status: 'Failed',
  duration_secs: 20.0,
  findings_count: 0,
};

describe('ReviewHistoryComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ReviewHistoryComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function createComponent(reviews: ReviewSummary[] = []) {
    const fixture = TestBed.createComponent(ReviewHistoryComponent);
    fixture.componentRef.setInput('reviews', reviews);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance };
  }

  it('should create', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  describe('empty state', () => {
    it('should render nothing when reviews is empty', () => {
      const { fixture } = createComponent([]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelectorAll('[data-testid="review-row"]').length).toBe(0);
      // No heading when empty
      expect(el.querySelector('[data-testid="review-heading"]')).toBeNull();
    });
  });

  describe('renders correct number of rows', () => {
    it('should render one row per review', () => {
      const { fixture } = createComponent([MOCK_COMPLETE, MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const rows = el.querySelectorAll('[data-testid="review-row"]');
      expect(rows.length).toBe(2);
    });

    it('should render review number', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]);
      const el: HTMLElement = fixture.nativeElement;
      const row = el.querySelector('[data-testid="review-row"]');
      expect(row?.textContent).toContain('1');
    });
  });

  describe('duration formatting', () => {
    it('should display duration as Xs for sub-minute durations', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]); // 45s
      const el: HTMLElement = fixture.nativeElement;
      const duration = el.querySelector('[data-testid="review-duration"]');
      expect(duration?.textContent?.trim()).toBe('45s');
    });

    it('should display duration as Xm Ys for over-minute durations', () => {
      const over = { ...MOCK_COMPLETE, duration_secs: 90.0 };
      const { fixture } = createComponent([over]);
      const el: HTMLElement = fixture.nativeElement;
      const duration = el.querySelector('[data-testid="review-duration"]');
      expect(duration?.textContent?.trim()).toBe('1m 30s');
    });

    it('should not show duration element when duration_secs is null', () => {
      const { fixture } = createComponent([MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="review-duration"]')).toBeNull();
    });
  });

  describe('findings count', () => {
    it('should show singular finding for count 1', () => {
      const { fixture } = createComponent([MOCK_RUNNING]); // 1 finding
      const el: HTMLElement = fixture.nativeElement;
      const findingsEl = el.querySelector('[data-testid="review-findings"]');
      expect(findingsEl?.textContent?.trim()).toContain('1 finding');
    });

    it('should show plural findings for count > 1', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]); // 2 findings
      const el: HTMLElement = fixture.nativeElement;
      const findingsEl = el.querySelector('[data-testid="review-findings"]');
      expect(findingsEl?.textContent?.trim()).toContain('2 findings');
    });

    it('should show 0 findings', () => {
      const { fixture } = createComponent([MOCK_FAILED]); // 0 findings
      const el: HTMLElement = fixture.nativeElement;
      const findingsEl = el.querySelector('[data-testid="review-findings"]');
      expect(findingsEl?.textContent?.trim()).toContain('0 findings');
    });
  });

  describe('status badges', () => {
    it('should show complete status', () => {
      const { fixture } = createComponent([MOCK_COMPLETE]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="review-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('complete');
    });

    it('should show running status for active review', () => {
      const { fixture } = createComponent([MOCK_RUNNING]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="review-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('running');
    });

    it('should show failed status', () => {
      const { fixture } = createComponent([MOCK_FAILED]);
      const el: HTMLElement = fixture.nativeElement;
      const badge = el.querySelector('[data-testid="review-status"]');
      expect(badge?.textContent?.trim().toLowerCase()).toContain('failed');
    });
  });
});
