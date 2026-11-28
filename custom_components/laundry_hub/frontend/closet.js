// Closet: search, filter chips (with counts), photo grid, Select mode with bulk actions.

const LIST_FIELDS = ["colors", "material", "seasons", "occasions"];
const ENUM_FIELDS = ["type", "subtype", "pattern", "sleeve_length", "neckline", "length", "fit", "rise"];
const RANGE_FIELDS = ["formality", "warmth"];
const FACETS = ["type", "subtype", "colors", "sleeve_length", "neckline", "length", "fit", "rise", "material", "pattern", "seasons", "occasions", "wear_for", "uniform", "size"];
const LOCATIONS = ["closet_1", "closet_2", "in_wash", "drying", "new", "missing"];
const BULK_SET_FIELDS = ["wear_for", "uniform", "type", "subtype", "colors", "pattern", "sleeve_length", "neckline", "length", "fit", "rise", "material", "formality", "warmth", "seasons", "occasions"];

const locKey = (g) => (g.state === "put_away" ? g.location : g.state === "in_wash" ? "in_wash" : g.state === "drying" ? "drying" : "new");

// Values a garment has for a facet (always an array of strings).
function values(g, field) {
  const desc = g.description || {};
  if (field === "colors") return (desc.colors || []).map((c) => c.name);
  if (LIST_FIELDS.includes(field)) return desc[field] || [];
  if (ENUM_FIELDS.includes(field)) return desc[field] ? [desc[field]] : [];
  if (field === "wear_for") return [g.wear_for || "any"];
  if (field === "uniform") return g.uniform ? ["yes"] : ["no"];
  if (field === "size") return g.size?.label ? [`${g.size.system}:${g.size.label}`] : [];
  return [];
}

