import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  webServer: { command: "npm run build && npm run preview", url: "http://localhost:4321", reuseExistingServer: !process.env.CI },
  use: { baseURL: "http://localhost:4321" },
});
