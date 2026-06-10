import { test, expect } from "@playwright/test";
test("page renders with brand and nav", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/agentcairn/);
  await expect(page.getByRole("link", { name: /agentcairn/ }).first()).toBeVisible();
});