function haystack(ctx, g) {
  const desc = g.description || {};
  const parts = [g.name, g.notes, ...(g.uids || []), g.brand, g.size?.label, g.purchase?.store,
    ctx.personName(g.owner), ctx.zoneName(g.wash_bin), ctx.lib.LOCATION_LABEL[locKey(g)]];
  for (const field of Object.keys(desc)) {
    const value = desc[field];
    if (Array.isArray(value)) parts.push(...value.map((v) => (typeof v === "object" ? v.name : v)));
    else if (value !== null && value !== undefined) parts.push(String(value));
  }
  if (desc.sleeve_length) parts.push(`${desc.sleeve_length} sleeve`);
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function matches(ctx, g, filters, skip = null) {
  const f = filters;
  if (!f.archived && (g.lifecycle || "active") !== "active") return false;
  if (skip !== "owner" && f.owner.length && !f.owner.includes(g.owner || "__none")) return false;
  if (skip !== "location" && f.location.length) {
    const keys = [locKey(g), ...(g.missing ? ["missing"] : [])];
    if (!f.location.some((l) => keys.includes(l))) return false;
  }
  if (skip !== "bin" && f.bin.length && !f.bin.includes(g.wash_bin)) return false;
  if (f.needsPhoto && (g.photos || []).length) return false;
  if (f.needsReview && !g.needs_review) return false;
  for (const [field, wanted] of Object.entries(f.tax || {})) {
    if (field === skip) continue;
    if (RANGE_FIELDS.includes(field)) {
      const value = (g.description || {})[field];
      if (value === null || value === undefined || value < wanted[0] || value > wanted[1]) return false;
    } else if (wanted.length && !values(g, field).some((v) => wanted.includes(v))) {
      return false;
    }
  }
  if (f.q.trim()) {
    const text = haystack(ctx, g);
    if (!f.q.toLowerCase().split(/\s+/).filter(Boolean).every((word) => text.includes(word))) return false;
  }
  return true;
}

function countFor(ctx, field, value) {
  return ctx.data.garments.filter((g) => {
    if (!matches(ctx, g, ctx.state.filters, field)) return false;
    if (field === "owner") return (g.owner || "__none") === value;
    if (field === "location") return locKey(g) === value || (value === "missing" && g.missing);
    if (field === "bin") return g.wash_bin === value;
    return values(g, field).includes(value);
  }).length;
}

const taxCount = (filters) => Object.values(filters.tax || {}).filter((v) => v.length).length;

function chip(ctx, { label, act, attrs = "", on, count, dot }) {
  const { esc } = ctx.lib;
  return `<button type="button" class="chip" data-act="${act}" ${attrs} aria-pressed="${on ? "true" : "false"}">
    ${dot ? `<span class="dot" style="background:${dot}"></span>` : ""}${esc(label)}${count !== undefined ? ` <span class="count">${count}</span>` : ""}${on ? " ✕" : ""}</button>`;
}

function chipRow(ctx) {
  const { lib, data, state } = ctx;
  const f = state.filters;
  const owners = [...new Set(data.garments.map((g) => g.owner).filter(Boolean))];
  const out = [];
  if (ctx.wardrobe && data.purchases && (data.purchases.connected || lib.unmatchedCards(data).length)) {
    out.push(chip(ctx, { label: "Purchases", act: "togglePurchases", on: f.purchases, count: lib.unmatchedCards(data).length }));
  }
  for (const owner of owners) {
    const n = countFor(ctx, "owner", owner);
    if (n || f.owner.includes(owner)) out.push(chip(ctx, { label: ctx.personName(owner), act: "toggleFilter", attrs: `data-field="owner" data-value="${lib.esc(owner)}"`, on: f.owner.includes(owner), count: n }));
  }
  for (const loc of LOCATIONS) {
    const n = countFor(ctx, "location", loc);
    if (n || f.location.includes(loc)) out.push(chip(ctx, { label: lib.LOCATION_LABEL[loc], act: "toggleFilter", attrs: `data-field="location" data-value="${loc}"`, on: f.location.includes(loc), count: n }));
  }
  for (const bin of data.wash_bins) {
    const n = countFor(ctx, "bin", bin.id);
    if (n || f.bin.includes(bin.id)) out.push(chip(ctx, { label: bin.name, act: "toggleFilter", attrs: `data-field="bin" data-value="${bin.id}"`, on: f.bin.includes(bin.id), count: n, dot: lib.rgb(bin.color) }));
  }
  if (ctx.wardrobe) {
    out.push(chip(ctx, { label: "Needs photo", act: "toggleFlag", attrs: 'data-flag="needsPhoto"', on: f.needsPhoto }));
    out.push(chip(ctx, { label: "Needs review", act: "toggleFlag", attrs: 'data-flag="needsReview"', on: f.needsReview }));
    const n = taxCount(f);
    out.push(`<button type="button" class="chip" data-act="openSheet" data-kind="filters" aria-pressed="${n ? "true" : "false"}">${lib.ICON.filter} Filters${n ? ` · ${n}` : ""}</button>`);
  }
  if (data.garments.some((g) => (g.lifecycle || "active") !== "active")) {
    out.push(chip(ctx, { label: "Archived", act: "toggleFlag", attrs: 'data-flag="archived"', on: f.archived }));
  }
  return `<div class="chips" role="group" aria-label="Filters">${out.join("")}</div>`;
}

function activeTaxChips(ctx) {
  const { lib, state } = ctx;
  const chips = [];
  for (const [field, wanted] of Object.entries(state.filters.tax || {})) {
    if (RANGE_FIELDS.includes(field)) {
      chips.push(`<button type="button" class="chip on" data-act="clearTax" data-field="${field}">${lib.FIELD_LABEL[field]} ${wanted[0]}–${wanted[1]} ✕</button>`);
      continue;
    }
    for (const value of wanted) {
      const label = field === "size" ? value.split(":")[1] : field === "wear_for" ? lib.WEAR_FOR_LABEL[value] : field === "uniform" ? (value === "yes" ? "Uniform" : "Not uniform") : lib.capitalize(value);
      chips.push(`<button type="button" class="chip on" data-act="toggleTax" data-field="${field}" data-value="${lib.esc(value)}">${lib.esc(lib.FIELD_LABEL[field] || field)}: ${lib.esc(label)} ✕</button>`);
    }
  }
  return chips.length ? `<div class="chips wrap" aria-label="Active filters">${chips.join("")}</div>` : "";
}

export function card(ctx, g, selectable = false, selected = false) {
  const { lib } = ctx;
  const bin = ctx.zone(g.wash_bin);
  const url = lib.coverThumb(g);
  const where = lib.whereLabel(g, ctx.zoneName);
  const owner = g.owner ? ctx.personName(g.owner) : null;
  return `<button type="button" class="gcard" data-act="${selectable ? "toggleSelect" : "openGarment"}" data-id="${g.id}" ${selectable ? `aria-pressed="${selected}"` : ""} aria-label="${lib.esc(g.name)}, ${lib.esc(where.text)}">
    <div class="media">${url ? `<img src="${lib.esc(url)}" alt="" loading="lazy">` : lib.tile(g, bin)}
      <span class="where ${where.warn ? "warn" : ""}">${lib.esc(where.text)}</span>
      ${selectable ? `<span class="check" aria-hidden="true">${selected ? lib.ICON.check : ""}</span>` : ""}</div>
    <div class="body"><span class="name">${lib.esc(g.name)}</span>
      <span class="meta">${owner ? `<span class="avatar" title="${lib.esc(owner)}">${lib.esc(lib.initials(owner))}</span>` : ""}
        <span class="dot round" style="background:${lib.rgb(bin?.color)}" aria-hidden="true"></span><span>${lib.esc(bin?.name || "—")}</span></span></div>
  </button>`;
}

export function filtered(ctx) {
  return ctx.data.garments.filter((g) => matches(ctx, g, ctx.state.filters)).sort((a, b) => a.name.localeCompare(b.name));
}

// The purchase inbox (Purchases chip): match, dismiss or "not clothing";
// "Archived" shows dismissed and auto-archived cards with Restore.
function uniformOffers(ctx) {
  const { lib, data } = ctx;
  return (data.purchases?.uniform_offers || []).map((o) => `<div class="card row wrap offer">
      <span style="flex:1">${lib.esc(o.count)} matched garment${o.count === 1 ? "" : "s"} from ${lib.esc(o.store)} ${o.count === 1 ? "isn't" : "aren't"} marked as uniform.</span>
      <button type="button" class="btn primary" data-act="markUniform" data-store="${lib.esc(o.store_key)}">Mark all matched garments from ${lib.esc(o.store)} as uniform</button>
      <button type="button" class="btn" data-act="declineUniform" data-store="${lib.esc(o.store_key)}">No thanks</button></div>`).join("");
}

// v0.2.6: one card per item with "×N", purchase history (Split), quantity
// stepper, assignment (with suggestions), Merge, and bulk "Assign to…".
function ownerFilterChips(ctx, cards) {
  const { lib, data, state } = ctx;
  const pick = state.filters.purchaseOwner || "all";
  const count = (o) => cards.filter((c) => (o === "all" ? true : o === "none" ? !c.owner : c.owner === o)).length;
  const opts = [["all", "All"], ["none", "Unassigned"], ...(data.persons || []).map((p) => [p.entity_id, p.name])];
  return `<div class="chips" role="group" aria-label="Assigned to">${opts.map(([v, l]) => `<button type="button" class="chip" data-act="purchaseOwner" data-owner="${lib.esc(v)}" aria-pressed="${pick === v}">${lib.esc(l)} <span class="count">${count(v)}</span></button>`).join("")}</div>`;
}

function historyList(ctx, c) {
  const { lib } = ctx;
  const lines = c.history || [];
  if (lines.length < 2 && Number(c.quantity || 1) < 2) return "";
  return `<ul class="history" aria-label="Purchases">${lines.map((h) => `<li><span>${lib.esc([lib.shortDate(h.order_date), h.store, h.order_ref ? `#${h.order_ref}` : null, lib.money(h.unit_price, h.currency || c.currency), `×${h.quantity || 1}`].filter(Boolean).join(" · "))}</span>
    ${lines.length > 1 ? `<button type="button" class="btn small" data-act="splitPurchase" data-card="${lib.esc(c.id)}" data-line="${lib.esc(h.id)}">Split</button>` : ""}</li>`).join("")}</ul>`;
}

function groupControls(ctx, c) {
  const { lib } = ctx;
  const qty = Number(c.quantity || 1);
  const floor = (c.matches || []).reduce((n, m) => n + Number(m.units || 0), 0);
  return `<div class="row wrap"><span class="sub">Quantity</span>
      <span class="stepper"><button type="button" class="icon-btn" data-act="purchaseQty" data-card="${lib.esc(c.id)}" data-qty="${qty - 1}" aria-label="One fewer" ${qty <= Math.max(1, floor) ? "disabled" : ""}>−</button>
        <span class="value" aria-live="polite">${qty}</span>
        <button type="button" class="icon-btn" data-act="purchaseQty" data-card="${lib.esc(c.id)}" data-qty="${qty + 1}" aria-label="One more">+</button></span>
      ${c.quantity_locked ? `<span class="sub">Set by you</span><button type="button" class="btn small" data-act="purchaseQtyReset" data-card="${lib.esc(c.id)}">Use the emails' count</button>` : ""}
      <span class="spacer"></span>
      <button type="button" class="btn small" data-act="openSheet" data-kind="assignPurchase" data-field="${lib.esc(c.id)}">${c.owner ? `For ${lib.esc(ctx.personName(c.owner))}` : "Assign to…"}</button>
      ${c.suggested_owner ? `<button type="button" class="chip suggest" data-act="assignSuggested" data-card="${lib.esc(c.id)}" data-owner="${lib.esc(c.suggested_owner)}">Suggested: ${lib.esc(ctx.personName(c.suggested_owner))}</button>` : ""}</div>
    ${historyList(ctx, c)}`;
}

function purchasesInbox(ctx) {
  const { lib, data, state } = ctx;
  const status = data.purchases || {};
  const archivedView = Boolean(state.filters.archivedPurchases);
  const archived = status.archived || [];
  const waiting = lib.unmatchedCards(data);
  const pick = state.filters.purchaseOwner || "all";
  const byOwner = (c) => (pick === "all" ? true : pick === "none" ? !c.owner : c.owner === pick);
  const cards = (archivedView ? archived : waiting).filter(byOwner);
  const psel = state.pselect || { on: false, ids: new Set() };
  const line = status.connected
    ? `Checked ${lib.esc(lib.timeAgo(status.last_check))}${status.last_error ? ` · last problem: ${lib.esc(status.last_error)}` : ""}`
    : "Purchase emails aren't connected. Connect them in Laundry Hub → Configure → Purchase emails.";
  const actionsFor = (c) => archivedView
    ? `<div class="row wrap"><span class="sub" style="flex:1">${c.status === "dismissed" ? "Dismissed" : "Archived"} ${lib.esc(lib.timeAgo(c.status_changed))}</span>
        <button type="button" class="btn primary" data-act="restorePurchase" data-card="${lib.esc(c.id)}">Restore</button></div>`
    : `${groupControls(ctx, c)}<div class="row wrap"><button type="button" class="btn primary" data-act="openSheet" data-kind="matchPurchase" data-field="${lib.esc(c.id)}">Match to garment</button>
        <button type="button" class="btn" data-act="openSheet" data-kind="mergePurchase" data-field="${lib.esc(c.id)}">Merge…</button>
        <button type="button" class="btn" data-act="dismissPurchase" data-card="${lib.esc(c.id)}">Dismiss</button>
        <button type="button" class="btn" data-act="notClothing" data-card="${lib.esc(c.id)}">Not clothing</button></div>`;
  const empty = archivedView
    ? `<div class="card empty"><strong>Nothing archived</strong><span class="hint">Dismissed cards and cards archived after a while show up here.</span></div>`
    : `<div class="card empty"><strong>No purchases waiting</strong><span class="hint">New orders from the stores you chose show up here.</span></div>`;
  const item = (c) => psel.on && !archivedView
    ? `<div class="card inbox-item">${lib.purchaseCard(c, { act: "togglePurchaseSelect", pressed: psel.ids.has(c.id), ownerName: ctx.personName })}</div>`
    : `<div class="card inbox-item">${lib.purchaseCard(c, { ownerName: ctx.personName })}${actionsFor(c)}</div>`;
  const list = cards.length ? `<div class="inbox">${cards.map(item).join("")}</div>` : empty;
  const chips = `<div class="chips" role="group" aria-label="Purchase views">
      <button type="button" class="chip" data-act="purchasesView" data-view="new" aria-pressed="${!archivedView}">Waiting <span class="count">${waiting.length}</span></button>
      <button type="button" class="chip" data-act="purchasesView" data-view="archived" aria-pressed="${archivedView}">Archived <span class="count">${archived.length}</span></button>
      ${archivedView ? "" : `<button type="button" class="chip" data-act="togglePurchaseSelectMode" aria-pressed="${psel.on}">${psel.on ? "Done" : "Select"}</button>`}</div>`;
  const bar = psel.on && !archivedView ? `<div class="actionbar" role="region" aria-label="Selection actions">
      <div class="row wrap"><strong>${psel.ids.size} selected</strong><span class="spacer"></span>
      <button type="button" class="chip tall" data-act="openSheet" data-kind="assignPurchase" data-field="__selected" ${psel.ids.size ? "" : "disabled"}>Assign to…</button></div></div>` : "";
  return `${chips}${ownerFilterChips(ctx, archivedView ? archived : waiting)}<div class="row wrap" style="margin:8px 0"><span class="sub" style="flex:1">${line}</span>
      ${status.connected && !archivedView ? `<button type="button" class="btn" data-act="checkPurchases" ${state.busy?.check ? "disabled" : ""}>${state.busy?.check ? "Checking…" : "Check now"}</button>` : ""}</div>
    ${archivedView ? "" : uniformOffers(ctx)}${list}${bar}`;
}

export function render(ctx) {
  const { lib, state } = ctx;
  if (state.filters.purchases && ctx.wardrobe) {
    return `<div class="page-head"><h1>Purchases</h1>
        <button type="button" class="btn" data-act="togglePurchases">${lib.ICON.back}All garments</button></div>
      <span class="hint">From the order emails of the stores you chose. Nothing is matched automatically.</span>${purchasesInbox(ctx)}`;
  }
  const list = filtered(ctx);
  const sel = state.select;
  const review = ctx.wardrobe ? list.filter((g) => g.needs_review).length : 0;
  const grid = list.length
    ? `<div class="grid">${list.map((g) => card(ctx, g, sel.on, sel.ids.has(g.id))).join("")}</div>`
    : `<div class="card empty" style="margin-top:12px"><strong>No garments match</strong><button type="button" class="btn" data-act="clearFilters">Clear filters</button></div>`;
  const bar = sel.on ? `<div class="actionbar" role="region" aria-label="Selection actions">
      <div class="row"><strong>${sel.ids.size} selected</strong><span class="spacer"></span>
        <button type="button" class="link" data-act="selectAll">Select all in this filter</button></div>
      <div class="chips">
        <button type="button" class="chip tall" data-act="openSheet" data-kind="bulk" data-field="wash_bin" ${sel.ids.size ? "" : "disabled"}>Move to bin</button>
        <button type="button" class="chip tall" data-act="openSheet" data-kind="bulk" data-field="dry_type" ${sel.ids.size ? "" : "disabled"}>Change dry type</button>
        <button type="button" class="chip tall" data-act="openSheet" data-kind="bulk" data-field="owner" ${sel.ids.size ? "" : "disabled"}>Change owner</button>
        ${ctx.wardrobe ? `<button type="button" class="chip tall" data-act="openSheet" data-kind="setfield" ${sel.ids.size ? "" : "disabled"}>Set field…</button>` : ""}
        <button type="button" class="chip tall" data-act="openSheet" data-kind="confirmDelete" style="color:var(--hub-problem)" ${sel.ids.size ? "" : "disabled"}>Delete</button>
      </div></div>` : "";
  return `<div class="page-head"><h1>Closet</h1>
      <button type="button" class="btn" data-act="toggleSelectMode" aria-pressed="${sel.on}">${sel.on ? "Done" : "Select"}</button></div>
    <div class="stack">
      <label class="search">${lib.ICON.search}<span class="sr-only">Search garments</span>
        <input type="search" data-input="search" data-keep="search" placeholder="${ctx.wardrobe ? "Search “navy long sleeve”" : "Search name, tag or notes"}" value="${lib.esc(state.filters.q)}"></label>
      ${chipRow(ctx)}${activeTaxChips(ctx)}
      <span class="sub">${list.length} garment${list.length === 1 ? "" : "s"}${ctx.wardrobe && review ? ` · ${review} need review` : ""}</span>
    </div>
    ${grid}
    ${sel.on ? bar : `<button type="button" class="fab" data-act="tab" data-tab="add" aria-label="Add garment">${lib.ICON.plus}</button>`}`;
}

// --- sheets ---------------------------------------------------------------------

function facetOptions(ctx, field) {
  const tax = ctx.data.taxonomy || {};
  if (field === "type") return tax.types || [];
  if (field === "subtype") {
    const types = ctx.state.filters.tax.type || [];
    return types.flatMap((t) => (tax.subtypes || {})[t] || []);
  }
  if (field === "colors") return (tax.colors || []).map((c) => c.name);
  if (field === "wear_for") return tax.wear_for || [];
  if (field === "uniform") return ["yes", "no"];
  if (field === "size") return [...new Set(ctx.data.garments.flatMap((g) => values(g, "size")))].sort();
  return tax[field] || [];
}

function filtersSheet(ctx) {
  const { lib, state } = ctx;
  const tax = ctx.data.taxonomy || {};
  const sections = FACETS.map((field) => {
    if (field === "subtype" && !(state.filters.tax.type || []).length) return "";
    const selected = state.filters.tax[field] || [];
    const options = facetOptions(ctx, field)
      .map((value) => ({ value, n: countFor(ctx, field, value), on: selected.includes(value) }))
      .filter((o) => o.n || o.on);
    if (!options.length) return "";
    const colorHex = Object.fromEntries((tax.colors || []).map((c) => [c.name, c.hex]));
    const label = (v) => (field === "size" ? `${v.split(":")[1]} (${v.split(":")[0]})` : field === "wear_for" ? lib.WEAR_FOR_LABEL[v] : field === "uniform" ? (v === "yes" ? "Uniform" : "Not uniform") : lib.capitalize(v));
    return `<div class="facet"><h3>${lib.esc(lib.FIELD_LABEL[field] || "Size")}</h3><div class="chips wrap">${options.map((o) => `
      <button type="button" class="chip" data-act="toggleTax" data-field="${field}" data-value="${lib.esc(o.value)}" aria-pressed="${o.on}">
        ${field === "colors" ? `<span class="dot round" style="background:${colorHex[o.value]}"></span>` : ""}${lib.esc(label(o.value))} <span class="count">${o.n}</span></button>`).join("")}</div></div>`;
  }).join("");
  const ranges = RANGE_FIELDS.map((field) => {
    const [lo, hi] = state.filters.tax[field] || [1, 5];
    const opts = (sel) => [1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${n === sel ? "selected" : ""}>${n}</option>`).join("");
    return `<div class="facet"><h3>${lib.FIELD_LABEL[field]}</h3><div class="row">
      <label class="field" style="flex:1">From<select class="input" data-change="range" data-field="${field}" data-end="0">${opts(lo)}</select></label>
      <label class="field" style="flex:1">To<select class="input" data-change="range" data-field="${field}" data-end="1">${opts(hi)}</select></label></div></div>`;
  }).join("");
  return `<div class="sheet-head"><h2>Filters</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    ${sections}${ranges}
    <div class="row"><button type="button" class="btn" data-act="clearTax">Clear</button><span class="spacer"></span>
      <button type="button" class="btn primary" data-act="closeSheet">Show ${filtered(ctx).length}</button></div>`;
}

