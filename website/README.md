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

The custom domain also requires the Cloudflare zone setting **SSL/TLS → Edge
Certificates → Always Use HTTPS**. Static-asset redirect rules cannot match a
request protocol, so this setting is what protects the first plain-HTTP request;
`public/_headers` applies HSTS and the remaining security policy after HTTPS is
reached. Verify both after deployment:

```bash
curl -sI http://agentcairn.dev/ | head -n 1       # expect 301/302/307/308
curl -sI https://agentcairn.dev/ | grep -i strict-transport-security
```

CI (`.github/workflows/site.yml`) is the **test gate only** (`astro check` + build +
Playwright/axe on PRs and `main`); it does not deploy.

`wrangler.jsonc` and `npm run deploy` provide an optional manual deployment to
Cloudflare Workers Static Assets. They mirror the same `dist/` output and header
policy, but they are not the production deployment source of truth while the
Pages Git integration is enabled.
