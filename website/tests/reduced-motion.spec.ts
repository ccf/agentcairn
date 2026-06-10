import { test, expect } from "@playwright/test";
test("hero diagram renders its final state under reduced motion", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/");
  const diagram = page.getByTestId("hero-diagram");
  await expect(diagram).toBeVisible();
  await expect(diagram.getByText("auth-fix.md")).toBeVisible();
});

test("uninstall demo shows final stage under reduced motion", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/");
  await expect(page.getByTestId("uninstall-demo").getByText(/0 facts lost/)).toBeVisible();
});
