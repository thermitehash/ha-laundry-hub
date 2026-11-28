// Headless smoke test of the Laundry Hub panel (v0.2) against a stub `hass` + websocket.
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
    w(garment("g06", "Linen shirt", { uids: [], tagged: false, location: null, last_seen: null, recent_taps: [], state_source: "manual", state_updated: iso(60 * 24 * 10), state_confidence: 0.7 }), desc("top", "button-down", ["cream"])),
    w(garment("g05", "White button-down", { location: "closet_2", wash_bin: "b2" }), desc("top", "button-down", ["navy"]), { purchase: { store: "Target", purchased_on: "2026-11-20", price: 8, currency: "USD", product_url: "https://target.example/p", order_ref: "T-9", source: "email" } }),
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
    purchases: wardrobe ? {
      connected: true, provider: "gmail", stores: 3, last_check: iso(12), last_error: null, new_cards: 2,
      cleanup: { not_clothing: 31, health: 2, grouped: 10 },
      unreadable: [{ store: "Outdoor Research", date: "2026-11-15T15:00:00+00:00", sender_domain: "outdoorresearch.narvar.com", error: "HomeAssistantError" }],
      cards: [
        { id: "p1", status: "new", store: "Old Navy", store_key: "old_navy", order_ref: "ON-1", order_date: "2026-11-22", currency: "USD", name: `High-Waisted Wow Jeans ${XSS}`, brand: "Old Navy", color: "Black", size: "8 Tall", quantity: 1, unit_price: 44.99, product_url: "https://oldnavy.example/p", photos: ["purchase-p1-a.jpg"], photo_urls: [{ name: "purchase-p1-a.jpg", full: PIXEL, thumb: PIXEL }], garment_id: null, created: iso(60), updated: iso(60), status_changed: iso(60) },
        { id: "p3", status: "new", store: "Blauer", store_key: "blauer", uniform_supplier: true, order_ref: "B-1", order_date: "2026-11-21", currency: "USD", name: "8703 Long Sleeve Shirt", brand: "Blauer", color: "French Blue", size: "L", quantity: 1, unit_price: 64, product_url: null, photos: [], photo_urls: [], garment_id: null, created: iso(100), updated: iso(100), status_changed: iso(100) },
        { id: "p4", status: "new", store: "Amazon", store_key: "amazon", order_ref: "113-2", order_date: "2026-11-19", currency: "USD", name: "RUMIA Dress", brand: "RUMIA", color: "Green", size: "L", cut: "Tall", quantity: 2, remaining: 2, quantity_locked: true, owner: null, suggested_owner: "person.sam", matches: [], unit_price: 39.99, product_url: null, photos: [], photo_urls: [], garment_id: null, created: iso(95), updated: iso(95), status_changed: iso(95),
          history: [{ id: "h1", order_ref: "113-1", order_date: "2026-11-18", store: "Amazon", unit_price: 39.99, currency: "USD", quantity: 1 }, { id: "h2", order_ref: "113-2", order_date: "2026-11-19", store: "Amazon", unit_price: 39.99, currency: "USD", quantity: 1 }] },
        { id: "p2", status: "new", store: "Target", store_key: "target", order_ref: "T-9", order_date: "2026-11-20", currency: "USD", name: "Short Sleeve T-Shirt with a rather long product name that wraps", brand: null, color: "White", size: "XS", quantity: 1, unit_price: 8, product_url: null, photos: [], photo_urls: [], garment_id: null, created: iso(90), updated: iso(90), status_changed: iso(90) },
      ],
      archived: [
        { id: "a1", status: "dismissed", store: "Lands' End", store_key: "lands_end", uniform_supplier: false, order_ref: "LE-1", order_date: "2026-11-10", currency: "USD", name: "Cashmere Scarf", brand: null, color: "Red", size: null, quantity: 1, unit_price: 59.95, product_url: null, photos: [], photo_urls: [], garment_id: null, created: iso(9000), updated: iso(9000), status_changed: iso(30) },
        { id: "a2", status: "archived", store: "Orvis", store_key: "orvis", uniform_supplier: false, order_ref: "O-1", order_date: "2026-09-01", currency: "USD", name: "Flannel Shirt", brand: null, color: null, size: "M", quantity: 1, unit_price: 79, product_url: null, photos: [], photo_urls: [], garment_id: null, created: iso(90000), updated: iso(90000), status_changed: iso(3000) },
      ],
      uniform_offers: [{ store_key: "blauer", store: "Blauer", count: 2 }],
    } : null,
  };
}

