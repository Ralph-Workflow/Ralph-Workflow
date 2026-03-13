import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { PhaseTimelineComponent, PhaseInfo } from './phase-timeline.component';

describe('PhaseTimelineComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PhaseTimelineComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function createComponent(phases?: PhaseInfo[]) {
    const fixture = TestBed.createComponent(PhaseTimelineComponent);
    const component = fixture.componentInstance;
    if (phases !== undefined) {
      fixture.componentRef.setInput('phases', phases);
    }
    fixture.detectChanges();
    return { fixture, component };
  }

  it('should create', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  describe('renders 4 default phases', () => {
    it('should render 4 phase nodes', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'pending' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const nodes = el.querySelectorAll('[data-testid="phase-node"]');
      expect(nodes.length).toBe(4);
    });

    it('should render phase names', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'pending' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const text = el.textContent ?? '';
      expect(text).toContain('Plan');
      expect(text).toContain('Develop');
      expect(text).toContain('Review');
      expect(text).toContain('Commit');
    });
  });

  describe('active phase has pulse class', () => {
    it('should apply pulse class to active phase dot', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'completed' },
        { name: 'Develop', status: 'active' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const activeDot = el.querySelector('[data-testid="phase-dot-active"]');
      expect(activeDot).toBeTruthy();
      expect(activeDot?.classList).toContain('animate-pulse');
    });
  });

  describe('completed phase shows checkmark', () => {
    it('should show checkmark (✓) for completed phases', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'completed' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const completedDot = el.querySelector('[data-testid="phase-dot-completed"]');
      expect(completedDot).toBeTruthy();
      expect(completedDot?.textContent?.trim()).toBe('✓');
    });

    it('should show ✗ for failed phases', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'failed' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const failedDot = el.querySelector('[data-testid="phase-dot-failed"]');
      expect(failedDot).toBeTruthy();
      expect(failedDot?.textContent?.trim()).toBe('✗');
    });

    it('should show ○ for pending phases', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'pending' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const pendingDot = el.querySelector('[data-testid="phase-dot-pending"]');
      expect(pendingDot).toBeTruthy();
      expect(pendingDot?.textContent?.trim()).toBe('○');
    });
  });

  describe('click emits event for completed phases', () => {
    it('should emit phaseClick when a completed phase is clicked', () => {
      const planPhase: PhaseInfo = { name: 'Plan', status: 'completed', duration: '1m 0s' };
      const { fixture, component } = createComponent([
        planPhase,
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);

      spyOn(component.phaseClick, 'emit');

      const el: HTMLElement = fixture.nativeElement;
      const completedNode = el.querySelector<HTMLElement>('[data-testid="phase-node"][data-status="completed"]');
      completedNode?.click();

      expect(component.phaseClick.emit).toHaveBeenCalledWith(planPhase);
    });

    it('should NOT emit phaseClick when a pending phase is clicked', () => {
      const { fixture, component } = createComponent([
        { name: 'Plan', status: 'pending' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);

      spyOn(component.phaseClick, 'emit');

      const el: HTMLElement = fixture.nativeElement;
      const pendingNode = el.querySelector<HTMLElement>('[data-testid="phase-node"][data-status="pending"]');
      pendingNode?.click();

      expect(component.phaseClick.emit).not.toHaveBeenCalled();
    });
  });

  describe('duration display', () => {
    it('should show duration below phase label when provided', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'completed', duration: '2m 34s' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      expect(el.textContent).toContain('2m 34s');
    });

    it('should not show duration element when duration is not provided', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'pending' },
        { name: 'Develop', status: 'pending' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const durations = el.querySelectorAll('[data-testid="phase-duration"]');
      expect(durations.length).toBe(0);
    });
  });

  describe('empty phases', () => {
    it('should render empty when no phases provided', () => {
      const { fixture } = createComponent([]);
      const el: HTMLElement = fixture.nativeElement;
      const nodes = el.querySelectorAll('[data-testid="phase-node"]');
      expect(nodes.length).toBe(0);
    });
  });

  describe('phase-specific colors in dot style', () => {
    it('should use purple color (--phase-plan) for Plan phase when completed', () => {
      const { component } = createComponent([
        { name: 'Plan', status: 'completed' },
      ]);
      const planPhase: PhaseInfo = { name: 'Plan', status: 'completed' };
      const style = component.getDotStyle(planPhase);
      // Should reference phase-plan CSS variable
      expect(style).toContain('var(--phase-plan)');
    });

    it('should use blue color (--phase-develop) for Develop phase when active', () => {
      const { component } = createComponent([
        { name: 'Develop', status: 'active' },
      ]);
      const developPhase: PhaseInfo = { name: 'Develop', status: 'active' };
      const style = component.getDotStyle(developPhase);
      expect(style).toContain('var(--phase-develop)');
    });

    it('should use amber color (--phase-review) for Review phase when completed', () => {
      const { component } = createComponent([
        { name: 'Review', status: 'completed' },
      ]);
      const reviewPhase: PhaseInfo = { name: 'Review', status: 'completed' };
      const style = component.getDotStyle(reviewPhase);
      expect(style).toContain('var(--phase-review)');
    });

    it('should use green color (--phase-commit) for Commit phase when completed', () => {
      const { component } = createComponent([
        { name: 'Commit', status: 'completed' },
      ]);
      const commitPhase: PhaseInfo = { name: 'Commit', status: 'completed' };
      const style = component.getDotStyle(commitPhase);
      expect(style).toContain('var(--phase-commit)');
    });

    it('should use muted color for pending phases', () => {
      const { component } = createComponent([
        { name: 'Plan', status: 'pending' },
      ]);
      const pendingPhase: PhaseInfo = { name: 'Plan', status: 'pending' };
      const style = component.getDotStyle(pendingPhase);
      // Pending phases use default muted colors, not phase-specific
      expect(style).toContain('var(--border-default)');
    });
  });

  describe('connector line styles', () => {
    it('should show done connector for completed phase', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'completed' },
        { name: 'Develop', status: 'active' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const connectors = el.querySelectorAll('.phase-timeline__connector');
      expect(connectors[0]?.classList).toContain('phase-timeline__connector--done');
    });

    it('should show active connector for active phase', () => {
      const { fixture } = createComponent([
        { name: 'Plan', status: 'completed' },
        { name: 'Develop', status: 'active' },
        { name: 'Review', status: 'pending' },
        { name: 'Commit', status: 'pending' },
      ]);
      const el: HTMLElement = fixture.nativeElement;
      const connectors = el.querySelectorAll('.phase-timeline__connector');
      expect(connectors[1]?.classList).toContain('phase-timeline__connector--active');
    });
  });
});
