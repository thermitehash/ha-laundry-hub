// Headless smoke test of the Laundry panel (v0.2) against a stub `hass` + websocket.
// Run: node tests/panel/smoke.mjs   (needs the `playwright` npm package + Chromium)
// Screenshots: tests/panel/screenshots/*.png (uploaded as a CI artifact).
import { createRequire } from "node:module";
import { mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.join(here, "../../custom_components/laundry_hub/frontend");
const SHOTS = path.join(here, "screenshots");
mkdirSync(SHOTS, { recursive: true });

const XSS = '<img src=x onerror="window.__xss=1">';
const now = Date.now();
const iso = (minutesAgo) => new Date(now - minutesAgo * 60000).toISOString();
const PIXEL = "data:image/svg+xml;utf8," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="40" height="30" fill="#2a2f8f"/></svg>');

function garment(id, name, extra = {}) {
  return {
    id, uids: [`04A1B2C3D4E5${id.slice(-2).padStart(2, "0")}`], name, owner: "person.heather", wash_bin: "b1", dry_type: "d1",
    notes: "", photos: [], photo_urls: [], state: "put_away", location: "closet_1", last_seen: { time: iso(30), station: "closet_1" },
    last_laundry_tap: null, created: iso(5000), recent_taps: [{ time: iso(30), station: "closet_1", event: "put_away", mode: "auto", repeat: false }],
    lifecycle: "active", photo: null, missing: false, ...extra,
  };
}

function data(wardrobe) {
  const desc = (type, subtype, colors, extra = {}) => ({
    type, subtype, colors: colors.map((name) => ({ name, hex: name === "navy" ? "#1E2A5A" : "#D32F2F" })), pattern: "solid",
    sleeve_length: "long", neckline: "crew", length: "hip", fit: "regular", rise: "n/a", material: ["cotton"], formality: 2,
    warmth: 3, seasons: ["fall", "winter"], occasions: ["casual"], style_tags: ["cozy"], summary: "A soft long-sleeve knit.", ...extra,
  });
  const w = (g, d, more = {}) => (wardrobe ? { ...g, description: d, description_provenance: { type: "ai", fit: "user" }, description_confidence: { type: 0.9 }, description_ai: d, description_model: "ai_task.openai", description_updated: iso(60), brand: "Old Navy", size: { label: "M", system: "letter", cut: "regular", brand_size_note: null }, purchase: { store: "Old Navy", purchased_on: "2025-10-02", price: 39.99, currency: "USD", product_url: "https://example.com/p", order_ref: null, source: "url" }, wear_for: "any", uniform: false, needs_review: false, ...more } : g);
  const garments = [
    w(garment("g01", `Navy cardigan ${XSS}`, { photos: ["g01-a.jpg", "g01-b.jpg"], photo_urls: [{ name: "g01-a.jpg", full: PIXEL, thumb: PIXEL }, { name: "g01-b.jpg", full: PIXEL, thumb: PIXEL }] }), desc("top", "cardigan", ["navy"])),
    w(garment("g02", "Red sweater", { state: "in_wash", location: "laundry" }), desc("top", "sweater", ["red"])),
    w(garment("g03", "Grey hoodie", { missing: true, owner: null }), null, { needs_review: true }),
    w(garment("g04", "Black leggings", { state: "drying", location: "laundry", recent_taps: [{ time: iso(120), station: "laundry", event: "drying", mode: "auto", repeat: false }] }), desc("bottom", "leggings", ["navy"], { rise: "high", sleeve_length: "n/a" })),
    w(garment("g05", "White button-down", { location: "closet_2", wash_bin: "b2" }), desc("top", "button-down", ["navy"])),
  ];
  return {
    garments,
    wash_bins: [
      { id: "b1", type: "wash_bin", name: "Darks", color: [42, 47, 143], light_entity: "light.seg1", order: 0, usage: 4 },
      { id: "b2", type: "wash_bin", name: `Lights ${XSS}`, color: [255, 255, 255], light_entity: null, order: 1, usage: 1 },
    ],
    dry_types: [{ id: "d1", type: "dry_type", name: "Hang rod", color: [0, 200, 255], light_entity: null, order: 0, usage: 5 }],
    stations: ["laundry", "closet_1", "closet_2"],
    station_health: [
      { station: "laundry", configured: true, online: true, last_tap: iso(2) },
      { station: "closet_1", configured: true, online: false, last_tap: iso(400) },
      { station: "closet_2", configured: false, online: null, last_tap: null },
    ],
    last_unknown: { uid: "AABBCCDD", station: "closet_2", time: iso(2) },
    unknown_tags: [{ uid: "AABBCCDD", station: "closet_2", first_seen: iso(2), last_seen: iso(2), count: 1 }],
    missing_after_days: 14,
    entry_id: "e1",
    version: "0.2.0",
    owners: { "person.heather": { work_label: "Work", body_profile: null, schedule: null } },
    wardrobe: { enabled: wardrobe, ai_entity: wardrobe ? "ai_task.openai" : null, currency: "USD", photo_limit: wardrobe ? 8 : 1 },
    follow_ha_theme: false,
    taxonomy: wardrobe ? {
      types: ["top", "bottom", "dress", "shoes"], subtypes: { top: ["cardigan", "sweater", "button-down", "t-shirt"], bottom: ["leggings", "jeans"], dress: ["wrap"], shoes: ["boots"] },
      colors: [{ name: "navy", hex: "#1E2A5A" }, { name: "red", hex: "#D32F2F" }, { name: "white", hex: "#FFFFFF" }],
      pattern: ["solid", "striped"], sleeve_length: ["short", "long", "n/a"], neckline: ["crew", "v-neck", "n/a"], length: ["hip", "ankle", "n/a"],
      fit: ["slim", "regular", "relaxed", "n/a"], rise: ["low", "mid", "high", "n/a"], material: ["cotton", "wool"], seasons: ["spring", "summer", "fall", "winter"],
      occasions: ["work", "casual"], wear_for: ["any", "work", "off_duty", "occasion", "athletic"], size_systems: ["letter", "numeric", "other"],
      size_cuts: ["regular", "petite", "n/a"], lifecycles: ["active"], description_fields: [], review_fields: [], review_confidence: 0.7,
    } : null,
    persons: [{ entity_id: "person.heather", name: "Heather" }, { entity_id: "person.sam", name: "Sam" }],
  };
}

const failures = [];
const fail = (msg) => { console.error("FAIL:", msg); failures.push(msg); };

async function setup(browser, viewport, wardrobe) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.route("http://hub.test/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/laundry") {
      return route.fulfill({ contentType: "text/html", body: "<!doctype html><html><head><meta name=viewport content='width=device-width,initial-scale=1'></head><body style='margin:0'></body></html>" });
    }
    if (url.pathname.startsWith("/laundry_hub_static/")) {
      const file = path.join(FRONTEND, url.pathname.replace("/laundry_hub_static/", ""));
      const type = file.endsWith(".css") ? "text/css" : "text/javascript";
      return route.fulfill({ contentType: type, body: readFileSync(file) });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  await page.goto("http://hub.test/laundry");
  await page.evaluate(async ({ payload }) => {
    window.__calls = [];
    window.__listeners = {};
    const hass = {
      themes: { darkMode: false },
      callWS: async (msg) => {
        window.__calls.push(msg);
        if (msg.type === "laundry_hub/data") return JSON.parse(JSON.stringify(payload));
        if (msg.type === "laundry_hub/garment/create" && msg.uid === "bad") throw { message: "Invalid tag UID bad" };
        if (msg.type === "laundry_hub/garment/create") return { ...payload.garments[0], id: "g01" };
        return { moved: 1, updated: 1 };
      },
      fetchWithAuth: async () => ({ ok: true, json: async () => payload.garments[0] }),
      connection: {
        subscribeMessage: async (cb) => { window.__push = cb; return () => {}; },
        addEventListener: (name, fn) => { (window.__listeners[name] ||= []).push(fn); },
      },
    };
    await import("/laundry_hub_static/laundry-panel.js?v=0.2.0");
    const el = document.createElement("laundry-hub-panel");
    document.body.appendChild(el);
    el.hass = hass;
  }, { payload: data(wardrobe) });
  await page.waitForFunction(() => document.querySelector("laundry-hub-panel")?.shadowRoot?.querySelector("main .page-head"));
  return { context, page, errors };
}

const sr = (page, fn, arg) => page.evaluate(([f, a]) => {
  const root = document.querySelector("laundry-hub-panel").shadowRoot;
  return new Function("root", "arg", f)(root, a);
}, [fn, arg]);
const text = (page) => sr(page, "return root.textContent");
const click = (page, selector) => sr(page, "const el = root.querySelector(arg); if (!el) throw new Error('missing ' + arg); el.click();", selector);
const settle = (page) => page.waitForTimeout(80);
async function noHScroll(page, label) {
  const [sw, w] = await page.evaluate(() => [document.scrollingElement.scrollWidth, window.innerWidth]);
  if (sw > w + 1) fail(`${label}: horizontal scroll (${sw} > ${w})`);
}
async function shot(page, name) { await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false }); }

