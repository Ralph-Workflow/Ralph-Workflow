import {
  Component,
  input,
  output,
  ChangeDetectionStrategy,
  HostListener,
} from '@angular/core';

@Component({
  selector: 'app-cancel-confirmation',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './cancel-confirmation.component.html',
  styleUrl: './cancel-confirmation.component.css',
})
export class CancelConfirmationComponent {
  readonly title = input<string>('Cancel Session?');
  readonly message = input<string>('');
  readonly confirmLabel = input<string>('Cancel Session');
  readonly cancelLabel = input<string>('Keep Running');
  readonly confirmVariant = input<'danger' | 'warning'>('danger');

  readonly confirmed = output<boolean>();

  @HostListener('keydown.escape')
  onEscape(): void {
    this.confirmed.emit(false);
  }

  onConfirm(): void {
    this.confirmed.emit(true);
  }

  onCancel(): void {
    this.confirmed.emit(false);
  }

  onBackdropClick(event: MouseEvent): void {
    if ((event.target as HTMLElement).classList.contains('dialog-backdrop')) {
      this.confirmed.emit(false);
    }
  }

  onDialogKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      this.confirmed.emit(false);
    }
  }

  get title_(): string { return this.title(); }
  get message_(): string { return this.message(); }
  get confirmLabel_(): string { return this.confirmLabel(); }
  get cancelLabel_(): string { return this.cancelLabel(); }

  get confirmBtnClass(): string {
    return `btn ${this.confirmVariant() === 'danger' ? 'btn-danger' : 'btn-warning'}`;
  }
}