const failures = [];
const fail = (msg) => { console.error("FAIL:", msg); failures.push(msg); };

async function setup(browser, viewport, wardrobe, opts = {}) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  const logged = []; // the panel's own "Laundry Hub: <command> failed" logs (handled errors)
  const dialogs = [];
  page.on("dialog", (d) => { dialogs.push(d.message()); d.accept(); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    if (m.text().startsWith("Laundry Hub: ")) logged.push(m.text()); else errors.push(m.text());
  });
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
  await page.evaluate(async ({ payload, opts }) => {
    window.__calls = [];
    window.__listeners = {};
    window.__payload = payload;
    window.__failData = opts.failData || 0; // the first N data calls reject like a reconnect
    window.__slowData = 0;
    const hass = {
      themes: { darkMode: false },
      callWS: async (msg) => {
        window.__calls.push(msg);
        if (msg.type === "laundry_hub/data") {
          if (window.__failData > 0) { window.__failData -= 1; throw { code: 3 }; }
          if (window.__slowData) await new Promise((r) => setTimeout(r, window.__slowData));
          return JSON.parse(JSON.stringify(window.__payload));
        }
        if (msg.type === "laundry_hub/garment/create" && msg.uid === "bad") throw { message: "Invalid tag UID bad" };
        if (msg.type === "laundry_hub/garment/create") return { ...payload.garments[0], id: "g01" };
        if (msg.type === "laundry_hub/garment/attach_tag" && msg.uid === "INUSE" && !msg.move) throw { code: "tag_in_use", message: "That tag belongs to Red sweater; move it here instead?" };
        if (msg.type === "laundry_hub/garment/attach_tag") return msg.uid ? { linked: true, garment: payload.garments[0] } : { linked: false, waiting_until: new Date(Date.now() + 120000).toISOString() };
        if (msg.type === "laundry_hub/garment/wear") return { wear: { id: "w1", at: new Date().toISOString(), source: "manual" }, garment: payload.garments.find((g) => g.id === msg.garment_id) };
        if (msg.type === "laundry_hub/garment/wear_undo") return payload.garments.find((g) => g.id === msg.garment_id);
        if (msg.type === "laundry_hub/unknown/link_candidates") return { garments: payload.garments.filter((g) => g.tagged === false) };
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
  }, { payload: data(wardrobe), opts });
  await page.waitForFunction(() => document.querySelector("laundry-hub-panel")?.shadowRoot?.querySelector("main .page-head"));
  return { context, page, errors, logged, dialogs };
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
  const { context, page, errors, dialogs } = await setup(browser, { width: vw, height: vh }, true);

  // Home
  let t = await text(page);
  for (const s of ["Laundry Hub", "Closet 1", "Darks", "Drying", "Missing (not seen in 14 days)"]) if (!t.includes(s)) fail(`${tag} home: missing "${s}"`);
  // v0.2.8: Purchases is easy to find: a Home card with the count, a Closet tab badge.
  if (!t.includes("Purchases · 4 to review")) fail(`${tag} home: purchases card count`);
  const badgeText = await sr(page, "return root.querySelector('.tabbar [data-tab=closet] .tab-badge, .segmented [data-tab=closet] .tab-badge')?.textContent || ''");
  if (badgeText !== "4") fail(`${tag}: closet tab badge ${badgeText}`);
  await click(page, '[data-act="openPurchases"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Match to garment") || !t.includes("All garments")) fail(`${tag}: home card did not open Purchases`);
  if (!(await page.evaluate(() => window.location.search)).includes("view=purchases")) fail(`${tag}: purchases deep link not written`);
  await click(page, '[data-act="togglePurchases"]'); await settle(page);
  await click(page, '[data-act="tab"][data-tab="home"]'); await settle(page);
  if (await page.evaluate(() => window.__xss)) fail(`${tag}: XSS executed`);
  if (await sr(page, "return !!root.querySelector('img[src=x]')")) fail(`${tag}: unescaped HTML`);
  await noHScroll(page, `${tag} home`);
  await shot(page, `${tag}-home`);

  // Closet + filters + select mode
  await click(page, '[data-act="tab"][data-tab="closet"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Navy cardigan") || !t.includes("6 garments")) fail(`${tag} closet: grid/count wrong`);
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

  // Purchases inbox
  await click(page, '[data-act="togglePurchases"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Match to garment") || !t.includes("Not clothing") || !t.includes("Checked 12 min ago")) fail(`${tag}: purchases inbox`);
  await noHScroll(page, `${tag} purchases`);
  await shot(page, `${tag}-purchases`);
  if (!t.includes("Mark all matched garments from Blauer as uniform")) fail(`${tag}: uniform offer missing`);
  await click(page, '[data-act="markUniform"][data-store="blauer"]'); await settle(page);
  const mu = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/purchases/uniform").pop());
  if (!mu || mu.store_key !== "blauer" || mu.accept !== true) fail(`${tag}: uniform call ${JSON.stringify(mu)}`);
  await click(page, '[data-act="purchasesView"][data-view="archived"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Cashmere Scarf") || !t.includes("Restore") || t.includes("Match to garment")) fail(`${tag}: archived view`);
  const order = await sr(page, "return [...root.querySelectorAll('.inbox .pcard .name')].map(e=>e.textContent).join('|')");
  if (order !== "Cashmere Scarf|Flannel Shirt") fail(`${tag}: archived order ${order}`);
  await shot(page, `${tag}-archived`);
  await click(page, '[data-act="restorePurchase"][data-card="a1"]'); await settle(page);
  const rs = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/purchases/restore").pop());
  if (!rs || rs.card_id !== "a1") fail(`${tag}: restore call`);
  await click(page, '[data-act="purchasesView"][data-view="new"]'); await settle(page);
  // v0.2.6: group card ×N, stepper, history with Split, suggestion, assign, merge, bulk assign.
  t = await text(page);
  for (const s of ["×2", "Set by you", "Suggested: Sam", "113-1", "Split", "Assign to…", "Tall"]) if (!t.includes(s)) fail(`${tag}: group card missing "${s}"`);
  const call = (type) => page.evaluate((ty) => window.__calls.filter((c) => c.type === ty).pop(), type);
  await click(page, '[data-act="purchaseQty"][data-card="p4"][data-qty="3"]'); await settle(page);
  let c = await call("laundry_hub/purchases/set_quantity");
  if (!c || c.card_id !== "p4" || c.quantity !== 3) fail(`${tag}: set_quantity ${JSON.stringify(c)}`);
  await click(page, '[data-act="purchaseQtyReset"][data-card="p4"]'); await settle(page);
  c = await call("laundry_hub/purchases/set_quantity");
  if (!c || c.quantity !== null) fail(`${tag}: unlock ${JSON.stringify(c)}`);
  await click(page, '[data-act="splitPurchase"][data-card="p4"][data-line="h1"]'); await settle(page);
  c = await call("laundry_hub/purchases/split");
  if (!c || c.purchase_id !== "h1") fail(`${tag}: split ${JSON.stringify(c)}`);
  await click(page, '[data-act="assignSuggested"][data-card="p4"]'); await settle(page);
  c = await call("laundry_hub/purchases/assign");
  if (!c || c.card_id !== "p4" || c.owner !== "person.sam") fail(`${tag}: suggested assign ${JSON.stringify(c)}`);
  await click(page, '[data-act="openSheet"][data-kind="assignPurchase"][data-field="p2"]'); await settle(page);
  await click(page, '[data-act="assignPurchase"][data-owner="person.heather"]'); await settle(page);
  c = await call("laundry_hub/purchases/assign");
  if (!c || JSON.stringify(c.card_ids) !== '["p2"]' || c.owner !== "person.heather") fail(`${tag}: assign ${JSON.stringify(c)}`);
  await click(page, '[data-act="openSheet"][data-kind="mergePurchase"][data-field="p4"]'); await settle(page);
  await click(page, '[data-act="mergePurchase"][data-other="p2"]'); await settle(page);
  c = await call("laundry_hub/purchases/merge");
  if (!c || c.card_id !== "p4" || c.other_id !== "p2") fail(`${tag}: merge ${JSON.stringify(c)}`);
  await click(page, '[data-act="togglePurchaseSelectMode"]'); await settle(page);
  await click(page, '[data-act="togglePurchaseSelect"][data-card="p1"]'); await settle(page);
  await click(page, '[data-act="togglePurchaseSelect"][data-card="p3"]'); await settle(page);
  await click(page, '[data-act="openSheet"][data-kind="assignPurchase"][data-field="__selected"]'); await settle(page);
  await click(page, '[data-act="assignPurchase"][data-owner=""]'); await settle(page);
  c = await call("laundry_hub/purchases/assign");
  if (!c || JSON.stringify(c.card_ids) !== '["p1","p3"]' || c.owner !== null) fail(`${tag}: bulk assign ${JSON.stringify(c)}`);
  await click(page, '[data-act="purchaseOwner"][data-owner="none"]'); await settle(page);
  await noHScroll(page, `${tag} purchase groups`);
  await shot(page, `${tag}-purchase-groups`);
  await click(page, '[data-act="purchaseOwner"][data-owner="all"]'); await settle(page);
  await click(page, '[data-act="openSheet"][data-kind="matchPurchase"][data-field="p4"]'); await settle(page);
  await click(page, '[data-act="matchUnits"][data-units="1"]'); await settle(page);
  await click(page, '[data-act="matchPurchase"][data-card="p4"]'); await settle(page);
  c = await call("laundry_hub/purchases/match");
  if (!c || c.card_id !== "p4" || c.units !== 1) fail(`${tag}: match one unit ${JSON.stringify(c)}`);
  await click(page, '[data-act="openSheet"][data-kind="matchPurchase"][data-field="p2"]'); await settle(page);
  await sr(page, "const i = root.querySelector('input[data-input=matchSearch]'); i.value = 'button'; i.dispatchEvent(new Event('input', {bubbles:true}));"); await settle(page);
  const rows = await sr(page, "return [...root.querySelectorAll('[data-act=matchPurchase]')].map(e=>e.dataset.id).join(',')");
  if (rows !== "g05") fail(`${tag}: match search (${rows})`);
  await shot(page, `${tag}-match`);
  await click(page, '[data-act="matchPurchase"][data-id="g05"]'); await settle(page);
  const m = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/purchases/match").pop());
  if (!m || m.card_id !== "p2" || m.garment_id !== "g05") fail(`${tag}: match call ${JSON.stringify(m)}`);
  await click(page, '[data-act="notClothing"][data-card="p2"]'); await settle(page);
  const nc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/purchases/dismiss").pop());
  if (!nc || nc.not_clothing !== true) fail(`${tag}: not clothing call`);
  await click(page, '[data-act="togglePurchases"]'); await settle(page);
  await click(page, '.gcard[data-id="g05"]'); await settle(page);
  if (!(await text(page)).includes("From your Target order")) fail(`${tag}: detail origin line`);
  await click(page, '[data-act="closeGarment"]'); await settle(page);

  // Detail
  await click(page, '.gcard[data-id="g01"]'); await settle(page);
  t = await text(page);
  for (const s of ["In Closet 1", "Recent taps", "Re-describe", "Old Navy", "Purchase"]) if (!t.includes(s)) fail(`${tag} detail: missing "${s}"`);
  await noHScroll(page, `${tag} detail`);
  await shot(page, `${tag}-detail`);
  await click(page, '[data-act="closeGarment"]'); await settle(page);

  // v0.2.8: a garment without a tag: badge, filter, quick actions, attach / move.
  t = await text(page);
  if (!t.includes("No tag") || !t.includes("Tagged")) fail(`${tag}: no-tag badge or filter missing`);
  await click(page, '[data-act="tagFilter"][data-value="untagged"]'); await settle(page);
  const untaggedNames = await sr(page, "return [...root.querySelectorAll('.gcard .name')].map(e=>e.textContent).join('|')");
  if (untaggedNames !== "Linen shirt") fail(`${tag}: No tag filter shows ${untaggedNames}`);
  await click(page, '[data-act="tagFilter"][data-value="untagged"]'); await settle(page);
  await click(page, '.gcard[data-id="g06"]'); await settle(page);
  t = await text(page);
  for (const s of ["To laundry", "Clean / put away", "Attach tag", "Set by hand", "may be out of date"]) if (!t.includes(s)) fail(`${tag} untagged detail: missing "${s}"`);
  await noHScroll(page, `${tag} untagged detail`);
  await shot(page, `${tag}-untagged`);
  await click(page, '[data-act="manualState"][data-action="to_laundry"]'); await settle(page);
  let uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/manual_state").pop());
  if (!uc || uc.garment_id !== "g06" || uc.action !== "to_laundry") fail(`${tag}: manual_state ${JSON.stringify(uc)}`);
  await click(page, '[data-act="attachTag"][data-garment="g06"]'); await settle(page);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/attach_tag").pop());
  if (!uc || uc.garment_id !== "g06" || uc.uid) fail(`${tag}: attach_tag start ${JSON.stringify(uc)}`);
  await page.evaluate(() => { window.__payload.link = { garment_id: "g06", replace: false, expires: new Date(Date.now() + 120000).toISOString() }; window.__push({ changed: true }); });
  await page.waitForTimeout(400);
  if (!(await text(page)).includes("Waiting for a tap")) fail(`${tag}: attach waiting state`);
  await sr(page, "root.querySelector('form[data-submit=typedTag] input[name=uid]').value = 'INUSE'; root.querySelector('form[data-submit=typedTag]').requestSubmit();");
  await page.waitForTimeout(150);
  if (!(await text(page)).includes("Move tag here")) fail(`${tag}: move offer missing`);
  await click(page, '[data-act="moveTag"][data-garment="g06"]'); await settle(page);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/attach_tag").pop());
  if (!uc || uc.uid !== "INUSE" || uc.move !== true) fail(`${tag}: move call ${JSON.stringify(uc)}`);
  await page.evaluate(() => { window.__payload.link = null; window.__push({ changed: true }); });
  await page.waitForTimeout(400);
  // "Move tag here": a picker of tagged garments, one confirm, no typing.
  await click(page, '[data-act="openSheet"][data-kind="moveTag"][data-field="g06"]'); await settle(page);
  if (!(await text(page)).includes("Pick the garment the tag is on now")) fail(`${tag}: move-tag picker`);
  await click(page, '[data-act="pickMoveTag"][data-id="g02"]'); await settle(page);
  if (!dialogs.some((d) => d === "Move tag from Red sweater to Linen shirt?")) fail(`${tag}: move confirm ${dialogs.join(" | ")}`);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/attach_tag").pop());
  if (!uc || uc.garment_id !== "g06" || uc.uid !== "04A1B2C3D4E502" || uc.move !== true) fail(`${tag}: move from picker ${JSON.stringify(uc)}`);
  await click(page, '[data-act="closeGarment"]'); await settle(page);
  await click(page, '.gcard[data-id="g01"]'); await settle(page);
  t = await text(page);
  if (!t.includes("Replace tag") || !t.includes("Remove tag") || !t.includes("Move tag here") || t.includes("Clean / put away")) fail(`${tag}: tagged detail tag section`);
  // "Wore it" on a tagged garment, with Undo on the toast.
  if (!t.includes("Wore it") || !t.includes("Not worn yet")) fail(`${tag}: wore-it on tagged garment`);
  await click(page, '[data-act="woreIt"][data-garment="g01"]'); await page.waitForTimeout(200);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/wear").pop());
  if (!uc || uc.garment_id !== "g01") fail(`${tag}: wear call ${JSON.stringify(uc)}`);
  if (!(await sr(page, "return root.querySelector('.toast .toast-action')?.textContent || ''")).includes("Undo")) fail(`${tag}: no Undo on the wear toast`);
  await click(page, '[data-act="toastAction"]'); await page.waitForTimeout(200);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/wear_undo").pop());
  if (!uc || uc.garment_id !== "g01" || uc.wear_id !== "w1") fail(`${tag}: wear undo ${JSON.stringify(uc)}`);
  await click(page, '[data-act="closeGarment"]'); await settle(page);

  // Add: listening, then a live unknown tap fills the UID
  await click(page, '[data-act="tab"][data-tab="add"]'); await settle(page);
  if (!(await text(page)).includes("Waiting for a tag")) fail(`${tag}: add step 1`);
  await shot(page, `${tag}-add`);
  await page.evaluate(() => window.__push({ tap: { event_type: "unknown", uid: "11223344", station: "closet_1" } }));
  await settle(page);
  t = await text(page);
  if (!t.includes("Is this from a recent order?") || !t.includes("Old Navy") || !t.includes("None of these")) fail(`${tag}: order question not shown`);
  if (await page.evaluate(() => window.__xss)) fail(`${tag}: XSS executed (purchase card)`);
  await noHScroll(page, `${tag} add order`);
  await shot(page, `${tag}-add-order`);
  await click(page, '[data-act="pickOrder"][data-card="p1"]'); await settle(page);
  t = await text(page);
  if (!t.includes("11223344") || !t.includes("Wash bin")) fail(`${tag}: live tap did not fill the UID`);
  if (!t.includes("From your Old Navy order")) fail(`${tag}: order banner missing`);
  const prefilled = await sr(page, "return root.querySelector('input[name=name]').value");
  if (!prefilled.startsWith("High-Waisted Wow Jeans")) fail(`${tag}: name not prefilled from card (${prefilled})`);
  const loc = await sr(page, "return root.querySelector('[data-field=location][aria-pressed=true]')?.dataset.value");
  if (loc !== "closet_1") fail(`${tag}: location not prefilled from station (${loc})`);
  await noHScroll(page, `${tag} add basics`);
  await shot(page, `${tag}-add-basics`);
  await sr(page, "root.querySelector('input[name=name]').value = 'Tee'; root.querySelector('form[data-submit=saveBasics]').requestSubmit();");
  await page.waitForTimeout(250);
  const created = await page.evaluate(() => window.__calls.find((c) => c.type === "laundry_hub/garment/create"));
  const matched = await page.evaluate(() => window.__calls.find((c) => c.type === "laundry_hub/purchases/match" && c.card_id === "p1"));
  if (!matched || matched.card_id !== "p1" || matched.garment_id !== "g01") fail(`${tag}: card not matched after create ${JSON.stringify(matched)}`);
  if (!created || created.uid !== "11223344" || created.location !== "closet_1" || created.wash_bin !== "b1") fail(`${tag}: create payload ${JSON.stringify(created)}`);
  if (!(await text(page)).includes("Photos")) fail(`${tag}: wardrobe step not shown`);
  // v0.2.8: "No tag (wardrobe only)" creates without a uid; a seen tag can link to an untagged garment.
  await click(page, '[data-act="addAnother"]').catch(() => {}); await settle(page);
  await click(page, '[data-act="tab"][data-tab="add"]'); await settle(page);
  t = await text(page);
  if (!t.includes("No tag (wardrobe only)") || !t.includes("Link to an untagged garment")) fail(`${tag}: add no-tag / link options`);
  await click(page, '[data-act="openSheet"][data-kind="linkTag"][data-field="AABBCCDD"]'); await page.waitForTimeout(150);
  await click(page, '[data-act="linkTag"][data-id="g06"]'); await settle(page);
  uc = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/attach_tag").pop());
  if (!uc || uc.uid !== "AABBCCDD" || uc.garment_id !== "g06") fail(`${tag}: link unknown tag ${JSON.stringify(uc)}`);
  await click(page, '[data-act="noTag"]'); await settle(page);
  await click(page, '[data-act="noOrder"]').catch(() => {}); await settle(page);
  if (!(await text(page)).includes("No tag (wardrobe only)")) fail(`${tag}: no-tag basics`);
  await sr(page, "root.querySelector('input[name=name]').value = 'Scarf'; root.querySelector('form[data-submit=saveBasics]').requestSubmit();");
  await page.waitForTimeout(250);
  const untaggedCreate = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/garment/create").pop());
  if (!untaggedCreate || "uid" in untaggedCreate || untaggedCreate.location !== null || untaggedCreate.name !== "Scarf") fail(`${tag}: untagged create ${JSON.stringify(untaggedCreate)}`);

  // Settings
  await click(page, '[data-act="tab"][data-tab="settings"]'); await settle(page);
  t = await text(page);
  for (const s of ["Stations", "Online", "Offline", "Not set up", "Wardrobe catalog"]) if (!t.includes(s)) fail(`${tag} settings: missing "${s}"`);
  const pe = await sr(page, "return root.querySelector('[data-section=purchase-emails]')?.textContent || ''");
  for (const s of ["Connected", "Stores", "3", "Last check", "12 min ago", "New cards", "Last problem", "None", "Change in Configure", "1 email couldn't be read · Outdoor Research", "Retry unreadable emails", "Cleaned up: 31 marked not clothing (restorable), 2 health items removed, 10 duplicates grouped", "Archived cards and their photos are deleted 1 year after archiving."]) if (!pe.includes(s)) fail(`${tag} settings: purchase row missing "${s}"`);
  await click(page, '[data-act="cleanupSeen"]'); await settle(page);
  if (!(await page.evaluate(() => window.__calls.some((c) => c.type === "laundry_hub/purchases/cleanup_seen")))) fail(`${tag}: cleanup_seen call`);
  // v0.2.7: the AI was unavailable after the retries: a note and "Reclassify old cards".
  await page.evaluate(() => { window.__payload.purchases.cleanup = { skipped: true }; window.__push({ changed: true }); });
  await page.waitForTimeout(700); await settle(page);
  const note = await sr(page, "return root.querySelector('[data-section=purchase-cleanup]')?.textContent || ''");
  if (!note.includes("Cleanup skipped: AI unavailable — use Reclassify old cards")) fail(`${tag}: cleanup skipped note ${note}`);
  await click(page, '[data-act="reclassify"]'); await settle(page);
  if (!(await page.evaluate(() => window.__calls.some((c) => c.type === "laundry_hub/purchases/reclassify")))) fail(`${tag}: reclassify call`);
  await page.evaluate(() => { window.__payload.purchases.cleanup = { not_clothing: 31, health: 2, grouped: 10 }; });
  await click(page, '[data-act="retryUnreadable"]'); await settle(page);
  if (!(await page.evaluate(() => window.__calls.some((c) => c.type === "laundry_hub/purchases/retry_unreadable")))) fail(`${tag}: retry unreadable call`);
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

// v0.2.8: error text never shows "[object Object]"; loads never overlap; a load
// during a reconnect retries quietly; ?tab=closet&view=purchases opens Purchases.
{
  const { context, page, errors, logged } = await setup(browser, { width: 390, height: 844 }, true, { failData: 2 });
  // The first two data calls failed like a reconnect: no toast, then the data.
  if (await sr(page, "return Boolean(root.querySelector('.toast'))")) fail("reconnect at startup: a toast was shown");
  const dataCalls = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/data").length);
  if (dataCalls !== 3) fail(`reconnect at startup: ${dataCalls} data calls (want 3)`);
  if (!logged.some((l) => l.includes("laundry_hub/data failed"))) fail("reconnect at startup: raw error not logged");
  if (!(await text(page)).includes("Purchases · 4 to review")) fail("reconnect at startup: data not shown");
  // errorText covers HA's message-less rejections.
  const texts = await page.evaluate(async () => {
    const lib = await import("/laundry_hub_static/lib.js?v=0.2.0");
    return [
      lib.errorText({ code: 3 }),
      lib.errorText({ error: { code: "not_found", message: "Nested message" } }),
      lib.errorText({ code: "unknown_command" }),
      lib.errorText({ foo: { bar: 1 } }),
      lib.errorText({}),
      lib.errorText(new Error("Plain error")),
      lib.errorText({ message: { nested: true } }),
      String(lib.isTransient({ code: 3 })),
    ];
  });
  const want = ["Lost connection to Home Assistant", "Nested message", "Laundry Hub is still starting"];
  want.forEach((w, i) => { if (texts[i] !== w) fail(`errorText case ${i}: ${texts[i]}`); });
  if (texts.some((x) => x.includes("[object Object]"))) fail(`errorText shows [object Object]: ${texts.join(" | ")}`);
  if (texts[4] !== "Something went wrong" || texts[5] !== "Plain error" || texts[7] !== "true") fail(`errorText fallbacks: ${texts.join(" | ")}`);
  // Overlapping loads (init, "ready", change events) collapse to one call.
  const before = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/data").length);
  await page.evaluate(async () => {
    window.__slowData = 300;
    const el = document.querySelector("laundry-hub-panel");
    const a = el._load();
    (window.__listeners.ready || []).forEach((fn) => fn());
    const b = el._load();
    await Promise.all([a, b]);
    window.__slowData = 0;
  });
  const after = await page.evaluate(() => window.__calls.filter((c) => c.type === "laundry_hub/data").length);
  if (after - before !== 1) fail(`overlapping loads made ${after - before} calls`);
  if (errors.length) fail(`startup/error tests: errors ${errors.join(" | ")}`);
  await context.close();
}
{
  const { context, page, errors } = await setup(browser, { width: 390, height: 844 }, true);
  await page.goto("http://hub.test/laundry?tab=closet&view=purchases");
  await page.evaluate(async ({ payload }) => {
    const hass = {
      themes: { darkMode: false },
      callWS: async (msg) => (msg.type === "laundry_hub/data" ? JSON.parse(JSON.stringify(payload)) : {}),
      fetchWithAuth: async () => ({ ok: true, json: async () => ({}) }),
      connection: { subscribeMessage: async () => () => {}, addEventListener: () => {} },
    };
    await import("/laundry_hub_static/laundry-panel.js?v=0.2.0");
    const el = document.createElement("laundry-hub-panel");
    document.body.appendChild(el);
    el.hass = hass;
  }, { payload: data(true) });
  await page.waitForFunction(() => document.querySelector("laundry-hub-panel")?.shadowRoot?.querySelector("main .page-head"));
  await settle(page);
  if (!(await text(page)).includes("Match to garment")) fail("deep link ?tab=closet&view=purchases did not open Purchases");
  if (errors.length) fail(`deep link: errors ${errors.join(" | ")}`);
  await context.close();
}

// HEIC: converted to JPEG in the browser; undecodable HEIC shows the friendly message.
{
  const { context, page, errors } = await setup(browser, { width: 390, height: 844 }, true);
  const result = await page.evaluate(async () => {
    const lib = await import("/laundry_hub_static/lib.js?v=0.2.0");
    const out = {};
    out.detect = [
      lib.isHeic(new File(["x"], "IMG_1.HEIC", { type: "" })),
      lib.isHeic(new File(["x"], "a.jpg", { type: "image/heif" })),
      lib.isHeic(new File(["x"], "a.jpg", { type: "image/jpeg" })),
    ];
    // Chromium can't decode HEIC: the upload is skipped with the friendly message.
    const panel = document.querySelector("laundry-hub-panel");
    window.__fetches = 0;
    const realFetch = panel.hass.fetchWithAuth;
    panel.hass.fetchWithAuth = async (...args) => { window.__fetches += 1; return realFetch(...args); };
    await panel.upload("g01", [new File(["not really heic"], "IMG_2.HEIC", { type: "image/heic" })]);
    out.toast = panel.shadowRoot.querySelector(".toast")?.textContent || "";
    out.fetchesAfterBad = window.__fetches;
    // A browser that can decode it (Safari): stand in with a decodable bitmap.
    const canvas = document.createElement("canvas");
    canvas.width = 30; canvas.height = 20;
    const real = window.createImageBitmap.bind(window);
    window.createImageBitmap = async () => real(canvas);
    const jpeg = await lib.prepareUpload(new File(["heic bytes"], "IMG_3.heic", { type: "image/heic" }));
    out.converted = [jpeg.type, jpeg.name, jpeg.size > 0];
    await panel.upload("g01", [new File(["heic bytes"], "IMG_4.HEIC", { type: "" })]);
    out.fetchesAfterGood = window.__fetches;
    window.createImageBitmap = real;
    return out;
  });
  if (JSON.stringify(result.detect) !== "[true,true,false]") fail(`heic: detection ${JSON.stringify(result.detect)}`);
  if (!result.toast.includes("Most Compatible")) fail(`heic: unsupported message missing (${result.toast})`);
  if (result.fetchesAfterBad !== 0) fail("heic: undecodable HEIC was uploaded");
  if (JSON.stringify(result.converted) !== '["image/jpeg","IMG_3.jpg",true]') fail(`heic: conversion ${JSON.stringify(result.converted)}`);
  if (result.fetchesAfterGood !== 1) fail("heic: converted photo not uploaded");
  const real = errors.filter((e) => !e.includes("Most Compatible"));
  if (real.length) fail(`heic: errors ${real.join(" | ")}`);
  await context.close();
}

await browser.close();
if (failures.length) { console.error(`${failures.length} failure(s)`); process.exit(1); }
console.log("Panel smoke test passed (phone 390x844 + wide 1280x800, wardrobe on/off)");