function bulkSheet(ctx, field) {
  const { lib, data } = ctx;
  const opts = field === "owner"
    ? [{ id: "", name: "No owner" }, ...data.persons.map((p) => ({ id: p.entity_id, name: p.name }))]
    : (field === "wash_bin" ? data.wash_bins : data.dry_types);
  const title = { wash_bin: "Move to bin", dry_type: "Change dry type", owner: "Change owner" }[field];
  return `<div class="sheet-head"><h2>${title}</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    <div class="swatches">${opts.map((o) => `<button type="button" class="swatch-btn" data-act="bulkApply" data-field="${field}" data-value="${lib.esc(o.id)}">
      ${o.color ? `<span class="sw" style="background:${lib.rgb(o.color)}"></span>` : field === "owner" ? `<span class="avatar">${lib.esc(lib.initials(o.name))}</span>` : ""}${lib.esc(o.name)}</button>`).join("")}</div>`;
}

function setFieldSheet(ctx) {
  const { lib, state } = ctx;
  const sheet = state.sheet;
  const tax = ctx.data.taxonomy || {};
  if (!sheet.field) {
    return `<div class="sheet-head"><h2>Set field…</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
      <p class="hint" style="margin:0">Applies to ${state.select.ids.size} garment(s). Values you set are locked: re-describing won't change them.</p>
      <div class="chips wrap">${BULK_SET_FIELDS.map((f) => `<button type="button" class="chip tall" data-act="pickSetField" data-field="${f}">${lib.esc(lib.FIELD_LABEL[f])}</button>`).join("")}</div>`;
  }
  const field = sheet.field;
  let options;
  if (field === "uniform") options = [{ v: true, l: "Uniform" }, { v: false, l: "Not uniform" }];
  else if (field === "wear_for") options = (tax.wear_for || []).map((v) => ({ v, l: lib.WEAR_FOR_LABEL[v] }));
  else if (RANGE_FIELDS.includes(field)) options = [1, 2, 3, 4, 5].map((v) => ({ v, l: String(v) }));
  else if (field === "subtype") options = [...new Set(Object.values(tax.subtypes || {}).flat())].map((v) => ({ v, l: lib.capitalize(v) }));
  else if (field === "colors") options = (tax.colors || []).map((c) => ({ v: c.name, l: lib.capitalize(c.name), hex: c.hex }));
  else options = (field === "type" ? tax.types : tax[field] || []).map((v) => ({ v, l: lib.capitalize(v) }));
  const multi = LIST_FIELDS.includes(field);
  const picked = sheet.picked || [];
  return `<div class="sheet-head"><button type="button" class="icon-btn plain" data-act="pickSetField" data-field="" aria-label="Back">${lib.ICON.back}</button>
      <h2 style="flex:1">${lib.esc(lib.FIELD_LABEL[field])}</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    <div class="chips wrap">${options.map((o) => `<button type="button" class="chip tall" data-act="${multi ? "pickSetValue" : "applySetField"}" data-value='${lib.esc(JSON.stringify(o.v))}' aria-pressed="${picked.includes(o.v)}">
      ${o.hex ? `<span class="dot round" style="background:${o.hex}"></span>` : ""}${lib.esc(o.l)}</button>`).join("")}</div>
    ${multi ? `<button type="button" class="btn primary block" data-act="applySetField" data-value='${lib.esc(JSON.stringify(picked))}'>Set ${picked.length || "none"}</button>` : ""}`;
}

