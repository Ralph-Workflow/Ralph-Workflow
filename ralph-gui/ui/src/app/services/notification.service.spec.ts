import { TestBed } from '@angular/core/testing';
import { NotificationService, NOTIFICATION_LISTEN_TOKEN } from './notification.service';

describe('NotificationService', () => {
  let service: NotificationService;
  let listenSpy: jasmine.Spy;

  beforeEach(() => {
    // Mock the Tauri listen function to prevent real IPC calls during tests
    listenSpy = jasmine.createSpy('listen').and.returnValue(
      Promise.resolve(jasmine.createSpy('unlisten'))
    );

    TestBed.configureTestingModule({
      providers: [
        { provide: NOTIFICATION_LISTEN_TOKEN, useValue: listenSpy },
      ],
    });
    service = TestBed.inject(NotificationService);
  });

  describe('add', () => {
    it('should start with zero notifications', () => {
      expect(service.notifications().length).toBe(0);
    });

    it('should add a notification and increment unreadCount', () => {
      service.add({ type: 'info', message: 'Hello' });
      expect(service.notifications().length).toBe(1);
      expect(service.unreadCount()).toBe(1);
    });

    it('should assign a unique id and timestamp on each notification', () => {
      service.add({ type: 'success', message: 'Done' });
      const n = service.notifications()[0];
      expect(n).toBeDefined();
      expect(n!.id).toBeTruthy();
      expect(n!.timestamp).toBeInstanceOf(Date);
      expect(n!.read).toBe(false);
    });

    it('should keep only the 50 most recent notifications when 51 are added', () => {
      for (let i = 0; i < 51; i++) {
        service.add({ type: 'info', message: `msg ${i}` });
      }
      expect(service.notifications().length).toBe(50);
    });

    it('should keep the newest notification when over max', () => {
      for (let i = 0; i < 51; i++) {
        service.add({ type: 'info', message: `msg ${i}` });
      }
      // The last added (msg 50) should still be present
      const messages = service.notifications().map(n => n.message);
      expect(messages).toContain('msg 50');
      // The oldest (msg 0) should have been dropped
      expect(messages).not.toContain('msg 0');
    });
  });

  describe('dismiss', () => {
    it('should remove a notification by id', () => {
      service.add({ type: 'warning', message: 'Warn' });
      const id = service.notifications()[0]!.id;
      service.dismiss(id);
      expect(service.notifications().length).toBe(0);
    });

    it('should not affect other notifications when dismissing one', () => {
      service.add({ type: 'info', message: 'First' });
      service.add({ type: 'error', message: 'Second' });
      // Newest first: [Second(0), First(1)]
      // Dismiss "Second" (index 0), "First" should remain
      const secondId = service.notifications()[0]!.id;
      service.dismiss(secondId);
      expect(service.notifications().length).toBe(1);
      expect(service.notifications()[0]!.message).toBe('First');
    });
  });

  describe('dismissAll', () => {
    it('should clear all notifications', () => {
      service.add({ type: 'info', message: 'A' });
      service.add({ type: 'info', message: 'B' });
      service.dismissAll();
      expect(service.notifications().length).toBe(0);
      expect(service.unreadCount()).toBe(0);
    });
  });

  describe('markAllRead', () => {
    it('should set read=true on all notifications', () => {
      service.add({ type: 'info', message: 'A' });
      service.add({ type: 'success', message: 'B' });
      service.markAllRead();
      const allRead = service.notifications().every(n => n.read);
      expect(allRead).toBe(true);
    });

    it('should reset unreadCount to 0', () => {
      service.add({ type: 'info', message: 'A' });
      service.add({ type: 'error', message: 'B' });
      expect(service.unreadCount()).toBe(2);
      service.markAllRead();
      expect(service.unreadCount()).toBe(0);
    });
  });

  describe('togglePanel', () => {
    it('should start with panel closed', () => {
      expect(service.isPanelOpen()).toBe(false);
    });

    it('should open the panel on first toggle', () => {
      service.togglePanel();
      expect(service.isPanelOpen()).toBe(true);
    });

    it('should close the panel on second toggle', () => {
      service.togglePanel();
      service.togglePanel();
      expect(service.isPanelOpen()).toBe(false);
    });
  });

  describe('closePanel', () => {
    it('should close the panel', () => {
      service.togglePanel();
      service.closePanel();
      expect(service.isPanelOpen()).toBe(false);
    });
  });

  describe('Tauri event subscription', () => {
    it('should call listenFn with run-status-change event on construction', () => {
      expect(listenSpy).toHaveBeenCalledWith('run-status-change', jasmine.any(Function));
    });

    it('should add a success notification when run-status-change with completed status fires', async () => {
      // Get the event handler registered by the service
      const callArgs = listenSpy.calls.mostRecent().args as [string, (event: { payload: { run_id: string; status: string; context?: string } }) => void];
      const handler = callArgs[1];

      handler({ payload: { run_id: 'abc123', status: 'completed' } });

      expect(service.notifications().length).toBe(1);
      expect(service.notifications()[0]!.type).toBe('success');
      expect(service.notifications()[0]!.message).toContain('completed');
    });

    it('should add an error notification when run-status-change with failed status fires', () => {
      const callArgs = listenSpy.calls.mostRecent().args as [string, (event: { payload: { run_id: string; status: string; context?: string } }) => void];
      const handler = callArgs[1];

      handler({ payload: { run_id: 'xyz987', status: 'failed', context: 'build error' } });

      expect(service.notifications().length).toBe(1);
      expect(service.notifications()[0]!.type).toBe('error');
    });

    it('should NOT add a notification for Running status', () => {
      const callArgs = listenSpy.calls.mostRecent().args as [string, (event: { payload: { run_id: string; status: string; context?: string } }) => void];
      const handler = callArgs[1];

      handler({ payload: { run_id: 'run1', status: 'running' } });

      expect(service.notifications().length).toBe(0);
    });
  });
});
