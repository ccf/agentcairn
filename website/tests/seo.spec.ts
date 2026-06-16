import { test, expect } from "@playwright/test";

test("head has canonical, absolute OG image, and complete cards", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://agentcairn.dev/");
  await expect(page.locator('meta[property="og:type"]')).toHaveAttribute("content", "website");
  await expect(page.locator('meta[property="og:url"]')).toHaveAttribute("content", "https://agentcairn.dev/");
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", "https://agentcairn.dev/og.png");
  await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute("content", "agentcairn");
  await expect(page.locator('meta[name="twitter:title"]')).toHaveCount(1);
  await expect(page.locator('meta[name="twitter:image"]')).toHaveAttribute("content", "https://agentcairn.dev/og.png");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "index,follow");
});

test("two valid JSON-LD blocks (WebSite + SoftwareApplication)", async ({ page }) => {
  await page.goto("/");
  const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
  expect(blocks.length).toBe(2);
  const types = blocks.map((b) => JSON.parse(b)["@type"]);
  expect(types).toContain("WebSite");
  expect(types).toContain("SoftwareApplication");
});

test("robots.txt and sitemap are served", async ({ request }) => {
  const robots = await request.get("/robots.txt");
  expect(robots.ok()).toBeTruthy();
  expect(await robots.text()).toContain("sitemap-index.xml");
  const sm = await request.get("/sitemap-index.xml");
  expect(sm.ok()).toBeTruthy();
});