function matchSheet(ctx) {
  const { lib, data, state } = ctx;
  const card = (data.purchases?.cards || []).find((c) => c.id === state.sheet.field);
  if (!card) return `<p>That purchase is gone.</p>`;
  const q = (state.sheet.q || "").toLowerCase().trim();
  const garments = data.garments
    .filter((g) => !q || `${g.name} ${g.brand || ""} ${ctx.personName(g.owner)}`.toLowerCase().includes(q))
    .sort((a, b) => a.name.localeCompare(b.name))
    .slice(0, 40);
  const left = Number(card.remaining ?? card.quantity ?? 1);
  const one = state.sheet.units === 1;
  const units = left > 1 ? `<div class="chips" role="group" aria-label="How many">
      <button type="button" class="chip" data-act="matchUnits" data-units="all" aria-pressed="${!one}">All ${left} for one garment</button>
      <button type="button" class="chip" data-act="matchUnits" data-units="1" aria-pressed="${one}">1 of ${left} (one garment each)</button></div>` : "";
  return `<div class="sheet-head"><h2>Match “${lib.esc(card.name)}”</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    ${units}<label class="search">${lib.ICON.search}<span class="sr-only">Search garments</span>
      <input type="search" data-input="matchSearch" data-keep="matchSearch" placeholder="Search garments" value="${lib.esc(state.sheet.q || "")}"></label>
    <div class="stack">${garments.map((g) => `<button type="button" class="pick-row" data-act="matchPurchase" data-card="${lib.esc(card.id)}" data-id="${g.id}">
      <span class="avatar">${lib.esc(lib.initials(g.name))}</span><span style="flex:1;text-align:left">${lib.esc(g.name)}</span><span class="sub">${lib.esc(ctx.personName(g.owner))}</span></button>`).join("") || `<span class="hint">No garments match.</span>`}</div>`;
}

