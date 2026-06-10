# agentcairn website

Static Astro site for agentcairn (the landing page at agentcairn.dev).

## Develop
```bash
cd website && npm install && npm run dev   # http://localhost:4321
```

## Build & test
```bash
npm run build      # -> dist/
npm run check      # astro/TS check
npm test           # Playwright e2e (smoke, reduced-motion, a11y)
```

## Deploy
Deployed via **Cloudflare Pages Git integration** (connect the repo in the Cloudflare
dashboard): root directory `website`, build command `npm run build`, output directory `dist`,
custom domain `agentcairn.dev`. Cloudflare builds + deploys on push to `main` and creates
preview URLs for pull requests — no GitHub secrets required.

CI (`.github/workflows/site.yml`) is the **test gate only** (`astro check` + build +
Playwright/axe on PRs and `main`); it does not deploy.
