import { test, expect } from "@playwright/test";

test("uninstall demo shows the complete proof without staged controls under reduced motion", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/");
  const demo = page.getByTestId("uninstall-demo");
  const stages = demo.locator("[data-stage]");
  await expect(stages).toHaveCount(3);
  for (let index = 0; index < 3; index += 1) {
    await expect(stages.nth(index)).toBeVisible();
  }
  await expect(demo.locator("[data-next-stage]")).toBeHidden();
  await expect(demo.getByText(/same fact recalled/)).toBeVisible();
  await context.close();
});
