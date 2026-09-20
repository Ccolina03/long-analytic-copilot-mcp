/**
 * Records a cinematic demo to demo/sme-network-demo.webm
 * Holds on splash, Jira, discovery rail, mesh hops, and finale.
 */
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");
const outDir = path.join(root, "demo");
const outFile = path.join(outDir, "sme-network-demo.webm");
const base = process.env.DEMO_URL || "http://localhost:3000";

fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: outDir, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();

console.log(`Recording cinematic demo from ${base} …`);
await page.goto(base, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForSelector(".splash-title", { timeout: 30000 });
await page.waitForTimeout(2400);

await page.getByRole("button", { name: "Watch recorded demo" }).click();

await page.waitForSelector(".jira-card", { timeout: 30000 });
await page.waitForTimeout(2800);

// Mesh should be visible under discovery rail; wait for first hop signal too.
await page.waitForSelector(".spot-rail", { timeout: 120000 });
await page.waitForTimeout(2000);
await page.waitForSelector(".signal", { timeout: 120000 }).catch(() => null);
await page.waitForTimeout(3500);

await page.waitForSelector(".finale-panel", { timeout: 240000 });
await page.waitForTimeout(5000);

await context.close();
await browser.close();

const recorded = fs
  .readdirSync(outDir)
  .filter((f) => f.endsWith(".webm"))
  .map((f) => path.join(outDir, f))
  .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];

if (!recorded) {
  console.error("No webm produced");
  process.exit(1);
}

if (path.resolve(recorded) !== path.resolve(outFile)) {
  fs.renameSync(recorded, outFile);
}

const mb = (fs.statSync(outFile).size / (1024 * 1024)).toFixed(2);
console.log(`Saved ${outFile} (${mb} MB)`);
