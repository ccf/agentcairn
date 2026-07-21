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

test("homepage chooser states and expanded evidence have no critical or serious violations", async ({ page }) => {
  await page.goto("/");
  const chooser = page.locator("#install");
  const routes = [
    ["Claude Code", "claude-code"],
    ["Codex", "codex"],
    ["Other agents", "other"],
    ["Standalone", "standalone"],
  ] as const;
  for (const [label, id] of routes) {
    await chooser.locator(`label[for="install-route-${id}"]`).click();
    await expect(chooser.getByRole("radio", { name: label, exact: true })).toBeChecked();
    if (label === "Other agents") {
      await chooser.getByLabel("Which host are you setting up?").selectOption("opencode");
    }
    const results = await new AxeBuilder({ page }).include("#install").analyze();
    const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
    expect(bad, `${label}: ${JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length })))}`).toEqual([]);
  }

  await page.getByTestId("benchmark-details").getByText(/Open full benchmark/).click();
  await page.locator("#hosts details").getByText(/Compare every host/).click();
  const expanded = await new AxeBuilder({ page }).include("#measured").include("#hosts").analyze();
  const bad = expanded.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(bad, JSON.stringify(bad.map((v) => ({ id: v.id, nodes: v.nodes.length })))).toEqual([]);
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
