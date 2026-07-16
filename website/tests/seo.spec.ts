import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const ROUTES = [
  "/",
  "/agent-memory/",
  "/obsidian-ai-memory/",
  "/alternatives/",
  "/hermes/",
  "/claude-code-memory/",
];

for (const route of ROUTES) {
  test(`${route}: complete, canonical metadata and exactly one H1`, async ({ page }) => {
    const response = await page.goto(route);
    expect(response?.ok()).toBeTruthy();

    const canonical = new URL(route, "https://agentcairn.dev").href;
    const title = await page.title();
    const description = await page.locator('meta[name="description"]').getAttribute("content");
    expect(title.trim().length).toBeGreaterThan(0);
    expect(description?.trim().length).toBeGreaterThan(0);
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", canonical);
    await expect(page.locator('meta[property="og:url"]')).toHaveAttribute("content", canonical);
    await expect(page.locator('meta[property="og:type"]')).toHaveAttribute("content", "website");
    await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute("content", "agentcairn");
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", title);
    await expect(page.locator('meta[property="og:description"]')).toHaveAttribute("content", description!);
    await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", /^https:\/\//);
    await expect(page.locator('meta[name="twitter:title"]')).toHaveAttribute("content", title);
    await expect(page.locator('meta[name="twitter:description"]')).toHaveAttribute("content", description!);
    await expect(page.locator('meta[name="twitter:image"]')).toHaveAttribute("content", /^https:\/\//);
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "index,follow");
  });
}

test("public pages have unique titles and descriptions", async ({ page }) => {
  const titles: string[] = [];
  const descriptions: string[] = [];
  for (const route of ROUTES) {
    await page.goto(route);
    titles.push(await page.title());
    descriptions.push((await page.locator('meta[name="description"]').getAttribute("content")) ?? "");
  }
  expect(new Set(titles).size).toBe(ROUTES.length);
  expect(new Set(descriptions).size).toBe(ROUTES.length);
});

test("two valid JSON-LD blocks (WebSite + SoftwareApplication)", async ({ page }) => {
  await page.goto("/");
  const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
  expect(blocks.length).toBe(2);
  const types = blocks.map((b) => JSON.parse(b)["@type"]);
  expect(types).toContain("WebSite");
  expect(types).toContain("SoftwareApplication");

  const application = blocks.map((b) => JSON.parse(b)).find((b) => b["@type"] === "SoftwareApplication");
  const packageSource = await readFile(
    new URL("../../src/cairn/__init__.py", import.meta.url),
    "utf8",
  );
  const currentVersion = packageSource.match(/^__version__\s*=\s*["']([^"']+)["']/m)?.[1];
  expect(currentVersion).toBeTruthy();
  expect(application).toMatchObject({
    softwareVersion: currentVersion,
    installUrl: "https://pypi.org/project/agentcairn/",
  });
  expect(application.sameAs).toContain("https://github.com/ccf/agentcairn");
  expect(application.sameAs).not.toContain("https://community.obsidian.md/plugins/agentcairn");
});

test("robots.txt and sitemap are served", async ({ request }) => {
  const robots = await request.get("/robots.txt");
  expect(robots.ok()).toBeTruthy();
  expect(await robots.text()).toContain("sitemap-index.xml");
  const sm = await request.get("/sitemap-index.xml");
  expect(sm.ok()).toBeTruthy();
  const xml = await (await request.get("/sitemap-0.xml")).text();
  for (const route of ROUTES) {
    expect(xml).toContain(`<loc>${new URL(route, "https://agentcairn.dev").href}</loc>`);
  }
});

test("Cloudflare static-asset policy preserves fresh HTML and caches fingerprints", async () => {
  const headers = await readFile(new URL("../public/_headers", import.meta.url), "utf8");
  expect(headers).toContain("Strict-Transport-Security: max-age=31536000");
  expect(headers).toContain("Content-Security-Policy:");
  expect(headers).toContain("X-Content-Type-Options: nosniff");
  expect(headers).toMatch(/workers\.dev\/\*[\s\S]*X-Robots-Tag: noindex/);
  expect(headers.split("# Astro fingerprints")[0]).not.toContain("Cache-Control:");
  expect(headers).toMatch(/\/_astro\/\*[\s\S]*Cache-Control: public, max-age=31556952, immutable/);
  expect(headers).toMatch(/\/\*\.png[\s\S]*Cache-Control: public, max-age=86400/);

  const wrangler = await readFile(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  expect(wrangler).toContain('"html_handling": "force-trailing-slash"');
  expect(wrangler).toContain("Always Use HTTPS");
});
