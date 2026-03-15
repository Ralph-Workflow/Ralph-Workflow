import { test as base, expect, Page, BrowserContext } from '@playwright/test';

const E2E_SERVER_URL = process.env.E2E_SERVER_URL || 'http://127.0.0.1:3001';

const TAURI_BRIDGE_SCRIPT = `
  (function() {
    window.__TAURI_INTERNALS__ = {
      invoke: async function(cmd, args) {
        args = args || {};
        const response = await fetch('${E2E_SERVER_URL}/invoke', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cmd: cmd, args: args }),
        });
        const json = await response.json();
        if (!json.ok) {
          throw new Error(json.error || 'Unknown error');
        }
        return json.data;
      },
      convertFileSrc: function(path) {
        return path;
      },
      transformCallback: function() {
        return function() {};
      }
    };
    console.log('[E2E Bridge] Tauri internals initialized');
  })();
`;

export interface TauriPage extends Page {
  tauri: {
    invoke: <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
  };
}

async function setupTestWorkspace(): Promise<void> {
  try {
    await fetch(`${E2E_SERVER_URL}/invoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        cmd: 'open_workspace', 
        args: { path: '/tmp/e2e-test-workspace' } 
      }),
    });
  } catch (e) {
    console.warn('[E2E Bridge] Could not set up test workspace:', e);
  }
}

export const test = base.extend<{ page: TauriPage }>({
  page: async ({ page }, use) => {
    await page.addInitScript(TAURI_BRIDGE_SCRIPT);
    
    const tauriInvoke = async <T = unknown>(cmd: string, args: Record<string, unknown> = {}): Promise<T> => {
      const response = await fetch(`${E2E_SERVER_URL}/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd, args }),
      });
      const json = await response.json();
      if (!json.ok) {
        throw new Error(json.error || `Command ${cmd} failed`);
      }
      return json.data as T;
    };

    (page as TauriPage).tauri = {
      invoke: tauriInvoke,
    };

    await setupTestWorkspace();
    
    await use(page as TauriPage);
  },
});

export { expect };
