import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { ContextualHelpComponent } from './contextual-help.component';

describe('ContextualHelpComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ContextualHelpComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function createComponent(helpText = 'This is helpful info') {
    const fixture = TestBed.createComponent(ContextualHelpComponent);
    fixture.componentRef.setInput('helpText', helpText);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance };
  }

  it('should create', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  it('should start with popover closed', () => {
    const { component } = createComponent();
    expect(component.isOpen()).toBe(false);
  });

  it('should open popover when [?] button is clicked', () => {
    const { fixture, component } = createComponent();
    const el: HTMLElement = fixture.nativeElement;
    const btn = el.querySelector<HTMLElement>('[data-testid="contextual-help-btn"]');
    expect(btn).toBeTruthy();
    btn?.click();
    expect(component.isOpen()).toBe(true);
  });

  it('should toggle popover off when clicked again', () => {
    const { fixture, component } = createComponent();
    const el: HTMLElement = fixture.nativeElement;
    const btn = el.querySelector<HTMLElement>('[data-testid="contextual-help-btn"]');
    btn?.click();
    fixture.detectChanges();
    expect(component.isOpen()).toBe(true);

    btn?.click();
    expect(component.isOpen()).toBe(false);
  });

  it('should render the popover with helpText when open', () => {
    const { fixture, component } = createComponent('Controls verbosity level of log output');
    component.open();
    fixture.detectChanges();

    const el: HTMLElement = fixture.nativeElement;
    const popover = el.querySelector('[data-testid="contextual-help-popover"]');
    expect(popover).toBeTruthy();
    expect(popover?.textContent).toContain('Controls verbosity level of log output');
  });

  it('should NOT render popover when closed', () => {
    const { fixture } = createComponent();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-testid="contextual-help-popover"]')).toBeNull();
  });

  it('should close popover when close() is called', () => {
    const { fixture, component } = createComponent();
    component.open();
    fixture.detectChanges();
    expect(component.isOpen()).toBe(true);

    component.close();
    expect(component.isOpen()).toBe(false);
  });

  it('should have aria-expanded reflecting open state', () => {
    const { fixture, component } = createComponent();
    const el: HTMLElement = fixture.nativeElement;
    const btn = el.querySelector<HTMLButtonElement>('[data-testid="contextual-help-btn"]');

    expect(btn?.getAttribute('aria-expanded')).toBe('false');
    component.open();
    fixture.detectChanges();
    expect(btn?.getAttribute('aria-expanded')).toBe('true');
  });
});
