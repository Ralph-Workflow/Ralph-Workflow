import { ComponentFixture, TestBed } from '@angular/core/testing';
import { signal, WritableSignal } from '@angular/core';
import { NotificationCenterComponent } from './notification-center.component';
import { NotificationService, Notification } from '../../services/notification.service';

describe('NotificationCenterComponent', () => {
  let component: NotificationCenterComponent;
  let fixture: ComponentFixture<NotificationCenterComponent>;
  let notificationsSignal: WritableSignal<Notification[]>;
  let isPanelOpenSignal: WritableSignal<boolean>;
  let unreadCountValue: number;

  const createMockNotificationService = () => ({
    notifications: notificationsSignal.asReadonly(),
    isPanelOpen: isPanelOpenSignal.asReadonly(),
    unreadCount: () => unreadCountValue,
    dismiss: jasmine.createSpy('dismiss'),
    dismissAll: jasmine.createSpy('dismissAll'),
    markAllRead: jasmine.createSpy('markAllRead'),
    togglePanel: jasmine.createSpy('togglePanel'),
    closePanel: jasmine.createSpy('closePanel'),
    add: jasmine.createSpy('add'),
  });

  const makeNotification = (overrides: Partial<Notification> = {}): Notification => ({
    id: 'test-id-1',
    type: 'info',
    message: 'Test message',
    timestamp: new Date('2024-01-01T10:00:00Z'),
    read: false,
    ...overrides,
  });

  beforeEach(async () => {
    notificationsSignal = signal<Notification[]>([]);
    isPanelOpenSignal = signal<boolean>(false);
    unreadCountValue = 0;

    await TestBed.configureTestingModule({
      imports: [NotificationCenterComponent],
      providers: [
        { provide: NotificationService, useFactory: createMockNotificationService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(NotificationCenterComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('panel visibility', () => {
    it('should not show panel when isPanelOpen is false', () => {
      isPanelOpenSignal.set(false);
      fixture.detectChanges();
      const panel = fixture.nativeElement.querySelector('.notification-panel') as HTMLElement | null;
      // Panel may be in DOM but not visible/open
      if (panel) {
        expect(panel.classList.contains('open')).toBe(false);
      } else {
        expect(panel).toBeNull();
      }
    });

    it('should show panel when isPanelOpen is true', () => {
      isPanelOpenSignal.set(true);
      fixture.detectChanges();
      const panel = fixture.nativeElement.querySelector('.notification-panel') as HTMLElement | null;
      expect(panel).not.toBeNull();
      expect(panel!.classList.contains('open')).toBe(true);
    });
  });

  describe('notification rendering', () => {
    it('should show empty state when no notifications', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([]);
      fixture.detectChanges();
      const emptyState = fixture.nativeElement.querySelector('.empty-state');
      expect(emptyState).not.toBeNull();
      expect(emptyState.textContent).toContain('No notifications');
    });

    it('should render notifications when present', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([
        makeNotification({ id: '1', message: 'First notification', type: 'info' }),
        makeNotification({ id: '2', message: 'Second notification', type: 'error' }),
      ]);
      fixture.detectChanges();
      const items = fixture.nativeElement.querySelectorAll('.notification-item');
      expect(items.length).toBe(2);
    });

    it('should display notification message text', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([makeNotification({ message: 'Hello World' })]);
      fixture.detectChanges();
      const item = fixture.nativeElement.querySelector('.notification-item');
      expect(item.textContent).toContain('Hello World');
    });

    it('should display type icons for each notification type', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([
        makeNotification({ id: '1', type: 'info' }),
        makeNotification({ id: '2', type: 'success' }),
        makeNotification({ id: '3', type: 'warning' }),
        makeNotification({ id: '4', type: 'error' }),
      ]);
      fixture.detectChanges();
      const icons = fixture.nativeElement.querySelectorAll('.notification-type-icon');
      expect(icons.length).toBe(4);
    });
  });

  describe('dismiss button', () => {
    it('should call dismiss when dismiss button is clicked', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([makeNotification({ id: 'abc-123' })]);
      fixture.detectChanges();
      const dismissBtn = fixture.nativeElement.querySelector('.dismiss-btn') as HTMLButtonElement;
      expect(dismissBtn).not.toBeNull();
      dismissBtn.click();
      const service = TestBed.inject(NotificationService);
      expect(service.dismiss).toHaveBeenCalledWith('abc-123');
    });
  });

  describe('mark all read button', () => {
    it('should call markAllRead when "Mark all as read" is clicked', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([makeNotification()]);
      unreadCountValue = 1;
      fixture.detectChanges();
      const btn = fixture.nativeElement.querySelector('.mark-all-read-btn') as HTMLButtonElement;
      expect(btn).not.toBeNull();
      btn.click();
      const service = TestBed.inject(NotificationService);
      expect(service.markAllRead).toHaveBeenCalled();
    });
  });

  describe('dismiss all button', () => {
    it('should call dismissAll when "Dismiss all" is clicked', () => {
      isPanelOpenSignal.set(true);
      notificationsSignal.set([makeNotification()]);
      fixture.detectChanges();
      const btn = fixture.nativeElement.querySelector('.dismiss-all-btn') as HTMLButtonElement;
      expect(btn).not.toBeNull();
      btn.click();
      const service = TestBed.inject(NotificationService);
      expect(service.dismissAll).toHaveBeenCalled();
    });
  });

  describe('relative time', () => {
    it('should compute relative time from timestamp', () => {
      const twoMinutesAgo = new Date(Date.now() - 2 * 60 * 1000);
      const result = component.relativeTime(twoMinutesAgo);
      expect(result).toContain('m ago');
    });

    it('should show "just now" for very recent notifications', () => {
      const justNow = new Date();
      const result = component.relativeTime(justNow);
      expect(result).toBe('just now');
    });
  });
});