function assignSheet(ctx) {
  const { lib, data, state } = ctx;
  const target = state.sheet.field;
  const bulk = target === "__selected";
  const card = bulk ? null : (data.purchases?.cards || []).find((c) => c.id === target);
  if (!bulk && !card) return `<p>That purchase is gone.</p>`;
  const title = bulk ? `Assign ${state.pselect?.ids?.size || 0} purchases` : `Assign “${lib.esc(card.name)}”`;
  const current = card?.owner ?? null;
  const people = [{ entity_id: "", name: "Unassigned" }, ...(data.persons || [])];
  return `<div class="sheet-head"><h2>${title}</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    <div class="stack">${people.map((p) => `<button type="button" class="pick-row" data-act="assignPurchase" data-owner="${lib.esc(p.entity_id)}" aria-pressed="${(current || "") === p.entity_id}">
      <span class="avatar">${lib.esc(lib.initials(p.name))}</span><span style="flex:1;text-align:left">${lib.esc(p.name)}</span>${card?.suggested_owner === p.entity_id ? `<span class="sub">Suggested</span>` : ""}</button>`).join("")}</div>`;
}

function mergeSheet(ctx) {
  const { lib, data, state } = ctx;
  const card = (data.purchases?.cards || []).find((c) => c.id === state.sheet.field);
  if (!card) return `<p>That purchase is gone.</p>`;
  const others = [...(data.purchases?.cards || []), ...(data.purchases?.archived || [])].filter((c) => c.id !== card.id && c.status !== "matched");
  return `<div class="sheet-head"><h2>Merge “${lib.esc(card.name)}” with…</h2><button type="button" class="icon-btn plain" data-act="closeSheet" aria-label="Close">${lib.ICON.close}</button></div>
    <span class="hint">The two cards become one, with both purchases and quantities.</span>
    <div class="stack">${others.map((o) => lib.purchaseCard(o, { act: "mergePurchase", attrs: `data-other="${lib.esc(o.id)}"` })).join("") || `<span class="hint">No other cards to merge with.</span>`}</div>`;
}

