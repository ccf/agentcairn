// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

import cloudflare from "@astrojs/cloudflare";

export default defineConfig({
  output: "static",
  site: "https://agentcairn.dev",
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
  adapter: cloudflare()
});