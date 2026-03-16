import * as path from 'path';
import * as fs from 'fs';
import { execSync } from 'child_process';

export interface TestRepo {
  path: string;
  cleanup: () => void;
}

export function createTestRepo(): TestRepo {
  const tempDir = fs.mkdtempSync('ralph-e2e-test-');
  
  execSync('git init', { cwd: tempDir, stdio: 'pipe' });
  execSync('git config user.email "test@example.com"', { cwd: tempDir, stdio: 'pipe' });
  execSync('git config user.name "Test User"', { cwd: tempDir, stdio: 'pipe' });
  
  fs.writeFileSync(
    path.join(tempDir, 'README.md'),
    '# Test Repository\n\nThis is a test repository for E2E testing.\n'
  );
  execSync('git add README.md', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Initial commit"', { cwd: tempDir, stdio: 'pipe' });
  
  fs.mkdirSync(path.join(tempDir, 'src'), { recursive: true });
  fs.writeFileSync(path.join(tempDir, 'src', 'index.ts'), 'console.log("Hello World");\n');
  execSync('git add src/', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Add source files"', { cwd: tempDir, stdio: 'pipe' });

  const branchName = 'feature/test-branch';
  execSync(`git checkout -b ${branchName}`, { cwd: tempDir, stdio: 'pipe' });
  fs.writeFileSync(
    path.join(tempDir, 'src', 'feature.ts'),
    'export const feature = true;\n'
  );
  execSync('git add src/feature.ts', { cwd: tempDir, stdio: 'pipe' });
  execSync('git commit -m "Add feature"', { cwd: tempDir, stdio: 'pipe' });
  execSync('git checkout main', { cwd: tempDir, stdio: 'pipe' });

  const agentDir = path.join(tempDir, '.agent');
  fs.mkdirSync(agentDir, { recursive: true });

  const checkpointData = {
    run_id: 'test-run-001',
    phase: 'Plan',
    timestamp: new Date().toISOString(),
    developer_agent: 'claude-sonnet',
    reviewer_agent: 'claude-sonnet',
    is_degraded: false,
    iteration_count: 0,
    review_count: 0,
    total_files_changed: 0,
  };
  fs.writeFileSync(
    path.join(agentDir, 'checkpoint.json'),
    JSON.stringify(checkpointData, null, 2)
  );

  fs.writeFileSync(
    path.join(agentDir, 'ralph-workflow.toml'),
    `[agent]
name = "claude-sonnet"
model = "claude-sonnet-4-20250514"

[drains]
planning = "default"
development = "default"
analysis = "default"
review = "default"
fix = "default"
commit = "default"

[execution]
developer_iterations = 3
review_passes = 2
`
  );

  return {
    path: tempDir,
    cleanup: () => {
      fs.rmSync(tempDir, { recursive: true, force: true });
    },
  };
}

export function setupGlobalTestRepo(): string {
  const repo = createTestRepo();
  process.env.E2E_REPO_PATH = repo.path;
  return repo.path;
}

export function cleanupGlobalTestRepo(): void {
  const repoPath = process.env.E2E_REPO_PATH;
  if (repoPath) {
    fs.rmSync(repoPath, { recursive: true, force: true });
    delete process.env.E2E_REPO_PATH;
  }
}
