import { test, expect } from "@playwright/test";

const PUBLIC_ROUTES = [
  "/",
  "/agent-memory/",
  "/obsidian-ai-memory/",
  "/alternatives/",
  "/hermes/",
  "/claude-code-memory/",
];

test("page renders with brand and nav", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/agentcairn/);
  await expect(page.getByRole("link", { name: /agentcairn/ }).first()).toBeVisible();
});

test("navigation stays compact at tablet width and returns focus on disclosure actions", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto("/");

  const menu = page.locator("details.mobile-menu");
  const trigger = menu.locator("summary");
  await expect(trigger).toBeVisible();
  const excess = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(excess).toBeLessThanOrEqual(1);
  await trigger.click();
  await expect(menu).toHaveAttribute("open", "");

  await page.keyboard.press("Escape");
  await expect(menu).not.toHaveAttribute("open", "");
  await expect(trigger).toBeFocused();

  await trigger.click();
  await menu.getByRole("link", { name: "How it works" }).click();
  await expect(page.locator("#how")).toBeFocused();
});

test("desktop navigation takes over at the large breakpoint", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/");
  await expect(page.locator("details.mobile-menu summary")).toBeHidden();
  await expect(page.locator("details.desktop-more summary")).toBeVisible();
});

test("copy control announces clipboard failures without hiding the command", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("clipboard denied")) },
    });
  });
  await page.goto("/");
  const command = page.getByText("claude plugin install agentcairn@agentcairn").first();
  await expect(command).toBeVisible();
  const copy = page.getByRole("button", { name: /Copy command:/ }).first();
  await copy.click();
  await expect(copy).toContainText("copy failed");
  await expect(copy.locator("[data-copy-status]")).toHaveText(
    "Copy failed. Select and copy the command manually.",
  );
  await expect(command).toBeVisible();
});

test("hero shows the shared-memory headline and plugin install line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("One memory across your coding agents");
  await expect(page.getByText("claude plugin install agentcairn@agentcairn").first()).toBeVisible();
});

test("inversion + differentiators render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /files are canonical/ })).toBeVisible();
  await expect(page.getByText("A free, deterministic graph")).toBeVisible();
});

test("benchmark table shows the nomic reranker row", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("0.662")).toBeVisible();
  await expect(page.getByText(/nomic-embed-text/)).toBeVisible();
});

test("uninstall demo advances through stages", async ({ page }) => {
  await page.goto("/");
  const demo = page.getByTestId("uninstall-demo");
  await demo.getByRole("button", { name: /Reindex/ }).click();
  await demo.getByRole("button", { name: /Recall/ }).click();
  await expect(demo.getByText(/0 facts lost/)).toBeVisible();
});

test("quickstart renders; removed roadmap/prior-art sections are absent", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("cairn doctor")).toBeVisible();
  // The removed prior-art section stays gone while the focused comparison page
  // remains discoverable through the footer.
  await expect(page.getByRole("link", { name: /Compare memory approaches/ })).toHaveAttribute(
    "href",
    "/alternatives/",
  );
  await expect(page.getByText("Roadmap & honest status")).toHaveCount(0);
});

test("public routes do not create page-level horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  for (const route of PUBLIC_ROUTES) {
    const response = await page.goto(route);
    expect(response?.ok()).toBeTruthy();
    await expect(page.locator("h1")).toBeVisible();
    const excess = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(excess, `${route} has ${excess}px of page-level horizontal overflow`).toBeLessThanOrEqual(1);
  }
});
