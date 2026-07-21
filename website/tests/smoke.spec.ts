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

  const targets = page.locator("nav.site-nav > .nav-hit-target, .desktop-nav .nav-hit-target");
  expect(await targets.count()).toBeGreaterThan(0);
  for (const target of await targets.all()) {
    expect(["flex", "inline-flex"]).toContain(
      await target.evaluate((node) => getComputedStyle(node).display),
    );
    const box = await target.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);
  }
});

test("wide content keeps a visible horizontal-scroll cue where it overflows", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await page.goto("/");
  await page.locator("#hosts details").getByText(/Compare every host/).click();
  await page.getByTestId("benchmark-details").getByText(/Open full benchmark/).click();
  await expect(page.locator("#hosts-scroll-hint")).toBeVisible();
  await expect(page.locator("#locomo-scroll-hint")).toBeHidden();

  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(page.locator("#hosts-scroll-hint")).toBeHidden();
  await expect(page.locator("#locomo-scroll-hint")).toBeHidden();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("#locomo-scroll-hint")).toBeVisible();
  const installCommand = page.locator('[data-install-panel="claude-code"] .command-scroll');
  await expect(installCommand.getByText("uvx --from agentcairn cairn install claude-code", { exact: true })).toBeVisible();
  expect(await installCommand.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
  await expect(page.locator('[data-install-panel="claude-code"] .command-scroll-hint')).toBeHidden();
  await page.goto("/alternatives/");
  await expect(page.locator("#comparison-scroll-hint")).toBeVisible();
});

test("copy control announces clipboard failures without hiding the command", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("clipboard denied")) },
    });
  });
  await page.goto("/");
  const chooser = page.locator("#install");
  const command = chooser.getByText("uvx --from agentcairn cairn install claude-code", { exact: true });
  await expect(command).toBeVisible();
  const copy = chooser.getByRole("button", { name: "Copy Claude Code install command" });
  await copy.click();
  await expect(copy).toContainText("copy failed");
  await expect(copy.locator("[data-copy-status]")).toHaveText(
    "Copy failed. Select and copy the command manually.",
  );
  await expect(command).toBeVisible();
});

test("uvx commands render option spacing clearly and copy the exact command", async ({ page }) => {
  const commandText = "uvx --from agentcairn cairn import claude-memory";
  await page.goto("/claude-code-memory/");

  const command = page.getByText(commandText, { exact: true }).first();
  await expect(command).toHaveCSS("font-variant-ligatures", "none");
  await expect(command).toHaveText(commandText);
  await expect(
    page.getByRole("button", { name: `Copy command: ${commandText}`, exact: true }).first(),
  ).toHaveAttribute("data-copy", commandText);
});

test("hero shows the memory-trail promise and a clear install action", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("One memory trail across your connected coding agents");
  const installCta = page.getByRole("link", { name: "Choose your agent" });
  await expect(installCta).toHaveAttribute("href", "#install");
  await expect(page.getByTestId("memory-trail").getByText("memories/staging-deploys-use-blue-green-5b9d7c60.md", { exact: true })).toBeVisible();
  await installCta.click();
  await expect(page).toHaveURL(/#install$/);
  await expect(page.getByRole("heading", { name: "Choose your agent. Get one exact path." })).toBeInViewport();
  await expect(page.locator('[data-install-panel="claude-code"]')).toBeVisible();
});

test("desktop hero and sections keep the centered site frame", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  for (const locator of [page.locator("main > header"), page.locator("#how"), page.locator("footer")]) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThanOrEqual(1099);
    expect(box!.width).toBeLessThanOrEqual(1101);
    expect(box!.x).toBeGreaterThanOrEqual(169);
    expect(box!.x).toBeLessThanOrEqual(171);
  }

  const heading = await page.getByRole("heading", { level: 1 }).boundingBox();
  expect(heading).not.toBeNull();
  expect(heading!.width).toBeGreaterThan(300);
  expect(heading!.height).toBeLessThan(400);

  const copy = await page.locator(".home-hero__copy").boundingBox();
  const trail = await page.getByTestId("memory-trail").boundingBox();
  expect(copy).not.toBeNull();
  expect(trail).not.toBeNull();
  expect(trail!.x).toBeGreaterThan(copy!.x + copy!.width);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileCopy = await page.locator(".home-hero__copy").boundingBox();
  const mobileTrail = await page.getByTestId("memory-trail").boundingBox();
  expect(mobileCopy).not.toBeNull();
  expect(mobileTrail).not.toBeNull();
  expect(mobileTrail!.y).toBeGreaterThan(mobileCopy!.y + mobileCopy!.height);
});

test("content-page headings render punctuation instead of entity source text", async ({ page }) => {
  await page.goto("/agent-memory/");
  await expect(page.getByRole("heading", { name: "What “agent memory” is" })).toBeVisible();
  await expect(page.getByText(/&(?:l|r)dquo;/)).toHaveCount(0);
  const pageTitle = page.getByRole("heading", { level: 1 });
  const sectionTitle = page.getByRole("heading", { level: 2 }).first();
  await expect(pageTitle).toHaveCSS("font-family", /Newsreader/);
  await expect(page.getByRole("heading", { level: 3 }).first()).toHaveCSS("font-family", /Newsreader/);
  const pageTitleSize = Number.parseFloat(await pageTitle.evaluate((node) => getComputedStyle(node).fontSize));
  const sectionTitleSize = Number.parseFloat(await sectionTitle.evaluate((node) => getComputedStyle(node).fontSize));
  expect(pageTitleSize).toBeGreaterThan(sectionTitleSize);
});

