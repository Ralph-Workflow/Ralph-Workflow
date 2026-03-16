import { test as base, Page, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';
import { execSync } from 'child_process';

const E2E_SERVER_URL = 'http://127.0.0.1:3001';

// Injected into every test page before Angular initializes.
// Defines window.__TAURI_INTERNALS__ so Angular's Tauri IPC calls are routed
// to the e2e-server HTTP endpoint instead of the native Tauri bridge.
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
    convertFileSrc: function(p) { return p; },
    transformCallback: function() { return function() {}; }
  };
})();
`;

interface InvokeResponse {
  ok: boolean;
  data: unknown;
  error: string | null;
}

export interface TauriInvokeBridge {
  invoke: <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
}

export interface TestRepoFixture {
  path: string;
  cleanup: () => void;
}

async function invokeE2E(cmd: string, args: Record<string, unknown> = {}): Promise<InvokeResponse> {
  const response = await fetch(`${E2E_SERVER_URL}/invoke`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd, args }),
  });
  return response.json() as Promise<InvokeResponse>;
}

export function createTauriInvokeBridge(): TauriInvokeBridge {
  return {
    invoke: async <T = unknown>(cmd: string, args: Record<string, unknown> = {}): Promise<T> => {
      const response = await invokeE2E(cmd, args);
      if (!response.ok) {
        throw new Error(`Tauri command failed: ${cmd} - ${response.error || 'Unknown error'}`);
      }
      return response.data as T;
    },
  };
}

function createTestRepo(): TestRepoFixture {
  const tempDir = fs.mkdtempSync('ralph-e2e-test-');
  
  execSync('git init', { cwd: tempDir, stdio: 'pipe' });
  execSync('git config user.email "test@example.com"', { cwd: tempDir, stdio: 'pipe' });
  execSync('git config user.name "Test User"', { cwd: tempDir, stdio: 'pipe' });
  
  fs.writeFileSync(path.join(tempDir, 'README.md'), '# Test Repository\n\nThis is a test repository for E2E testing.\n');
  execSync('git add README.md', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Initial commit"', { cwd: tempDir, stdio: 'pipe' });
  
  fs.mkdirSync(path.join(tempDir, 'src'), { recursive: true });
  fs.writeFileSync(path.join(tempDir, 'src', 'index.ts'), 'console.log("Hello World");\n');
  execSync('git add src/', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Add source files"', { cwd: tempDir, stdio: 'pipe' });

  const branchName = 'feature/test-branch';
  execSync(`git checkout -b ${branchName}`, { cwd: tempDir, stdio: 'pipe' });
  fs.writeFileSync(path.join(tempDir, 'src', 'feature.ts'), 'export const feature = true;\n');
  execSync('git add src/feature.ts', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Add feature"', { cwd: tempDir, stdio: 'pipe' });
  execSync('git checkout main', { cwd: tempDir, stdio: 'pipe' });

  return {
    path: tempDir,
    cleanup: () => {
      fs.rmSync(tempDir, { recursive: true, force: true });
    },
  };
}

export interface TestFixtures {
  tauri: TauriInvokeBridge;
  testRepo: TestRepoFixture;
  page: Page;
  waitForWorkspace: () => Promise<void>;
}

export const test = base.extend<TestFixtures>({
  page: async ({ page }, use) => {
    await page.addInitScript(TAURI_BRIDGE_SCRIPT);

    // Override page.goto to use hash routing
    const originalGoto = page.goto.bind(page);
    page.goto = async (url: string, options?: Parameters<typeof originalGoto>[1]) => {
      // Convert /path to /#/path for Angular hash routing
      if (url.startsWith('/') && !url.startsWith('/#')) {
        url = '/#' + url;
      }
      return originalGoto(url, options);
    };

    await use(page);
  },

  // Custom fixture for waiting for workspace to load
  // Use this in tests that need a workspace selected
  waitForWorkspace: async ({ page }, use) => {
    const waitForWorkspaceLoaded = async (): Promise<void> => {
      // Wait for app-root to be visible first
      await page.waitForSelector('app-root', { timeout: 10000 });

      // Wait for workspace to be loaded - look for workspace-related UI
      // The status bar shows workspace info when loaded
      await page.waitForFunction(() => {
        // Check if either we have a workspace selected or we're on the welcome page
        const statusBar = document.querySelector('app-status-bar');
        const welcomePage = document.querySelector('app-welcome');

        if (welcomePage) return true; // Welcome page is fine

        // If status bar exists, check if it has workspace info
        if (statusBar) {
          // Try to find evidence of workspace being loaded
          const statusText = statusBar.textContent || '';
          // If it says "Select repository" we haven't loaded workspace yet
          if (statusText.includes('Select repository')) {
            return false;
          }
          return true;
        }
        return false;
      }, { timeout: 10000 });
    };

    await use(waitForWorkspaceLoaded);
  },

  tauri: async ({}, use) => {
    const bridge = createTauriInvokeBridge();
    await use(bridge);
  },

  testRepo: async ({}, use) => {
    const repo = createTestRepo();
    await use(repo);
    repo.cleanup();
  },
});

export { expect } from '@playwright/test';
