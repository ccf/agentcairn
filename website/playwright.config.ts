import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  // Serve the built static output directly. `astro preview` is overridden by the
  // Cloudflare adapter to a workerd/wrangler runtime that doesn't bind :4321 here,
  // so we serve dist/ with a plain static server (the site is static; the Worker
  // just serves these same assets in production).
  webServer: { command: "npm run build && python3 -m http.server 4321 --directory dist", url: "http://localhost:4321", reuseExistingServer: !process.env.CI },
  use: { baseURL: "http://localhost:4321" },
});