export function renderSheet(ctx) {
  const { lib, state } = ctx;
  const sheet = state.sheet;
  let inner = "";
  if (sheet.kind === "filters") inner = filtersSheet(ctx);
  else if (sheet.kind === "bulk") inner = bulkSheet(ctx, sheet.field);
  else if (sheet.kind === "setfield") inner = setFieldSheet(ctx);
  else if (sheet.kind === "matchPurchase") inner = matchSheet(ctx);
  else if (sheet.kind === "assignPurchase") inner = assignSheet(ctx);
  else if (sheet.kind === "mergePurchase") inner = mergeSheet(ctx);
  else if (sheet.kind === "confirmDelete") {
    inner = `<h2>Delete ${state.select.ids.size} garment(s)?</h2><p class="hint" style="margin:0">Their tags become unknown again. This can't be undone.</p>
      <div class="row"><button type="button" class="btn" data-act="closeSheet">Cancel</button><span class="spacer"></span><button type="button" class="btn primary" style="background:var(--hub-problem)" data-act="bulkDelete">Delete</button></div>`;
  }
  return `<div class="scrim" data-act="closeSheet"></div><div class="sheet" role="dialog" aria-modal="true">${inner}</div>`;
}

// --- actions ---------------------------------------------------------------------

const toggle = (list, value) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

