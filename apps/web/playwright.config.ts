import { defineConfig, devices } from "@playwright/test";

// Two kinds of browser tests:
//  * mocked      — MOCKED API: every /api/og/* and /api/session/* call is answered by
//                  fixtures in e2e/mocked/fixtures.ts. Runs anywhere (CI).
//  * integration — REAL API: the running local stack (web → API → Postgres, verifier,
//                  fixture pages). Opt-in with E2E_BASE_URL; see docs/P07_UI_VERIFICATION.md.
const MOCK_PORT = 3021;

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "mocked-desktop", testDir: "e2e/mocked", testIgnore: /responsive\.spec\.ts/, use: { ...devices["Desktop Chrome"], baseURL: `http://localhost:${MOCK_PORT}`, viewport: { width: 1440, height: 900 } } },
    { name: "mocked-mobile", testDir: "e2e/mocked", testMatch: /responsive\.spec\.ts/, use: { ...devices["Pixel 7"], baseURL: `http://localhost:${MOCK_PORT}` } },
    {
      name: "integration",
      testDir: "e2e/integration",
      timeout: 15 * 60_000,
      use: { ...devices["Desktop Chrome"], baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3010", viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    // The mocked project needs a running web server but no API: the BFF is never reached
    // because the browser requests are intercepted.
    command: `npx next start -p ${MOCK_PORT}`,
    url: `http://localhost:${MOCK_PORT}`,
    reuseExistingServer: !process.env.CI,
    env: { OUTCOMEGUARD_API_URL: "http://127.0.0.1:9" },
  },
});
