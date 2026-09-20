/**
 * Records a ~80s explain+show demo to demo/sme-network-demo.webm
 * Captions animate over a 3× mesh run, then CC0 music is muxed in.
 */
import { chromium } from "playwright";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");
const outDir = path.join(root, "demo");
const outFile = path.join(outDir, "sme-network-demo.webm");
const music = path.join(root, "web/public/demo-music.mp3");
const base = process.env.DEMO_URL || "http://localhost:3000";

fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: outDir, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
const t0 = Date.now();

console.log(`Recording ~80s demo from ${base} …`);
await page.goto(base, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForSelector(".splash-title", { timeout: 30000 });
await page.waitForTimeout(3500);

await page.getByRole("button", { name: "Watch recorded demo" }).click();

await page.waitForSelector(".beat-caption, .jira-card", { timeout: 30000 });
await page.waitForTimeout(1800);

await page.waitForSelector(".finale-panel", { timeout: 150000 });
await page.waitForTimeout(9000);

await context.close();
await browser.close();

const recorded = fs
  .readdirSync(outDir)
  .filter((f) => f.endsWith(".webm") && f !== "sme-network-demo.webm")
  .map((f) => path.join(outDir, f))
  .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];

if (!recorded) {
  console.error("No webm produced");
  process.exit(1);
}

const silent = path.join(outDir, "sme-network-demo.silent.webm");
fs.renameSync(recorded, silent);

if (!fs.existsSync(music)) {
  console.warn("No demo-music.mp3 — saving silent video");
  fs.renameSync(silent, outFile);
} else {
  const ffmpeg = process.env.FFMPEG || "ffmpeg";
  const mixed = path.join(outDir, "sme-network-demo.mixed.webm");
  const r = spawnSync(
    ffmpeg,
    [
      "-y",
      "-hide_banner",
      "-loglevel",
      "error",
      "-i",
      silent,
      "-i",
      music,
      "-map",
      "0:v:0",
      "-map",
      "1:a:0",
      "-c:v",
      "copy",
      "-c:a",
      "libopus",
      "-b:a",
      "96k",
      "-shortest",
      mixed,
    ],
    { encoding: "utf8" },
  );
  if (r.status !== 0) {
    console.error(r.stderr || "ffmpeg mux failed");
    fs.renameSync(silent, outFile);
  } else {
    fs.renameSync(mixed, outFile);
    fs.unlinkSync(silent);
  }
}

const sec = ((Date.now() - t0) / 1000).toFixed(1);
const mb = (fs.statSync(outFile).size / (1024 * 1024)).toFixed(2);
console.log(`Saved ${outFile} (${mb} MB, ~${sec}s wall clock, with music)`);
