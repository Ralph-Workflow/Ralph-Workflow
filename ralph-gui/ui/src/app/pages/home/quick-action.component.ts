import { Component, input, output, ChangeDetectionStrategy } from '@angular/core';

@Component({
  selector: 'app-quick-action',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './quick-action.component.html',
})
export class QuickActionComponent {
  readonly icon = input('');
  readonly label = input('');
  readonly desc = input('');
  readonly action = output<void>();

  get iconValue(): string {
    return this.icon();
  }

  get labelValue(): string {
    return this.label();
  }

  get descValue(): string {
    return this.desc();
  }
}
