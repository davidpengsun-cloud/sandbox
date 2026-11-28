/* The graph drawing benchmark of cerebro.md §7.5: Cerebro's GraphView with 1,000 nodes and 2,500
   links in Chromium, panned and zoomed, timed frame by frame. Its figures are planning estimates
   for the machine it runs on, not the budget itself, until run on the mid-range laptop §7.5 names.

   Run from portal/ui after npm ci, with Playwright for Node on NODE_PATH (as make test-e2e finds it):
     NODE_PATH=<dir holding playwright> node scripts/graph-benchmark.mjs
   Prints one JSON line. The frame figures to quote are p95, max and frames over 33 ms: the
   median is the headless 60 fps ceiling, since idle frames between mouse steps are counted. */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import os from "node:os";
import { createServer } from "vite";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const root = fileURLToPath(new URL("..", import.meta.url));

const quantile = (values, q) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
};
const summary = (frames) => ({
  frames: frames.length,
  medianFrameMs: +quantile(frames, 0.5).toFixed(1),
  p95FrameMs: +quantile(frames, 0.95).toFixed(1),
  maxFrameMs: +Math.max(...frames).toFixed(1),
  over33Ms: frames.filter((ms) => ms > 33).length,
});

const server = await createServer({ root, configFile: `${root}/vite.config.mjs`, logLevel: "error", server: { port: 0, host: "127.0.0.1" } });
let browser;
try {
  await server.listen();
  const url = `${server.resolvedUrls.local[0]}bench/graph.html`;
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(url);
  await page.waitForFunction(() => window.__bench?.ready && document.querySelectorAll(".react-flow__node").length > 0, null, { timeout: 120_000 });
  await page.waitForTimeout(500);
  const loaded = await page.evaluate(() => ({ sent: window.__bench.sent, layoutMs: Math.round(window.__bench.layoutMs), laidOutMs: Math.round(window.__bench.ready - window.__bench.start) }));
  if (loaded.sent.nodes !== 1000 || loaded.sent.links !== 2500) throw new Error(`expected 1,000 nodes and 2,500 links, drew ${JSON.stringify(loaded.sent)}`);

  const box = await page.locator(".react-flow__pane").boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const measure = async (gesture) => {
    await page.evaluate(() => {
      const frames = (window.__frames = []);
      let last = performance.now();
      const tick = (now) => { frames.push(now - last); last = now; window.__raf = requestAnimationFrame(tick); };
      window.__raf = requestAnimationFrame(tick);
    });
    await gesture();
    return page.evaluate(() => { cancelAnimationFrame(window.__raf); return window.__frames.slice(1); });
  };
  const drawn = () => page.evaluate(() => ({ nodes: document.querySelectorAll(".react-flow__node").length, edges: document.querySelectorAll(".react-flow__edge").length }));

  const atFit = await drawn();
  if (atFit.nodes !== 1000 || atFit.edges !== 2500) throw new Error(`expected 1,000 nodes and 2,500 links on screen at fit, found ${JSON.stringify(atFit)}`);
  const pan = async () => {
    for (let round = 0; round < 3; round += 1) {
      await page.mouse.move(cx, cy);
      await page.mouse.down();
      await page.mouse.move(cx + 300, cy + 150, { steps: 40 });
      await page.mouse.move(cx - 300, cy - 150, { steps: 80 });
      await page.mouse.move(cx, cy, { steps: 40 });
      await page.mouse.up();
    }
  };
  const zoom = async () => {
    await page.mouse.move(cx, cy);
    for (let step = 0; step < 30; step += 1) { await page.mouse.wheel(0, -120); await page.waitForTimeout(16); }
    for (let step = 0; step < 30; step += 1) { await page.mouse.wheel(0, 120); await page.waitForTimeout(16); }
  };
  const panFrames = await measure(pan);
  const zoomFrames = await measure(zoom);
  for (let step = 0; step < 25; step += 1) await page.mouse.wheel(0, -120);
  await page.waitForTimeout(300);
  const zoomedIn = await drawn();
  const panZoomedIn = summary(await measure(pan));

  console.log(JSON.stringify({
    label: "planning estimate, measured on this machine, not the mid-range laptop of cerebro.md §7.5",
    machine: { cpu: os.cpus()[0]?.model, cores: os.cpus().length, memoryGb: Math.round(os.totalmem() / 2 ** 30), platform: `${os.platform()} ${os.release()}`, browser: `headless Chromium ${browser.version()}` },
    graph: loaded.sent,
    layoutInWorkerMs: loaded.layoutMs,
    readToLaidOutMs: loaded.laidOutMs,
    drawnAtFit: atFit,
    drawnZoomedIn: zoomedIn,
    panAtFit: summary(panFrames),
    zoom: summary(zoomFrames),
    panZoomedIn,
  }));
} finally {
  await browser?.close();
  await server.close();
}
