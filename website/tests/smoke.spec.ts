import { test, expect } from "@playwright/test";
test("page renders with brand and nav", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/agentcairn/);
  await expect(page.getByRole("link", { name: /agentcairn/ }).first()).toBeVisible();
});

test("hero shows the inversion headline and install line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("made it your files");
  await expect(page.getByText("uvx agentcairn").first()).toBeVisible();
});

test("inversion + differentiators render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /made it your files/ })).toHaveCount(2); // hero + inversion
  await expect(page.getByText("A free, deterministic graph")).toBeVisible();
});
