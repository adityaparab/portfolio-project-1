import { defineConfig } from "@playwright/test";

/**
 E2E smoke against the Compose stack (issue #39):
 docker compose up -d api && docker compose --profile ui up -d ui
 then `npm run e2e`. The smoke covers the deterministic wiring end to
 end — the full decision journey additionally needs a live model backend
 (Phase 5 eval runs cover that end to end).
*/
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
});