const browser = await chromium.launch();
for (const [vw, vh, tag] of [[390, 844, "phone"], [1280, 800, "wide"]]) {
  const { context, page, errors } = await setup(browser, { width: vw, height: vh }, true);

  // Home
  let t = await text(page);
  for (const s of ["Laundry", "Closet 1", "Darks", "Drying", "Missing (not seen in 14 days)"]) if (!t.includes(s)) fail(`${tag} home: missing "${s}"`);
  if (await page.evaluate(() => window.__xss)) fail(`${tag}: XSS executed`);
  if (await sr(page, "return !!root.querySelector('img[src=x]')")) fail(`${tag}: unescaped HTML`);
  await noHScroll(page, `${tag} home`);
  await shot(page, `${tag}-home`);

  // Closet + filters + select mode
  await click(page, '[data-act="tab"][data-tab="closet"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Navy cardigan") || !t.includes("5 garments")) fail(`${tag} closet: grid/count wrong`);
  if (!t.includes("1 need review")) fail(`${tag} closet: needs review count`);
  await noHScroll(page, `${tag} closet`);
  await shot(page, `${tag}-closet`);
  await click(page, '[data-act="openSheet"][data-kind="filters"]'); await settle(page);
  if (!(await text(page)).includes("Filters")) fail(`${tag}: filters sheet`);
  await click(page, '[data-act="toggleTax"][data-field="type"][data-value="bottom"]'); await settle(page);
  if (!(await sr(page, "return root.querySelector('[data-act=toggleTax][data-field=subtype]')?.dataset.value"))) fail(`${tag}: subtype cascade`);
  await shot(page, `${tag}-filters`);
  await click(page, '[data-act="closeSheet"].btn'); await settle(page);
  t = await text(page);
  if (!t.includes("1 garment") || t.includes("Navy cardigan")) fail(`${tag}: type filter not applied`);
  await click(page, '.chips.wrap [data-act="toggleTax"][data-value="bottom"]'); await settle(page);
  await sr(page, "const i = root.querySelector('input[data-input=search]'); i.value = 'long cardigan'; i.dispatchEvent(new Event('input', {bubbles:true}));"); await settle(page);
  t = await text(page);
  if (!t.includes("Navy cardigan") || t.includes("Red sweater")) fail(`${tag}: taxonomy search: ${await sr(page, "return [...root.querySelectorAll('.gcard .name')].map(e=>e.textContent).join(',')")}`);
  await click(page, '[data-act="clearFilters"]').catch(() => {});
  await sr(page, "const i = root.querySelector('input[data-input=search]'); i.value = ''; i.dispatchEvent(new Event('input', {bubbles:true}));"); await settle(page);
  await click(page, '[data-act="toggleSelectMode"]'); await settle(page);
  await click(page, '.gcard[data-id="g02"]'); await settle(page);
  t = await text(page);
  if (!t.includes("1 selected") || !t.includes("Move to bin") || !t.includes("Set field")) fail(`${tag}: select mode bar`);
  await noHScroll(page, `${tag} select`);
  await shot(page, `${tag}-select`);
  await click(page, '[data-act="toggleSelectMode"]'); await settle(page);

  // Detail
  await click(page, '.gcard[data-id="g01"]'); await settle(page);
  t = await text(page);
  for (const s of ["In Closet 1", "Recent taps", "Re-describe", "Old Navy", "Purchase"]) if (!t.includes(s)) fail(`${tag} detail: missing "${s}"`);
  await noHScroll(page, `${tag} detail`);
  await shot(page, `${tag}-detail`);
  await click(page, '[data-act="closeGarment"]'); await settle(page);

  // Add: listening, then a live unknown tap fills the UID
  await click(page, '[data-act="tab"][data-tab="add"]'); await settle(page);
  if (!(await text(page)).includes("Waiting for a tag")) fail(`${tag}: add step 1`);
  await shot(page, `${tag}-add`);
  await page.evaluate(() => window.__push({ tap: { event_type: "unknown", uid: "11223344", station: "closet_1" } }));
  await settle(page);
  t = await text(page);
  if (!t.includes("11223344") || !t.includes("Wash bin")) fail(`${tag}: live tap did not fill the UID`);
  const loc = await sr(page, "return root.querySelector('[data-field=location][aria-pressed=true]')?.dataset.value");
  if (loc !== "closet_1") fail(`${tag}: location not prefilled from station (${loc})`);
  await noHScroll(page, `${tag} add basics`);
  await shot(page, `${tag}-add-basics`);
  await sr(page, "root.querySelector('input[name=name]').value = 'Tee'; root.querySelector('form[data-submit=saveBasics]').requestSubmit();");
  await page.waitForTimeout(250);
  const created = await page.evaluate(() => window.__calls.find((c) => c.type === "laundry_hub/garment/create"));
  if (!created || created.uid !== "11223344" || created.location !== "closet_1" || created.wash_bin !== "b1") fail(`${tag}: create payload ${JSON.stringify(created)}`);
  if (!(await text(page)).includes("Photos")) fail(`${tag}: wardrobe step not shown`);

  // Settings
  await click(page, '[data-act="tab"][data-tab="settings"]'); await settle(page);
  t = await text(page);
  for (const s of ["Stations", "Online", "Offline", "Not set up", "Wardrobe catalog"]) if (!t.includes(s)) fail(`${tag} settings: missing "${s}"`);
  await noHScroll(page, `${tag} settings`);
  await shot(page, `${tag}-settings`);

  // Reconnecting pill
  await page.evaluate(() => window.__listeners.disconnected?.forEach((fn) => fn()));
  await settle(page);
  if (!(await text(page)).includes("Reconnecting")) fail(`${tag}: reconnecting pill`);
  await page.evaluate(() => window.__listeners.ready?.forEach((fn) => fn()));
  await settle(page);

  if (errors.length) fail(`${tag}: console/page errors: ${errors.join(" | ")}`);
  await context.close();
}

// Wardrobe off: no wardrobe UI, v0.1 behaviour.
{
  const { context, page, errors } = await setup(browser, { width: 390, height: 844 }, false);
  await click(page, '[data-act="tab"][data-tab="closet"]'); await settle(page);
  const t = await text(page);
  if (t.includes("Filters") || t.includes("Needs review")) fail("wardrobe off: wardrobe chips shown");
  await click(page, '.gcard[data-id="g01"]'); await settle(page);
  const d = await text(page);
  if (d.includes("Re-describe") || d.includes("Purchase")) fail("wardrobe off: wardrobe detail shown");
  await shot(page, "phone-wardrobe-off-detail");
  if (errors.length) fail(`wardrobe off: errors ${errors.join(" | ")}`);
  await context.close();
}

await browser.close();
if (failures.length) { console.error(`${failures.length} failure(s)`); process.exit(1); }
console.log("Panel smoke test passed (phone 390x844 + wide 1280x800, wardrobe on/off)");
