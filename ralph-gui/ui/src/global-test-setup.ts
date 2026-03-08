// Global setup runs in the main vitest process before any worker threads or
// forked processes are created. Setting NODE_ENV here ensures worker processes
// inherit the development value, so React loads its development build instead
// of the production build (which does not support act() in tests).

// Declare minimal process type without requiring @types/node.
declare const process: { env: Record<string, string | undefined> };

export function setup() {
  process.env["NODE_ENV"] = "development";
}
