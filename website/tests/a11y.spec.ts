import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const ROUTES = [
  "/",
  "/agent-memory/",
  "/obsidian-ai-memory/",
  "/alternatives/",
  "/hermes/",
  "/claude-code-memory/",
];

test("homepage has no critical or serious accessibility violations at desktop size", async ({ page }) => {
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(
    bad,
    JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length }))),
  ).toEqual([]);
});

for (const route of ROUTES) {
  test(`${route}: no critical or serious accessibility violations at mobile size`, async ({ browser }) => {
    const ctx = await browser.newContext({
      reducedMotion: "reduce",
      viewport: { width: 390, height: 844 },
    });
    const page = await ctx.newPage();
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
    expect(
      bad,
      JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length }))),
    ).toEqual([]);
    await ctx.close();
  });
}
