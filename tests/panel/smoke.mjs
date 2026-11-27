// Headless smoke test of the Laundry panel against a fake `hass` object.
// Run: node tests/panel/smoke.mjs   (needs the `playwright` npm package + Chromium)
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const js = readFileSync(path.join(here, "../../custom_components/laundry_hub/frontend/laundry-panel.js"), "utf8");

const XSS = '<img src=x onerror="window.__xss=1">';
const data = {
  garments: [
    { id: "g1", uids: ["04A1B2C3D4E5F6"], name: `Hoodie ${XSS}`, owner: "person.alex", wash_bin: "b1", dry_type: "d1",
      notes: XSS, photo: null, state: "in_wash", location: "laundry", last_seen: { time: "2026-11-27T10:00:00+00:00", station: "laundry" },
      created: "2026-11-01T10:00:00+00:00", missing: false },
    { id: "g2", uids: ["11223344"], name: "Socks", owner: null, wash_bin: "b2", dry_type: "d1", notes: "", photo: null,
      state: "put_away", location: "closet_1", last_seen: null, created: "2026-10-01T10:00:00+00:00", missing: true },
  ],
  wash_bins: [
    { id: "b1", type: "wash_bin", name: "Darks", color: [40, 40, 160], light_entity: "light.seg1", order: 0, usage: 1 },
    { id: "b2", type: "wash_bin", name: `Lights ${XSS}`, color: [255, 255, 255], light_entity: null, order: 1, usage: 1 },
  ],
  dry_types: [{ id: "d1", type: "dry_type", name: "Dryer", color: [255, 120, 0], light_entity: null, order: 0, usage: 2 }],
  stations: ["laundry", "closet_1", "closet_2"],
  last_unknown: { uid: "AABBCCDD", station: "closet_2", time: "2026-11-27T11:00:00+00:00" },
  unknown_tags: [{ uid: "AABBCCDD", station: "closet_2", first_seen: "2026-11-27T11:00:00+00:00", last_seen: "2026-11-27T11:00:00+00:00", count: 2 }],
  missing_after_days: 14,
  persons: [{ entity_id: "person.alex", name: "Alex" }],
};

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
await page.setContent("<!doctype html><html><body></body></html>");
await page.addScriptTag({ content: js, type: "module" });
await page.evaluate((data) => {
  window.__calls = [];
  const hass = {
    callWS: async (msg) => {
      window.__calls.push(msg);
      if (msg.type === "laundry_hub/data") return JSON.parse(JSON.stringify(data));
      if (msg.type === "laundry_hub/garment/create" && msg.uid === "bad") throw { message: "Invalid tag UID bad" };
      return { moved: 1 };
    },
    connection: { subscribeMessage: async () => () => {} },
  };
  const el = document.createElement("laundry-hub-panel");
  document.body.appendChild(el);
  el.hass = hass;
}, data);

const root = () => page.locator("laundry-hub-panel");
const text = async () => root().evaluate((el) => el.shadowRoot.textContent);
const click = async (sel) => root().evaluate((el, sel) => el.shadowRoot.querySelector(sel).click(), sel);
const fail = (msg) => { console.error("FAIL:", msg); process.exitCode = 1; };

await page.waitForFunction(() => document.querySelector("laundry-hub-panel")?.shadowRoot.querySelector("table"));
let t = await text();
if (!t.includes("Hoodie") || !t.includes("Socks")) fail("garments not listed");
if (!t.includes("Wash: Darks")) fail("location not rendered");
if (await page.evaluate(() => window.__xss)) fail("XSS executed");
const hasImg = await root().evaluate((el) => !!el.shadowRoot.querySelector("img"));
if (hasImg) fail("unescaped HTML injected");

// Filter by location "missing".
await root().evaluate((el) => { const s = el.shadowRoot.querySelector("#f-location"); s.value = "missing"; s.dispatchEvent(new Event("change")); });
t = await text();
if (t.includes("Hoodie") || !t.includes("Socks")) fail("missing filter wrong");

// Status page.
await click('nav button[data-page="status"]');
t = await text();
for (const s of ["Closet 1", "In the wash", "Drying", "Missing", "Test light"]) if (!t.includes(s)) fail(`status missing ${s}`);

// Enroll page: prefilled UID, error message shown on server error.
await click('nav button[data-page="enroll"]');
const uid = await root().evaluate((el) => el.shadowRoot.querySelector('input[name="uid"]').value);
if (uid !== "AABBCCDD") fail(`enroll uid prefill ${uid}`);
const loc = await root().evaluate((el) => el.shadowRoot.querySelector('select[name="location"]').value);
if (loc !== "closet_2") fail(`enroll location prefill ${loc}`);
await root().evaluate((el) => {
  const f = el.shadowRoot.querySelector("form.garment-form");
  f.querySelector('input[name="uid"]').value = "bad";
  f.querySelector('input[name="name"]').value = "Shirt";
  f.requestSubmit();
});
await page.waitForTimeout(100);
t = await text();
if (!t.includes("Invalid tag UID bad")) fail("server error not shown");
const created = await page.evaluate(() => window.__calls.find((c) => c.type === "laundry_hub/garment/create"));
if (!created || created.name !== "Shirt" || created.location !== "closet_2") fail("create payload wrong");

if (errors.length) fail(`page errors: ${errors.join("; ")}`);
await browser.close();
if (!process.exitCode) console.log("Panel smoke test passed");
