import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const PAGES = [
  { path: "/claude-code-memory", h1: /Persistent memory for Claude Code/i },
  { path: "/cursor-memory", h1: /memory for Cursor/i },
  { path: "/obsidian-ai-memory", h1: /Obsidian vault/i },
  { path: "/agent-memory", h1: /memory for AI coding agents/i },
  { path: "/alternatives", h1: /agentcairn vs/i },
];

for (const p of PAGES) {
  test(`${p.path}: 200, one H1, unique title, canonical`, async ({ page }) => {
    const resp = await page.goto(p.path);
    expect(resp?.ok()).toBeTruthy();
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toContainText(p.h1);
    await expect(page).toHaveTitle(/agentcairn/i);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href",
      new RegExp(`https://agentcairn\\.dev${p.path}/?$`),
    );
  });
}

test("all content pages are listed in the sitemap", async ({ request }) => {
  const xml = await (await request.get("/sitemap-0.xml")).text();
  for (const p of PAGES) expect(xml).toContain(`https://agentcairn.dev${p.path}`);
});

test("FAQ structured data on concept + comparison pages", async ({ page }) => {
  for (const path of ["/agent-memory", "/alternatives"]) {
    await page.goto(path);
    const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
    const types = blocks.map((b) => JSON.parse(b)["@type"]);
    expect(types).toContain("FAQPage");
  }
});

test("no critical or serious a11y violations on /alternatives", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/alternatives");
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(bad, JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length })))).toEqual([]);
  await ctx.close();
});
