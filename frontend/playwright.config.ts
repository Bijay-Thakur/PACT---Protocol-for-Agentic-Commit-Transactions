import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  use: {
    baseURL: process.env.PACT_BROWSER_BASE_URL || "http://127.0.0.1:13000",
    browserName: "chromium",
    ...(process.env.PACT_BROWSER_CHANNEL ? { channel: process.env.PACT_BROWSER_CHANNEL } : {}),
  },
  reporter: "list",
});
