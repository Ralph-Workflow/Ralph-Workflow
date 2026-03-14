import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { CancelConfirmationComponent } from './cancel-confirmation.component';

describe('CancelConfirmationComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [CancelConfirmationComponent],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  function createComponent(overrides: {
    title?: string;
    message?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    confirmVariant?: 'danger' | 'warning';
  } = {}) {
    const fixture = TestBed.createComponent(CancelConfirmationComponent);
    fixture.componentRef.setInput('title', overrides.title ?? 'Cancel Session?');
    fixture.componentRef.setInput('message', overrides.message ?? 'This session will stop.');
    fixture.componentRef.setInput('confirmLabel', overrides.confirmLabel ?? 'Cancel Session');
    fixture.componentRef.setInput('cancelLabel', overrides.cancelLabel ?? 'Keep Running');
    fixture.componentRef.setInput('confirmVariant', overrides.confirmVariant ?? 'danger');
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance };
  }

  it('should create', () => {
    const { component } = createComponent();
    expect(component).toBeTruthy();
  });

  describe('renders title and message from inputs', () => {
    it('should render title text', () => {
      const { fixture } = createComponent({ title: 'Stop the run?' });
      const el: HTMLElement = fixture.nativeElement;
      expect(el.textContent).toContain('Stop the run?');
    });

    it('should render message text', () => {
      const { fixture } = createComponent({ message: 'All progress will be lost.' });
      const el: HTMLElement = fixture.nativeElement;
      expect(el.textContent).toContain('All progress will be lost.');
    });
  });

  describe('button labels', () => {
    it('should display confirm button with confirmLabel', () => {
      const { fixture } = createComponent({ confirmLabel: 'Yes, cancel' });
      const el: HTMLElement = fixture.nativeElement;
      const confirmBtn = el.querySelector<HTMLElement>('[data-testid="confirm-btn"]');
      expect(confirmBtn?.textContent?.trim()).toBe('Yes, cancel');
    });

    it('should display cancel button with cancelLabel', () => {
      const { fixture } = createComponent({ cancelLabel: 'No, keep going' });
      const el: HTMLElement = fixture.nativeElement;
      const cancelBtn = el.querySelector<HTMLElement>('[data-testid="cancel-btn"]');
      expect(cancelBtn?.textContent?.trim()).toBe('No, keep going');
    });
  });

  describe('confirmed output', () => {
    it('should emit true when confirm button is clicked', () => {
      const { fixture, component } = createComponent();
      let emitted: boolean | undefined;
      component.confirmed.subscribe((v: boolean) => emitted = v);

      const el: HTMLElement = fixture.nativeElement;
      const confirmBtn = el.querySelector<HTMLElement>('[data-testid="confirm-btn"]');
      confirmBtn?.click();
      fixture.detectChanges();

      expect(emitted).toBe(true);
    });

    it('should emit false when cancel button is clicked', () => {
      const { fixture, component } = createComponent();
      let emitted: boolean | undefined;
      component.confirmed.subscribe((v: boolean) => emitted = v);

      const el: HTMLElement = fixture.nativeElement;
      const cancelBtn = el.querySelector<HTMLElement>('[data-testid="cancel-btn"]');
      cancelBtn?.click();
      fixture.detectChanges();

      expect(emitted).toBe(false);
    });

    it('should emit false when Escape key is pressed on the dialog', () => {
      const { fixture, component } = createComponent();
      let emitted: boolean | undefined;
      component.confirmed.subscribe((v: boolean) => emitted = v);

      const el: HTMLElement = fixture.nativeElement;
      const dialog = el.querySelector<HTMLElement>('[data-testid="dialog"]');
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
      dialog?.dispatchEvent(event);
      fixture.detectChanges();

      expect(emitted).toBe(false);
    });
  });

  describe('confirmVariant styling', () => {
    it('should apply danger class when confirmVariant is danger', () => {
      const { fixture } = createComponent({ confirmVariant: 'danger' });
      const el: HTMLElement = fixture.nativeElement;
      const confirmBtn = el.querySelector<HTMLElement>('[data-testid="confirm-btn"]');
      expect(confirmBtn?.classList).toContain('btn-danger');
    });

    it('should apply warning class when confirmVariant is warning', () => {
      const { fixture } = createComponent({ confirmVariant: 'warning' });
      const el: HTMLElement = fixture.nativeElement;
      const confirmBtn = el.querySelector<HTMLElement>('[data-testid="confirm-btn"]');
      expect(confirmBtn?.classList).toContain('btn-warning');
    });
  });
});
