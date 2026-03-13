import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { NotificationService, Notification } from '../../services/notification.service';
import { RelativeTimePipe } from '../../pipes/relative-time.pipe';

@Component({
  selector: 'app-notification-center',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RelativeTimePipe],
  host: {
    '[class.panel-host]': 'true',
  },
  templateUrl: './notification-center.component.html',
  styleUrl: './notification-center.component.css',
})
export class NotificationCenterComponent {
  private readonly _notificationService = inject(NotificationService);

  /** Exposed as getters so the template does not need to call signals directly. */
  get isPanelOpen() { return this._notificationService.isPanelOpen(); }
  get unreadCount() { return this._notificationService.unreadCount(); }
  get notifications() { return this._notificationService.notifications(); }

  /** Delegates to service methods — kept as pass-throughs for template event binding. */
  get notificationService() { return this._notificationService; }

  /** Record of type → icon character, accessed via index in the template (no call expression). */
  readonly typeIcons: Record<Notification['type'], string> = {
    info: 'ℹ',
    success: '✓',
    warning: '⚠',
    error: '✗',
  };

  /** @deprecated Use the `relativeTime` pipe in templates. Kept for backwards-compatible spec access. */
  relativeTime(date: Date): string {
    const now = Date.now();
    const diffMs = now - date.getTime();
    const diffSeconds = Math.floor(diffMs / 1000);

    if (diffSeconds < 10) return 'just now';
    if (diffSeconds < 60) return `${diffSeconds}s ago`;

    const diffMinutes = Math.floor(diffSeconds / 60);
    if (diffMinutes < 60) return `${diffMinutes}m ago`;

    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) return `${diffHours}h ago`;

    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }
}
