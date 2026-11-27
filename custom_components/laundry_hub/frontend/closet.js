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

export function render(ctx) {
  const { lib, state } = ctx;
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

export function renderSheet(ctx) {
  const { lib, state } = ctx;
  const sheet = state.sheet;
  let inner = "";
  if (sheet.kind === "filters") inner = filtersSheet(ctx);
  else if (sheet.kind === "bulk") inner = bulkSheet(ctx, sheet.field);
  else if (sheet.kind === "setfield") inner = setFieldSheet(ctx);
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
    ctx.state.filters = { q: "", owner: [], location: [], bin: [], needsPhoto: false, needsReview: false, archived: false, tax: {} };
    ctx.render();
  },
  search(ctx, el) { ctx.state.filters.q = el.value; ctx.render(); },
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
