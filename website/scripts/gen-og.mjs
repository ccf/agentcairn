import { fileURLToPath } from "node:url";
import { stat } from "node:fs/promises";
import sharp from "sharp";

const output = fileURLToPath(new URL("../public/og.png", import.meta.url));

// Keep the social card deterministic and browser-free. The geometry mirrors
// assets/readme/hero.svg, so README, favicon, nav, and link previews share one
// recognizable cairn/vault/index/recall identity.
const svg = String.raw`<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#FAFAF8"/>
  <rect x="2" y="2" width="1196" height="626" rx="28" fill="none" stroke="#DDDCD7" stroke-width="4"/>

  <g transform="translate(58 46)">
    <path d="M2 31C5 23 14 20 29 21c13 1 20 6 18 14-3 6-38 7-43 2-2-2-3-4-2-6Z" fill="#191919"/>
    <path d="M10 19c2-7 10-10 21-9 9 1 13 5 11 12-3 5-28 6-31 1-1-1-2-3-1-4Z" fill="#545450"/>
    <path d="M19 8c2-5 8-7 14-5 5 1 7 5 4 9-3 4-16 4-18 0-1-1-1-3 0-4Z" fill="#8B8B85"/>
  </g>
  <text x="118" y="80" fill="#191919" font-family="Arial, sans-serif" font-size="28" font-weight="700" letter-spacing="-0.7">agentcairn</text>
  <text x="58" y="137" fill="#5F5F5A" font-family="monospace" font-size="17" letter-spacing="1.4">ONE VAULT · EVERY SUPPORTED AGENT · LOCAL-FIRST</text>

  <text x="58" y="229" fill="#191919" font-family="Georgia, serif" font-size="64" letter-spacing="-1.8">One memory across</text>
  <text x="58" y="299" fill="#191919" font-family="Georgia, serif" font-size="64" letter-spacing="-1.8">your coding agents.</text>
  <text x="58" y="354" fill="#191919" font-family="Arial, sans-serif" font-size="29" font-weight="700" letter-spacing="-0.5">Plain Markdown under your control.</text>
  <text x="58" y="402" fill="#5F5F5A" font-family="Arial, sans-serif" font-size="20">Capture once. Inspect the files. Recall anywhere.</text>

  <g transform="translate(58 470)">
    <rect width="310" height="52" rx="13" fill="#FFFFFF" stroke="#D6D6D0" stroke-width="2"/>
    <text x="20" y="33" fill="#2563EB" font-family="monospace" font-size="17">MARKDOWN · DUCKDB · MCP</text>
  </g>
  <text x="58" y="572" fill="#70706A" font-family="monospace" font-size="16">agentcairn.dev</text>

  <path d="M961 192V230" stroke="#317CFF" stroke-width="3" stroke-linecap="round"/>
  <circle cx="961" cy="210" r="5" fill="#E89B3C"/>
  <path d="M961 374V414" stroke="#317CFF" stroke-width="3" stroke-linecap="round"/>
  <circle cx="961" cy="394" r="5" fill="#317CFF"/>

  <g transform="rotate(-1 969 136)">
    <rect x="796" y="74" width="346" height="126" rx="22" fill="#191919"/>
    <text x="824" y="108" fill="#9ABEFF" font-family="monospace" font-size="16" letter-spacing="1">MCP · CITED RECALL</text>
    <text x="824" y="148" fill="#FFFFFF" font-family="Arial, sans-serif" font-size="21" font-weight="700">Current context</text>
    <text x="824" y="177" fill="#C8C8C3" font-family="monospace" font-size="15">↳ auth-fix.md · api</text>
  </g>

  <g transform="rotate(0.8 942 305)">
    <rect x="748" y="230" width="394" height="146" rx="23" fill="#FFFFFF" stroke="#D6D6D0" stroke-width="2"/>
    <text x="777" y="267" fill="#5F5F5A" font-family="monospace" font-size="16" letter-spacing="1">INDEX · REBUILDABLE CACHE</text>
    <text x="777" y="314" fill="#191919" font-family="Arial, sans-serif" font-size="30" font-weight="700">DuckDB</text>
    <text x="777" y="346" fill="#5F5F5A" font-family="monospace" font-size="15">BM25 + vectors + rerank</text>
    <circle cx="1098" cy="340" r="8" fill="#E89B3C"/>
  </g>

  <g transform="rotate(-0.6 916 494)">
    <rect x="690" y="414" width="452" height="158" rx="24" fill="#EAF1FF" stroke="#317CFF" stroke-width="2.5"/>
    <text x="720" y="452" fill="#2563EB" font-family="monospace" font-size="16" letter-spacing="1">VAULT · SOURCE OF TRUTH</text>
    <text x="720" y="498" fill="#191919" font-family="Arial, sans-serif" font-size="27" font-weight="700">Markdown you control</text>
    <text x="720" y="534" fill="#4F4F4A" font-family="monospace" font-size="15">inspect · edit · sync</text>
  </g>
</svg>`;

await sharp(Buffer.from(svg))
  .png({ compressionLevel: 9, palette: true, quality: 100 })
  .toFile(output);

const { size } = await stat(output);
console.log(`OG image written to ${output} (${size} bytes)`);
