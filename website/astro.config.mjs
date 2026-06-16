// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Static site → served by Cloudflare Workers Static Assets (see wrangler.jsonc).
// No SSR, so no adapter is needed (the @astrojs/cloudflare adapter is for
// on-demand rendering and is incompatible with output: "static" under v13).
export default defineConfig({
  output: "static",
  site: "https://agentcairn.dev",
  integrations: [react(), sitemap()],
  vite: { plugins: [tailwindcss()] },
});
