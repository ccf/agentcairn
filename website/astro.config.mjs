// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Static site → production is served by Cloudflare Pages Git integration.
// wrangler.jsonc provides the equivalent optional Workers Static Assets path.
// No SSR, so no adapter is needed (the @astrojs/cloudflare adapter is for
// on-demand rendering and is incompatible with output: "static" under v13).
export default defineConfig({
  output: "static",
  site: "https://agentcairn.dev",
  trailingSlash: "always",
  integrations: [sitemap()],
  vite: { plugins: [tailwindcss()] },
});
