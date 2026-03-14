import { ComponentFixture, TestBed } from '@angular/core/testing';
import { PreflightSummaryComponent } from './preflight-summary.component';

describe('PreflightSummaryComponent', () => {
  let component: PreflightSummaryComponent;
  let fixture: ComponentFixture<PreflightSummaryComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [PreflightSummaryComponent],
      providers: [],
    }).compileComponents();
    
    fixture = TestBed.createComponent(PreflightSummaryComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  describe('contextRows', () => {
    it('should return repository and worktree context', () => {
      component.repoPath = '/repo';
      component.worktreePath = '/repo/wt-1';
      fixture.detectChanges();

      const rows = component.contextRows;

      expect(rows.length).toBe(2);
      expect(rows[0]?.label).toBe('Repository');
      expect(rows[0]?.value).toBe('/repo');
      expect(rows[1]?.label).toBe('Context');
      expect(rows[1]?.value).toBe('/repo/wt-1');
    });

    it('should show direct repository when no worktree', () => {
      component.repoPath = '/repo';
      component.worktreePath = null;
      fixture.detectChanges();

      const rows = component.contextRows;

      expect(rows.length).toBe(2);
      expect(rows[1]?.value).toBe('Direct repository');
    });
  });

  describe('launch button enablement', () => {
    it('should enable launch button when not launching', () => {
      component.isLaunching = false;
      fixture.detectChanges();
      
      const button = fixture.debugElement.nativeElement.querySelector('button.btn-primary');
      expect(button?.disabled).toBe(false);
    });

    it('should disable launch button when launching', () => {
      component.isLaunching = true;
      fixture.detectChanges();
      
      const button = fixture.debugElement.nativeElement.querySelector('button.btn-primary');
      expect(button?.disabled).toBe(true);
    });

    it('should show launching text when launching', () => {
      component.isLaunching = true;
      fixture.detectChanges();
      
      const button = fixture.debugElement.nativeElement.querySelector('button.btn-primary');
      expect(button?.textContent).toContain('Launching…');
    });
  });

  describe('button events', () => {
    it('should emit confirmLaunch on launch button click', () => {
      component.isLaunching = false;
      fixture.detectChanges();
      
      spyOn(component.confirmLaunch, 'emit');
      const button = fixture.debugElement.nativeElement.querySelector('button.btn-primary');
      button.click();
      
      expect(component.confirmLaunch.emit).toHaveBeenCalled();
    });

    it('should emit goBack on back button click', () => {
      fixture.detectChanges();
      
      spyOn(component.goBack, 'emit');
      const button = fixture.debugElement.nativeElement.querySelector('button.btn-secondary');
      button.click();
      
      expect(component.goBack.emit).toHaveBeenCalled();
    });
  });

  describe('config display', () => {
    it('should display developer iterations', () => {
      component.developerIterations = 10;
      fixture.detectChanges();
      
      const text = fixture.debugElement.nativeElement.textContent;
      expect(text).toContain('10');
    });

    it('should display reviewer passes', () => {
      component.reviewerPasses = 3;
      fixture.detectChanges();
      
      const text = fixture.debugElement.nativeElement.textContent;
      expect(text).toContain('3');
    });
  });
});