export const actions = {
  openGarment: (ctx, el) => ctx.open(el.dataset.id),
  toggleFilter(ctx, el) {
    const f = ctx.state.filters;
    f[el.dataset.field] = toggle(f[el.dataset.field], el.dataset.value);
    ctx.render();
  },
  toggleFlag(ctx, el) {
    ctx.state.filters[el.dataset.flag] = !ctx.state.filters[el.dataset.flag];
    ctx.render();
  },
  toggleTax(ctx, el) {
    const tax = ctx.state.filters.tax;
    const field = el.dataset.field;
    tax[field] = toggle(tax[field] || [], el.dataset.value);
    if (!tax[field].length) delete tax[field];
    if (field === "type" && !tax.type) delete tax.subtype;
    ctx.render();
  },
  clearTax(ctx, el) {
    if (el.dataset.field) delete ctx.state.filters.tax[el.dataset.field];
    else ctx.state.filters.tax = {};
    ctx.render();
  },
  clearFilters(ctx) {
    ctx.state.filters = { q: "", owner: [], location: [], bin: [], needsPhoto: false, needsReview: false, archived: false, purchases: false, tax: {} };
    ctx.render();
  },
  search(ctx, el) { ctx.state.filters.q = el.value; ctx.render(); },
  togglePurchases(ctx) {
    ctx.state.filters.purchases = !ctx.state.filters.purchases;
    ctx.state.filters.archivedPurchases = false;
    ctx.render();
  },
  purchasesView(ctx, el) { ctx.state.filters.archivedPurchases = el.dataset.view === "archived"; ctx.render(); },
  async restorePurchase(ctx, el) {
    const res = await ctx.ws("purchases/restore", { card_id: el.dataset.card });
    if (res) { ctx.toast(res.photos?.length ? "Restored" : "Restored (no photo)"); ctx.reload(); }
  },
  async markUniform(ctx, el) {
    const res = await ctx.ws("purchases/uniform", { store_key: el.dataset.store, accept: true });
    if (res) { ctx.toast(`Marked ${res.updated} garment(s) as uniform`); ctx.reload(); }
  },
  async declineUniform(ctx, el) {
    if (await ctx.ws("purchases/uniform", { store_key: el.dataset.store, accept: false })) ctx.reload();
  },
  matchSearch(ctx, el) { ctx.state.sheet.q = el.value; ctx.render(); },
  matchUnits(ctx, el) { ctx.state.sheet.units = el.dataset.units === "1" ? 1 : null; ctx.render(); },
  purchaseOwner(ctx, el) { ctx.state.filters.purchaseOwner = el.dataset.owner; ctx.render(); },
  togglePurchaseSelectMode(ctx) {
    const on = !(ctx.state.pselect?.on);
    ctx.state.pselect = { on, ids: new Set() };
    ctx.render();
  },
  togglePurchaseSelect(ctx, el) {
    const ids = ctx.state.pselect.ids;
    if (ids.has(el.dataset.card)) ids.delete(el.dataset.card); else ids.add(el.dataset.card);
    ctx.render();
  },
  async purchaseQty(ctx, el) {
    if (await ctx.ws("purchases/set_quantity", { card_id: el.dataset.card, quantity: Number(el.dataset.qty) })) ctx.reload();
  },
  async purchaseQtyReset(ctx, el) {
    if (await ctx.ws("purchases/set_quantity", { card_id: el.dataset.card, quantity: null })) ctx.reload();
  },
  async splitPurchase(ctx, el) {
    if (await ctx.ws("purchases/split", { card_id: el.dataset.card, purchase_id: el.dataset.line })) { ctx.toast("Split into its own card"); ctx.reload(); }
  },
  async mergePurchase(ctx, el) {
    const res = await ctx.ws("purchases/merge", { card_id: ctx.state.sheet.field, other_id: el.dataset.other });
    if (res) { ctx.toast(`Merged: ×${res.quantity}`); ctx.update({ sheet: null }); ctx.reload(); }
  },
  async assignPurchase(ctx, el) {
    const target = ctx.state.sheet.field;
    const ids = target === "__selected" ? [...(ctx.state.pselect?.ids || [])] : [target];
    const owner = el.dataset.owner || null;
    if (await ctx.ws("purchases/assign", { card_ids: ids, owner })) {
      ctx.toast(owner ? `Assigned to ${ctx.personName(owner)}` : "Unassigned");
      if (target === "__selected") ctx.state.pselect = { on: false, ids: new Set() };
      ctx.update({ sheet: null });
      ctx.reload();
    }
  },
  async assignSuggested(ctx, el) {
    if (await ctx.ws("purchases/assign", { card_id: el.dataset.card, owner: el.dataset.owner })) { ctx.toast(`Assigned to ${ctx.personName(el.dataset.owner)}`); ctx.reload(); }
  },
  async matchPurchase(ctx, el) {
    const units = ctx.state.sheet?.units === 1 ? { units: 1 } : {};
    const res = await ctx.ws("purchases/match", { card_id: el.dataset.card, garment_id: el.dataset.id, ...units });
    if (res) { ctx.toast(`Matched to ${res.name}`); ctx.update({ sheet: null }); ctx.reload(); }
  },
  async dismissPurchase(ctx, el) {
    if (await ctx.ws("purchases/dismiss", { card_id: el.dataset.card })) { ctx.toast("Dismissed"); ctx.reload(); }
  },
  async notClothing(ctx, el) {
    if (await ctx.ws("purchases/dismiss", { card_id: el.dataset.card, not_clothing: true })) {
      ctx.toast("Got it — that product won't show up again");
      ctx.reload();
    }
  },
  async checkPurchases(ctx) {
    ctx.state.busy = { ...(ctx.state.busy || {}), check: true };
    ctx.render();
    const res = await ctx.ws("purchases/check", {});
    ctx.state.busy = { ...(ctx.state.busy || {}), check: false };
    if (res) ctx.toast(res.skipped ? "A check is already running" : `${res.new_cards || 0} new, ${res.updated_cards || 0} updated`);
    ctx.reload();
  },
  range(ctx, el) {
    const field = el.dataset.field;
    const current = ctx.state.filters.tax[field] || [1, 5];
    current[Number(el.dataset.end)] = Number(el.value);
    if (current[0] > current[1]) current.reverse();
    ctx.state.filters.tax[field] = current;
    if (current[0] === 1 && current[1] === 5) delete ctx.state.filters.tax[field];
    ctx.render();
  },
  toggleSelectMode(ctx) {
    ctx.state.select = { on: !ctx.state.select.on, ids: new Set() };
    ctx.render();
  },
  toggleSelect(ctx, el) {
    const ids = ctx.state.select.ids;
    if (ids.has(el.dataset.id)) ids.delete(el.dataset.id); else ids.add(el.dataset.id);
    ctx.render();
  },
  selectAll(ctx) {
    ctx.state.select.ids = new Set(filtered(ctx).map((g) => g.id));
    ctx.render();
  },
  openSheet(ctx, el) { ctx.update({ sheet: { kind: el.dataset.kind, field: el.dataset.field || null, picked: [] } }); },
  closeSheet(ctx) { ctx.update({ sheet: null }); },
  pickSetField(ctx, el) { ctx.update({ sheet: { ...ctx.state.sheet, field: el.dataset.field || null, picked: [] } }); },
  pickSetValue(ctx, el) {
    const value = JSON.parse(el.dataset.value);
    ctx.state.sheet.picked = toggle(ctx.state.sheet.picked || [], value);
    ctx.render();
  },
  async applySetField(ctx, el) {
    const ids = [...ctx.state.select.ids];
    const res = await ctx.ws("garment/set_field", { garment_ids: ids, field: ctx.state.sheet.field, value: JSON.parse(el.dataset.value) });
    if (res) { ctx.toast(`Updated ${res.updated} garment(s)`); ctx.update({ sheet: null }); }
  },
  async bulkApply(ctx, el) {
    const ids = [...ctx.state.select.ids];
    const field = el.dataset.field;
    const res = field === "owner"
      ? await ctx.ws("garment/set_field", { garment_ids: ids, field: "owner", value: el.dataset.value || null })
      : await ctx.ws("reassign", { field, to: el.dataset.value, garment_ids: ids });
    if (res) { ctx.toast(`Updated ${res.moved ?? res.updated} garment(s)`); ctx.update({ sheet: null }); }
  },
  async bulkDelete(ctx) {
    const ids = [...ctx.state.select.ids];
    let done = 0;
    for (const id of ids) if (await ctx.ws("garment/delete", { garment_id: id })) done += 1;
    ctx.state.select = { on: false, ids: new Set() };
    ctx.toast(`Deleted ${done} garment(s)`);
    ctx.update({ sheet: null });
  },
};
