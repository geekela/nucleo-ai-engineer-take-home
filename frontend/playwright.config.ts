import { defineConfig, devices } from "@playwright/test";

// The end-to-end tests run their own copy of the app against e2e/fake_api.py, never the real service.
// Ports are separate from `npm run dev` (8000 / 5173 by default), so both can run at once.
const FAKE_API = 8765;
const API = 8100;
const WEB = 5273;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // one backend and one upload queue are shared by every test
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${WEB}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: `uv run --project ../backend python e2e/fake_api.py --port ${FAKE_API}`,
      url: `http://127.0.0.1:${FAKE_API}/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `uv run --project ../backend uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port ${API}`,
      url: `http://127.0.0.1:${API}/api/status`,
      // The fake allows many uploads a minute, so the suite doesn't sit out the real 10-per-minute window.
      // The 2-at-a-time limit stays as it is, and a test checks it.
      env: { NUCLEO_API_BASE_URL: `http://127.0.0.1:${FAKE_API}`, NUCLEO_API_TOKEN: "e2e-token", NUCLEO_UPLOADS_PER_MINUTE: "600" },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npx vite",
      url: `http://127.0.0.1:${WEB}`,
      env: { VIEWER_API_PORT: String(API), VIEWER_WEB_PORT: String(WEB) },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
