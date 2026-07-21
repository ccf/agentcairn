import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  // Serve the built static output directly. `astro preview` is overridden by the
  // Cloudflare adapter to a workerd/wrangler runtime that doesn't bind :4321 here,
  // so we serve dist/ with a plain static server (the site is static; the Worker
  // just serves these same assets in production).
  // Keep tests on their own port so a developer's Astro server (and its dev
  // toolbar) can never be mistaken for the production-static test target.
  webServer: { command: "npm run build && python3 -m http.server 4173 --directory dist", url: "http://localhost:4173", reuseExistingServer: false },
  use: { baseURL: "http://localhost:4173" },
});
