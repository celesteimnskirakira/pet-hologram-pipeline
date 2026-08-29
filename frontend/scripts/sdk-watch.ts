#!/usr/bin/env bun
/**
 * Watches `../eazo-sdk/sdk/src` for changes and runs `sdk:dev`
 * (build SDK + sync into this template's `node_modules/@eazo/sdk/`)
 * on every save.
 *
 * Why: Turbopack rejects `file:` / symlink workspace deps for `@eazo/sdk`,
 * so the local dev loop relies on a hard copy. Manually re-running
 * `bun run sdk:sync` after every SDK edit gets old fast. This watcher
 * automates that step.
 *
 * Pair with: `transpilePackages: ["@eazo/sdk"]` in `next.config.ts`.
 * With that flag set, Turbopack includes the synced SDK in its watch +
 * HMR graph, so the running `bun dev` picks up each rebuild automatically
 * — no `next dev` restart needed.
 *
 * Run:  bun run sdk:watch
 */

import { watch } from "node:fs";
import { resolve, relative } from "node:path";
import { spawn } from "node:child_process";

// `__dirname` works in both Bun and Node and avoids the Bun-specific
// `import.meta.dir` getter, which trips up strict TS checks during the
// template's `next build`.
const TEMPLATE_ROOT = resolve(__dirname, "..");
const SDK_SRC = resolve(TEMPLATE_ROOT, "../eazo-sdk/sdk/src");

// Coalesce rapid bursts of file events into a single rebuild — saving a
// file in an editor often fires multiple `change` events within ms.
const DEBOUNCE_MS = 250;

let pending = false;
let running = false;
let queuedAgain = false;

async function runSync(reason: string): Promise<void> {
  if (running) {
    queuedAgain = true;
    return;
  }
  running = true;
  console.log(`\n[sdk-watch] ${reason} → rebuilding SDK + syncing…`);
  const start = Date.now();

  await new Promise<void>((res) => {
    const proc = spawn("bun", ["run", "sdk:dev"], {
      cwd: TEMPLATE_ROOT,
      stdio: "inherit",
    });
    proc.on("exit", (code) => {
      const ms = Date.now() - start;
      if (code === 0) {
        console.log(`[sdk-watch] done in ${ms}ms — browser should reload via Turbopack HMR.`);
      } else {
        console.log(`[sdk-watch] failed (exit ${code}) after ${ms}ms`);
      }
      res();
    });
  });

  running = false;
  if (queuedAgain) {
    queuedAgain = false;
    void runSync("queued change while last build was running");
  }
}

function scheduleSync(path: string): void {
  if (pending) return;
  pending = true;
  setTimeout(() => {
    pending = false;
    void runSync(`change: ${relative(SDK_SRC, path)}`);
  }, DEBOUNCE_MS);
}

console.log(`[sdk-watch] watching ${SDK_SRC} (Ctrl-C to stop)`);
const watcher = watch(SDK_SRC, { recursive: true }, (event, filename) => {
  if (!filename) return;
  // Ignore obvious noise — editor swap files, lockfiles, hidden dirs.
  if (filename.startsWith(".") || filename.endsWith("~")) return;
  scheduleSync(resolve(SDK_SRC, filename));
});

process.on("SIGINT", () => {
  watcher.close();
  console.log("\n[sdk-watch] stopped.");
  process.exit(0);
});

// Initial build, so the first invocation always starts from a clean state.
void runSync("initial sync");
