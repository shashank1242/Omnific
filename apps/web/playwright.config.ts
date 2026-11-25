import { defineConfig, devices } from "@playwright/test";

/**
 * Browser end-to-end tests (e2e/). They run against a running site: the compose stack (default
 * http://localhost:3000) or `E2E_BASE_URL`. `E2E_CHANNEL` picks the installed browser Playwright drives (msedge by
 * default, so no browser download is needed on Windows; "chrome" or "chromium" elsewhere).
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: process.env.E2E_CHANNEL ?? "msedge",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Edge"], channel: process.env.E2E_CHANNEL ?? "msedge" } },
    { name: "phone", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, grep: /@phone/ },
  ],
});
