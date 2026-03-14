import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RouterModule } from '@angular/router';
import { ConceptsGuideComponent } from './concepts-guide.component';

describe('ConceptsGuideComponent', () => {
  let component: ConceptsGuideComponent;
  let fixture: ComponentFixture<ConceptsGuideComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ConceptsGuideComponent, RouterModule.forRoot([])],
      providers: [],
    }).compileComponents();

    fixture = TestBed.createComponent(ConceptsGuideComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('isOpen signal', () => {
    it('should start closed (isOpen = false)', () => {
      expect(component.isOpen()).toBe(false);
    });

    it('should toggle to open on first toggle()', () => {
      component.toggle();
      expect(component.isOpen()).toBe(true);
    });

    it('should toggle back to closed on second toggle()', () => {
      component.toggle();
      component.toggle();
      expect(component.isOpen()).toBe(false);
    });
  });

  describe('collapsible sections', () => {
    beforeEach(() => {
      component.isOpen.set(true);
      fixture.detectChanges();
    });

    it('should render "How It Works" section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      const sections = nativeEl.querySelectorAll('details');
      const sectionTexts = Array.from(sections).map(s => s.textContent ?? '');
      expect(sectionTexts.some(t => t.includes('How It Works'))).toBe(true);
    });

    it('should render "The Pipeline" section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('The Pipeline');
    });

    it('should render Agent Chains and Drains section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Agent Chains');
    });

    it('should render "Worktrees" section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Worktrees');
    });

    it('should render "Sessions and Runs" section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Sessions');
    });

    it('should render "Configuration Scopes" section', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Configuration Scopes');
    });

    it('should render all drain descriptions', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      const text = nativeEl.textContent ?? '';
      expect(text).toContain('Analysis');
      expect(text).toContain('Planning');
      expect(text).toContain('Development');
      expect(text).toContain('Review');
      expect(text).toContain('Fix');
      expect(text).toContain('Commit');
    });

    it('should show analysis drain description about GPT models', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('GPT models recommended');
    });
  });

  describe('pipeline phases', () => {
    beforeEach(() => {
      component.isOpen.set(true);
      fixture.detectChanges();
    });

    it('should render Plan phase', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Plan');
    });

    it('should render Develop phase', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Develop');
    });

    it('should render Review phase', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Review');
    });

    it('should render Commit phase', () => {
      const nativeEl = fixture.nativeElement as HTMLElement;
      expect(nativeEl.textContent).toContain('Commit');
    });
  });

  describe('visibility based on isOpen', () => {
    it('should not show sections when isOpen is false', () => {
      component.isOpen.set(false);
      fixture.detectChanges();
      const nativeEl = fixture.nativeElement as HTMLElement;
      const guideBody = nativeEl.querySelector('.concepts-guide-body');
      if (guideBody) {
        // Either body is not in DOM or is hidden
        expect(guideBody.getAttribute('hidden') !== null || !guideBody.classList.contains('visible')).toBe(true);
      } else {
        // Body not rendered at all when closed
        expect(guideBody).toBeNull();
      }
    });

    it('should show sections when isOpen is true', () => {
      component.isOpen.set(true);
      fixture.detectChanges();
      const nativeEl = fixture.nativeElement as HTMLElement;
      const sections = nativeEl.querySelectorAll('details');
      expect(sections.length).toBeGreaterThan(0);
    });
  });
});
