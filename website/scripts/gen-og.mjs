import { chromium } from "@playwright/test";

const html = `<!doctype html><html><body style="margin:0;width:1200px;height:630px;background:#fff;color:#191919;font-family:Georgia,serif;display:flex;flex-direction:column;justify-content:center;padding:80px;box-sizing:border-box">
  <div style="font-size:40px;font-family:system-ui">🪨 agentcairn</div>
  <div style="font-size:56px;font-weight:500;line-height:1.15;margin-top:24px;max-width:18ch">Local-first memory for AI agents — your files are the source of truth.</div>
  <div style="font-family:ui-monospace,monospace;font-size:24px;color:#2563eb;margin-top:40px">$ uvx agentcairn</div>
</body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.setContent(html);
await page.screenshot({ path: "public/og.png" });
await browser.close();
console.log("OG image written to public/og.png");