test("content-page install CTA resolves to the homepage install section", async ({ page }) => {
  await page.goto("/agent-memory/");
  const installLink = page.getByRole("link", { name: "Choose an install path" });
  await expect(installLink).toHaveAttribute("href", "/#install");
  await installLink.click();
  await expect(page).toHaveURL(/\/#install$/);
  await expect(page.locator("#install")).toBeInViewport();
});

test("canonical-file contract and real vault proof render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /files are canonical/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Readable files" })).toBeVisible();
  await expect(page.getByAltText(/Memory view in Obsidian/)).toBeVisible();
});

test("benchmark takeaway leads, with full evidence available on demand", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/66.2% of answer-bearing turns found in the first five results/)).toBeVisible();
  await page.getByTestId("benchmark-details").getByText(/Open full benchmark/).click();
  await expect(page.getByText("0.662")).toBeVisible();
  await expect(page.getByText(/nomic-embed-text/)).toBeVisible();
});

test("uninstall demo advances through stages", async ({ page }) => {
  await page.goto("/");
  const demo = page.getByTestId("uninstall-demo");
  const stages = demo.locator("[data-stage]");
  const next = demo.locator("[data-next-stage]");
  await expect(stages.nth(0)).toBeVisible();
  await expect(stages.nth(1)).toBeHidden();
  await expect(stages.nth(2)).toBeHidden();
  await next.click();
  await expect(stages.nth(1)).toBeVisible();
  await expect(stages.nth(2)).toBeHidden();
  await expect(next).toHaveText("Recall →");
  await next.click();
  await expect(stages.nth(2)).toBeVisible();
  await expect(demo.getByText(/same fact recalled/)).toBeVisible();
  await expect(next).toHaveText("Proof complete");
  await expect(next).toBeDisabled();
});

test("install chooser presents one recommended route and preserves deeper project links", async ({ page }) => {
  await page.goto("/");
  const chooser = page.locator("#install");
  await expect(chooser.getByRole("radio", { name: "Claude Code", exact: true })).toBeChecked();
  await expect(chooser.locator("[data-install-panel]:visible")).toHaveCount(1);
  await expect(chooser.locator('[data-install-panel="claude-code"]')).toBeVisible();
  await expect(chooser.getByText("uvx --from agentcairn cairn install claude-code", { exact: true })).toBeVisible();
  await chooser.locator('label[for="install-route-standalone"]').click();
  await expect(chooser.getByRole("radio", { name: "Standalone", exact: true })).toBeChecked();
  await expect(chooser.locator("[data-install-panel]:visible")).toHaveCount(1);
  await expect(chooser.locator('[data-install-panel="standalone"]')).toBeVisible();
  await expect(chooser.getByText(/cairn doctor --vault/)).toBeVisible();
  // The removed prior-art section stays gone while the focused comparison page
  // remains discoverable through the footer.
  await expect(page.getByRole("link", { name: /Compare memory approaches/ })).toHaveAttribute(
    "href",
    "/alternatives/",
  );
  await expect(page.getByText("Roadmap & honest status")).toHaveCount(0);
});

test("install chooser switches routes by keyboard and keeps copy data synchronized", async ({ page }) => {
  await page.goto("/");
  const chooser = page.locator("#install");
  const claude = chooser.getByRole("radio", { name: "Claude Code", exact: true });
  await claude.focus();
  await page.keyboard.press("ArrowRight");
  await expect(chooser.getByRole("radio", { name: "Codex", exact: true })).toBeChecked();
  const codexCommand = "uvx --from agentcairn cairn install codex";
  await expect(chooser.getByText(codexCommand, { exact: true })).toBeVisible();
  await expect(chooser.getByRole("button", { name: "Copy Codex install command" })).toHaveAttribute("data-copy", codexCommand);

  await chooser.locator('label[for="install-route-other"]').click();
  await expect(chooser.getByRole("radio", { name: "Other agents", exact: true })).toBeChecked();
  await chooser.getByLabel("Which host are you setting up?").selectOption("opencode");
  const openCodeCommand = "uv tool install agentcairn\ncairn install opencode";
  await expect(chooser.locator('[data-other-install-panel="opencode"] code')).toHaveText(
    openCodeCommand,
  );
  await expect(chooser.getByRole("button", { name: "Copy OpenCode setup command" })).toHaveAttribute("data-copy", openCodeCommand);
  await expect(page.locator("[data-final-install]").getByText("OpenCode", { exact: true })).toBeVisible();
  await expect(page.locator("[data-final-install-command]")).toHaveText(openCodeCommand);
  await expect(page.locator("[data-final-install]").getByRole("button", { name: "Copy OpenCode install command" })).toHaveAttribute("data-copy", openCodeCommand);
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
