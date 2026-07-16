import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const PAGES = [
  { path: "/obsidian-ai-memory/", h1: /Obsidian vault/i },
  { path: "/agent-memory/", h1: /memory for AI coding agents/i },
  { path: "/alternatives/", h1: /agentcairn vs/i },
  { path: "/hermes/", h1: /agentcairn for Hermes Agent/i },
  { path: "/claude-code-memory/", h1: /Claude Code.*memory|memory.*Claude Code/i },
];

for (const p of PAGES) {
  test(`${p.path}: 200 and one descriptive H1`, async ({ page }) => {
    const resp = await page.goto(p.path);
    expect(resp?.ok()).toBeTruthy();
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toContainText(p.h1);
  });
}

test("all content pages are listed in the sitemap", async ({ request }) => {
  const xml = await (await request.get("/sitemap-0.xml")).text();
  for (const p of PAGES) {
    expect(xml).toContain(`<loc>https://agentcairn.dev${p.path}</loc>`);
  }
});

test("FAQ structured data on concept + comparison pages", async ({ page }) => {
  for (const path of ["/agent-memory/", "/alternatives/"]) {
    await page.goto(path);
    const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
    const types = blocks.map((b) => JSON.parse(b)["@type"]);
    expect(types).toContain("FAQPage");
  }
});

test("Hermes guide uses the installable repository subdirectory and states compatibility", async ({ page }) => {
  await page.goto("/hermes/");
  await expect(
    page.locator("pre").filter({ hasText: "hermes plugins install ccf/agentcairn/integrations/hermes" }),
  ).toBeVisible();
  await expect(page.getByText(/agentcairn currently requires Python 3\.12 or newer/)).toBeVisible();
});

test("Claude importer documents best-effort indexing and the official memory source", async ({ page }) => {
  await page.goto("/claude-code-memory/");
  await expect(page.getByRole("link", { name: /Claude Code.*auto-memory documentation/ })).toHaveAttribute(
    "href",
    "https://code.claude.com/docs/en/memory",
  );
  await expect(page.getByText(/If embedding or reconciliation fails, the CLI warns/)).toBeVisible();
});

test("no critical or serious a11y violations on /alternatives", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/alternatives/");
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(bad, JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length })))).toEqual([]);
  await ctx.close();
});
