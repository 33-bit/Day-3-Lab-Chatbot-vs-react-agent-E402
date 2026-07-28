import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:5180",
    colorScheme: "light",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command: "python3 -m uvicorn src.api:app --host 127.0.0.1 --port 8010",
      cwd: "..",
      env: { LLM_PROVIDER: "mock" },
      url: "http://127.0.0.1:8010/api/health",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 5180",
      cwd: ".",
      env: { VITE_PROXY_TARGET: "http://127.0.0.1:8010" },
      url: "http://127.0.0.1:5180",
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
