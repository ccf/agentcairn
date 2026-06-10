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
Cloudflare Pages, project root `website/`, build `npm run build`, output `dist/`.
Custom domain `agentcairn.dev`. CI deploys on push to `main` (`.github/workflows/site.yml`).
