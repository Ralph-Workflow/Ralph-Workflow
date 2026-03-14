import { ComponentFixture, TestBed } from '@angular/core/testing';
import { WorkspaceLoadingSkeletonComponent } from './workspace-loading-skeleton.component';

describe('WorkspaceLoadingSkeletonComponent', () => {
  let component: WorkspaceLoadingSkeletonComponent;
  let fixture: ComponentFixture<WorkspaceLoadingSkeletonComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [WorkspaceLoadingSkeletonComponent],
      providers: [],
    }).compileComponents();

    fixture = TestBed.createComponent(WorkspaceLoadingSkeletonComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should have aria-busy="true" on root element for accessibility', () => {
    const root = fixture.nativeElement.querySelector('[aria-busy="true"]');
    expect(root).toBeTruthy();
  });

  it('should have aria-label indicating loading state', () => {
    const root = fixture.nativeElement.querySelector('[aria-label="Loading workspace"]');
    expect(root).toBeTruthy();
  });

  it('should render skeleton rows matching the sessions list layout', () => {
    // Skeleton rows mirror the sessions/worktrees list structure
    const rows = fixture.nativeElement.querySelectorAll('.bg-white\\/5.rounded-md');
    expect(rows.length).toBe(component.skeletonRows.length);
  });

  it('should render skeleton cards matching the dashboard cards layout', () => {
    // Skeleton cards mirror the dashboard summary card layout
    const cards = fixture.nativeElement.querySelectorAll('.h-\\[72px\\]');
    expect(cards.length).toBe(component.skeletonCards.length);
  });

  it('should expose skeletonRows with default count of 5', () => {
    expect(component.skeletonRows.length).toBe(5);
  });

  it('should expose skeletonCards with default count of 3', () => {
    expect(component.skeletonCards.length).toBe(3);
  });

  it('should have animate-pulse class for pulse animation', () => {
    const animated = fixture.nativeElement.querySelector('.animate-pulse');
    expect(animated).toBeTruthy();
  });
});
