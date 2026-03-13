import { Injectable, computed, signal, inject, InjectionToken } from '@angular/core';
import { listen as tauriListen } from '@tauri-apps/api/event';

/** Injection token for Tauri's listen function — allows mocking in tests. */
export const NOTIFICATION_LISTEN_TOKEN = new InjectionToken<typeof tauriListen>(
  'NOTIFICATION_LISTEN_TOKEN',
  { providedIn: 'root', factory: () => tauriListen }
);

export interface Notification {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp: Date;
  read: boolean;
}

/** Payload emitted by the backend for run status changes. */
interface RunStatusChangePayload {
  run_id: string;
  status: string;
  context?: string;
}

const MAX_NOTIFICATIONS = 50;

@Injectable({ providedIn: 'root' })
export class NotificationService {
  private readonly listenFn = inject(NOTIFICATION_LISTEN_TOKEN);

  private readonly _notifications = signal<Notification[]>([]);
  private readonly _isPanelOpen = signal<boolean>(false);

  readonly notifications = this._notifications.asReadonly();
  readonly isPanelOpen = this._isPanelOpen.asReadonly();

  readonly unreadCount = computed(() => this._notifications().filter(n => !n.read).length);

  constructor() {
    void this.subscribeToRunStatusChanges();
  }

  add(n: Omit<Notification, 'id' | 'timestamp' | 'read'>): void {
    const notification: Notification = {
      id: crypto.randomUUID(),
      timestamp: new Date(),
      read: false,
      ...n,
    };
    this._notifications.update(list => {
      const updated = [notification, ...list];
      return updated.length > MAX_NOTIFICATIONS ? updated.slice(0, MAX_NOTIFICATIONS) : updated;
    });
  }

  dismiss(id: string): void {
    this._notifications.update(list => list.filter(n => n.id !== id));
  }

  dismissAll(): void {
    this._notifications.set([]);
  }

  markAllRead(): void {
    this._notifications.update(list => list.map(n => ({ ...n, read: true })));
  }

  togglePanel(): void {
    this._isPanelOpen.update(v => !v);
  }

  closePanel(): void {
    this._isPanelOpen.set(false);
  }

  /** Subscribe to backend run status change events to auto-populate notifications. */
  private subscribeToRunStatusChanges(): Promise<void> {
    return this.listenFn<RunStatusChangePayload>(
      'run-status-change',
      (event) => {
        const { status, run_id, context } = event.payload;
        const type = this.statusToType(status);
        const message = this.buildMessage(status, run_id, context ?? '');
        if (message) {
          this.add({ type, message });
        }
      }
    ).then(() => {
      // Unlisten function stored implicitly; notification service lives for app lifetime
    }).catch(() => {
      // Non-critical — if event subscription fails, notifications simply won't auto-populate
    });
  }

  private statusToType(status: string): Notification['type'] {
    const lower = status.toLowerCase();
    if (lower === 'completed') return 'success';
    if (lower === 'failed') return 'error';
    if (lower === 'degraded') return 'warning';
    return 'info';
  }

  private buildMessage(status: string, runId: string, context: string): string | null {
    const shortId = runId.length > 12 ? `${runId.slice(0, 12)}…` : runId;
    const lower = status.toLowerCase();
    if (lower === 'completed') return `Run ${shortId} completed${context ? ` (${context})` : ''}`;
    if (lower === 'failed') return `Run ${shortId} failed${context ? ` — ${context}` : ''}`;
    if (lower === 'paused') return `Run ${shortId} paused at checkpoint`;
    if (lower === 'degraded') return `Run ${shortId} is degraded — retries exceeded`;
    return null; // Don't notify for other status transitions
  }
}
