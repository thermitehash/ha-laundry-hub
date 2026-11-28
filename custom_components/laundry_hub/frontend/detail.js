// Garment detail: photo carousel, status, bin/dry type, wardrobe details and
// description (with per-field locks and "Reset to AI"), recent taps, ⋯ menu.

const DESC_SINGLE = ["type", "subtype", "pattern", "sleeve_length", "neckline", "length", "fit", "rise"];
const DESC_MULTI = ["colors", "material", "seasons", "occasions"];
const DESC_SCALE = ["formality", "warmth"];
const EVENT_LABEL = { in_wash: "Into the wash", drying: "To drying", put_away: "Put away", unknown: "Tapped" };

function statusChip(ctx, g) {
  const where = ctx.lib.whereLabel(g, ctx.zoneName);
  const text = g.state === "put_away" && g.location ? `In ${where.text}` : where.long || where.text;
  return `<span class="status-chip ${g.missing ? "warn" : ""}">${ctx.lib.esc(g.missing ? `Missing · ${text}` : text)}</span>`;
}

function hero(ctx, g) {
  const { lib } = ctx;
  const photos = g.photo_urls || [];
  const slides = photos.length
    ? photos.map((p, i) => `<div class="slide"><img src="${lib.esc(p.full)}" alt="${lib.esc(g.name)} photo ${i + 1}"></div>`).join("")
    : `<div class="slide">${lib.tile(g, ctx.zone(g.wash_bin))}</div>`;
  return `<div class="hero-wrap"><div class="hero" aria-label="Photos">${slides}</div>
    <button type="button" class="icon-btn glass back" data-act="closeGarment" aria-label="Back">${lib.ICON.back}</button>
    <button type="button" class="icon-btn glass menu-btn" data-act="toggleMenu" aria-label="More actions" aria-expanded="${ctx.state.menu}">${lib.ICON.more}</button>
    ${photos.length > 1 ? `<span class="counter">${photos.length} photos · swipe</span>` : ""}
    ${ctx.state.menu ? `<div class="menu" role="menu"><button type="button" role="menuitem" data-act="editBasics">Edit</button>
      <button type="button" role="menuitem" class="danger" data-act="deleteGarment">Delete</button></div>` : ""}</div>`;
}

function basicsForm(ctx, g) {
  const { lib, data } = ctx;
  const opt = (items, sel) => items.map((i) => `<option value="${lib.esc(i.id)}" ${i.id === sel ? "selected" : ""}>${lib.esc(i.name)}</option>`).join("");
  const people = [{ id: "", name: "No owner" }, ...data.persons.map((p) => ({ id: p.entity_id, name: p.name }))];
  const locs = [{ id: "", name: "Leave as is" }, ...["closet_1", "closet_2", "in_wash", "drying"].map((l) => ({ id: l, name: lib.LOCATION_LABEL[l] }))];
  return `<form data-submit="saveGarmentBasics" data-garment="${g.id}" class="card stack">
    <label class="field">Name<input class="input" name="name" required maxlength="100" value="${lib.esc(g.name)}"></label>
    <label class="field">Owner<select class="input" name="owner">${opt(people, g.owner || "")}</select></label>
    <div class="row wrap"><label class="field" style="flex:1 1 140px">Wash bin<select class="input" name="wash_bin">${opt(data.wash_bins, g.wash_bin)}</select></label>
      <label class="field" style="flex:1 1 140px">Dry type<select class="input" name="dry_type">${opt(data.dry_types, g.dry_type)}</select></label></div>
    <label class="field">Set location<select class="input" name="location">${opt(locs, "")}</select></label>
    <label class="field">Notes<textarea class="input" name="notes" maxlength="1000">${lib.esc(g.notes || "")}</textarea></label>
    <div class="field">Tags<div class="chips wrap">${g.uids.map((u) => `<span class="chip static">${lib.esc(u)}${g.uids.length > 1 ? ` <button type="button" class="link" data-act="removeUid" data-garment="${g.id}" data-uid="${lib.esc(u)}" aria-label="Remove tag ${lib.esc(u)}">✕</button>` : ""}</span>`).join("")}</div>
      <input class="input" name="add_uid" placeholder="Add a replacement tag ID" autocomplete="off"></div>
    <div class="row"><button type="button" class="btn" data-act="cancelEdit">Cancel</button><span class="spacer"></span><button class="btn primary">Save</button></div></form>`;
}

function descChips(ctx, g) {
  const { lib } = ctx;
  const d = g.description || {};
  const prov = g.description_provenance || {};
  const lock = (f) => (prov[f] === "user" ? ` ${lib.ICON.lock}<span class="sr-only">set by you</span>` : "");
  const chips = [];
  const add = (field, html) => chips.push(`<span class="chip static">${html}${lock(field)}</span>`);
  if (d.type) add("type", lib.esc([d.type, d.subtype].filter(Boolean).map(lib.capitalize).join(" · ")));
  (d.colors || []).forEach((c) => add("colors", `<span class="dot round" style="background:${lib.esc(c.hex)}"></span>${lib.esc(lib.capitalize(c.name))}`));
  if (d.pattern && d.pattern !== "solid") add("pattern", lib.esc(lib.capitalize(d.pattern)));
  if (d.sleeve_length && d.sleeve_length !== "n/a") add("sleeve_length", `${lib.esc(lib.capitalize(d.sleeve_length))} sleeve`);
  if (d.neckline && d.neckline !== "n/a") add("neckline", lib.esc(lib.capitalize(d.neckline)));
  if (d.fit && d.fit !== "n/a") add("fit", `${lib.esc(lib.capitalize(d.fit))} fit`);
  if (d.length && d.length !== "n/a") add("length", `${lib.esc(lib.capitalize(d.length))} length`);
  if (d.rise && d.rise !== "n/a") add("rise", `${lib.esc(lib.capitalize(d.rise))} rise`);
  if ((d.material || []).length) add("material", lib.esc(d.material.map(lib.capitalize).join(" · ")));
  if ((d.seasons || []).length) add("seasons", lib.esc(d.seasons.map(lib.capitalize).join(" · ")));
  if ((d.occasions || []).length) add("occasions", lib.esc(d.occasions.map(lib.capitalize).join(" · ")));
  const dots = (n) => `<span class="dots" aria-hidden="true">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
  if (d.formality) add("formality", `Formality ${dots(d.formality)}<span class="sr-only">${d.formality} of 5</span>`);
  if (d.warmth) add("warmth", `Warmth ${dots(d.warmth)}<span class="sr-only">${d.warmth} of 5</span>`);
  if (g.size?.label) chips.push(`<span class="chip static">Size ${lib.esc(g.size.label)}${g.size.cut && !["n/a", "regular"].includes(g.size.cut) ? ` ${lib.esc(g.size.cut)}` : ""}</span>`);
  if (g.wear_for && g.wear_for !== "any") {
    const work = ctx.data.owners?.[g.owner]?.work_label || "Work";
    chips.push(`<span class="chip static">${lib.esc(g.wear_for === "work" ? work : lib.WEAR_FOR_LABEL[g.wear_for])}${g.uniform ? " · uniform" : ""}</span>`);
  }
  return chips.join("") || `<span class="hint">Not described yet.</span>`;
}

function descForm(ctx, g) {
  const { lib, data } = ctx;
  const tax = data.taxonomy || {};
  const d = g.description || {};
  const prov = g.description_provenance || {};
  const ai = g.description_ai || {};
  const reset = (f) => (prov[f] === "user" && ai[f] !== undefined ? `<button type="button" class="link" data-act="resetField" data-garment="${g.id}" data-field="${f}">Reset to AI</button>` : "");
  const options = (f) => (f === "type" ? tax.types : f === "subtype" ? (tax.subtypes || {})[d.type] || [...new Set(Object.values(tax.subtypes || {}).flat())] : f === "colors" ? (tax.colors || []).map((c) => c.name) : tax[f] || []);
  const single = DESC_SINGLE.map((f) => `<label class="field">${lib.FIELD_LABEL[f]} ${prov[f] === "user" ? lib.ICON.lock : ""} ${reset(f)}
    <select class="input" name="${f}"><option value="">Not set</option>${options(f).map((v) => `<option value="${lib.esc(v)}" ${d[f] === v ? "selected" : ""}>${lib.esc(lib.capitalize(v))}</option>`).join("")}</select></label>`).join("");
  const current = (f) => (f === "colors" ? (d.colors || []).map((c) => c.name) : d[f] || []);
  const multi = DESC_MULTI.map((f) => `<fieldset class="field" style="border:none;padding:0;margin:0"><legend>${lib.FIELD_LABEL[f]} ${prov[f] === "user" ? lib.ICON.lock : ""} ${reset(f)}</legend>
    <div class="chips wrap">${options(f).map((v) => `<label class="chip"><input type="checkbox" name="${f}" value="${lib.esc(v)}" ${current(f).includes(v) ? "checked" : ""}> ${lib.esc(lib.capitalize(v))}</label>`).join("")}</div></fieldset>`).join("");
  const scale = DESC_SCALE.map((f) => `<label class="field">${lib.FIELD_LABEL[f]} ${prov[f] === "user" ? lib.ICON.lock : ""} ${reset(f)}
    <select class="input" name="${f}"><option value="">Not set</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${d[f] === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>`).join("");
  return `<form data-submit="saveDesc" data-garment="${g.id}" class="stack">
    <p class="hint" style="margin:0">Fields you change are locked ${lib.ICON.lock}: re-describing never overwrites them.</p>
    <div class="row wrap" style="align-items:stretch">${single}</div>${scale}${multi}
    <label class="field">Summary ${prov.summary === "user" ? lib.ICON.lock : ""} ${reset("summary")}<textarea class="input" name="summary" maxlength="200">${lib.esc(d.summary || "")}</textarea></label>
    <div class="row"><button type="button" class="btn" data-act="cancelEdit">Cancel</button><span class="spacer"></span><button class="btn primary">Save</button></div></form>`;
}

function wardrobeSection(ctx, g) {
  const { lib, data, state } = ctx;
  const p = g.purchase || {};
  const editing = state.editing;
  const ai = data.wardrobe?.ai_entity;
  const photos = (g.photo_urls || []).map((ph, i) => `<div class="ph"><img src="${lib.esc(ph.thumb)}" alt="Photo ${i + 1}">
    <div class="ph-actions">${i > 0 ? `<button type="button" data-act="makeCover" data-garment="${g.id}" data-photo="${lib.esc(ph.name)}" aria-label="Make cover">★</button>` : "<span></span>"}
      <button type="button" data-act="removePhoto" data-garment="${g.id}" data-photo="${lib.esc(ph.name)}" aria-label="Delete photo ${i + 1}">✕</button></div></div>`).join("");
  return `<div class="card stack"><div class="row"><strong style="flex:1">Photos</strong><span class="sub">${(g.photos || []).length} / ${data.wardrobe.photo_limit}</span></div>
      ${photos ? `<div class="photo-strip">${photos}</div>` : ""}
      ${(g.photos || []).length < data.wardrobe.photo_limit ? ctx.mods.add.photoInputs(ctx, g.id) : ""}${ctx.mods.add.importBlock(ctx, g.id)}</div>
    <div class="card stack"><div class="row"><strong style="flex:1">Details</strong>
        ${editing === "desc" ? "" : `<button type="button" class="link" data-act="editDesc">Edit</button>`}</div>
      ${editing === "desc" ? descForm(ctx, g) : `<div class="chips wrap">${descChips(ctx, g)}</div>
        ${g.description?.summary ? `<span>${lib.esc(g.description.summary)}</span>` : ""}
        <span class="sub">${g.description_model ? `Described by AI ${lib.esc(lib.timeAgo(g.description_updated))}` : "Not described by AI"} · ${lib.ICON.lock} = set by you</span>
        ${ai ? `<button type="button" class="btn" data-act="describe" data-garment="${g.id}" ${state.busy.describe ? "disabled" : ""}>${lib.ICON.sparkle}${state.busy.describe ? "Describing…" : g.description_model ? "Re-describe" : "Describe from photos"}</button>`
          : `<span class="hint">Choose an AI Task entity in Laundry Hub's settings to describe garments from photos.</span>`}`}</div>
    <div class="card stack"><div class="row"><strong style="flex:1">Purchase &amp; fit</strong>
        ${editing === "purchase" ? "" : `<button type="button" class="link" data-act="editPurchase">Edit</button>`}</div>
      ${editing === "purchase" ? ctx.mods.add.purchaseForm(ctx, g, "savePurchase") : `
        ${p.source === "email" && p.store ? `<div class="origin">${lib.ICON.sparkle}<span>From your ${lib.esc(p.store)} order${p.purchased_on ? `, ${lib.esc(lib.shortDate(p.purchased_on))}` : ""}${p.product_url ? ` · <a href="${lib.esc(p.product_url)}" target="_blank" rel="noopener noreferrer">View product</a>` : ""}</span></div>` : ""}
        <div class="kv"><span>Bought</span><span>${lib.esc([p.store, p.purchased_on, lib.money(p.price, p.currency)].filter(Boolean).join(" · ") || "—")}</span></div>
        <div class="kv"><span>Brand</span><span>${lib.esc(g.brand || "—")}</span></div>
        <div class="kv"><span>Size</span><span>${lib.esc(g.size ? [g.size.label, g.size.system, g.size.brand_size_note].filter(Boolean).join(" · ") : "—")}</span></div>
        ${p.product_url ? `<div class="kv"><span>Link</span><span><a href="${lib.esc(p.product_url)}" target="_blank" rel="noopener noreferrer">Product page</a></span></div>` : ""}`}</div>`;
}

// v0.2.8: tags are optional. Untagged garments get quick actions; any
// garment can attach (next unknown tap within 2 minutes, or a typed ID),
// replace or remove a tag.
function quickActions(ctx, g) {
  const { lib } = ctx;
  if (g.tagged !== false) return "";
  const when = g.state_updated ? `Set by hand ${lib.timeAgo(g.state_updated)}` : "Not set yet";
  const stale = g.state_updated && g.state_confidence < 1 ? " · may be out of date" : "";
  return `<div class="card stack" data-section="quick-actions"><div class="stack" style="gap:2px"><strong>Laundry</strong><span class="sub">${lib.esc(when + stale)}</span></div>
    <div class="row wrap"><button type="button" class="btn" data-act="manualState" data-garment="${g.id}" data-action="to_laundry" aria-pressed="${g.state === "in_wash"}">To laundry</button>
      <button type="button" class="btn" data-act="manualState" data-garment="${g.id}" data-action="put_away" aria-pressed="${g.state === "put_away"}">Clean / put away</button></div></div>`;
}

function tagSection(ctx, g) {
  const { lib, data, state } = ctx;
  const waiting = data.link && data.link.garment_id === g.id;
  const conflict = state.tagConflict && state.tagConflict.garment === g.id ? state.tagConflict : null;
  const tags = (g.uids || []).map((u) => `<span class="chip static" style="font-family:monospace">${lib.esc(u)}</span>`).join("");
  const body = waiting
    ? `<div class="tagbox"><strong>Waiting for a tap…</strong><span class="hint">Tap the tag on any station or with the app (until ${lib.esc(new Date(data.link.expires).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}).</span>
        <form data-submit="typedTag" data-garment="${g.id}" class="row wrap"><input class="input" name="uid" placeholder="Or type the tag ID" autocomplete="off" style="flex:1 1 160px"><button class="btn">Attach</button></form>
        <button type="button" class="btn" data-act="cancelAttach" data-garment="${g.id}">Cancel</button></div>`
    : g.tagged === false
      ? `<div class="row wrap"><span class="notag-inline">No tag</span><span class="spacer"></span><button type="button" class="btn primary" data-act="attachTag" data-garment="${g.id}">Attach tag</button></div>`
      : `<div class="row wrap">${tags}<span class="spacer"></span>
          <button type="button" class="btn" data-act="attachTag" data-garment="${g.id}" data-replace="1">Replace tag</button>
          <button type="button" class="btn" data-act="removeTag" data-garment="${g.id}">Remove tag</button></div>`;
  const move = conflict ? `<div class="row wrap"><span style="flex:1">That tag belongs to <strong>${lib.esc(conflict.name)}</strong>.</span>
      <button type="button" class="btn primary" data-act="moveTag" data-garment="${g.id}">Move tag here</button></div>` : "";
  return `<div class="card stack" data-section="tag"><strong>Tag</strong>${body}${move}</div>`;
}

export function render(ctx) {
  const { lib, state } = ctx;
  const g = ctx.garment(state.garment);
  if (!g) {
    return `<div class="detail" role="dialog" aria-modal="true"><div class="detail-body"><button type="button" class="btn" data-act="closeGarment">Back</button>
      <p>That garment no longer exists.</p></div></div>`;
  }
  const owner = g.owner ? ctx.personName(g.owner) : "No owner";
  const bin = ctx.zone(g.wash_bin);
  const photosOff = !ctx.wardrobe ? `<div class="card stack"><strong>Photo</strong>${ctx.mods.add.photoInputs(ctx, g.id, { importLink: false })}
      ${(g.photos || []).length ? `<button type="button" class="btn danger" data-act="removePhoto" data-garment="${g.id}" data-photo="${lib.esc(g.photos[0])}">Remove photo</button>` : ""}</div>` : "";
  const taps = (g.recent_taps || []).slice().reverse();
  return `<div class="detail" role="dialog" aria-modal="true" aria-label="${lib.esc(g.name)}">${hero(ctx, g)}
    <div class="detail-body">
      <div class="row" style="align-items:flex-start;justify-content:space-between;gap:12px">
        <div class="stack" style="gap:4px;min-width:0"><h1 style="margin:0;font-size:24px">${lib.esc(g.name)}</h1>
          <span class="sub">${lib.esc(owner)} · <span class="dot" style="display:inline-block;vertical-align:middle;background:${lib.rgb(bin?.color)}"></span> ${lib.esc(ctx.zoneName(g.wash_bin))} · ${lib.esc(ctx.zoneName(g.dry_type))}</span></div>
        ${statusChip(ctx, g)}</div>
      ${quickActions(ctx, g)}${tagSection(ctx, g)}
      ${state.editing === "basics" ? basicsForm(ctx, g) : ""}
      ${ctx.wardrobe ? wardrobeSection(ctx, g) : photosOff}
      ${g.notes && state.editing !== "basics" ? `<div class="card"><span class="sub">Notes</span><p style="margin:4px 0 0">${lib.esc(g.notes)}</p></div>` : ""}
      <div class="card"><strong>Recent taps</strong>${taps.length ? `<ul class="events">${taps.map((t) => `<li><span>${lib.esc(EVENT_LABEL[t.event] || t.event)}${t.repeat ? " (again)" : ""} · ${lib.esc(lib.stationLabel(t.station))}</span><span class="sub">${lib.esc(lib.timeAgo(t.time))}</span></li>`).join("")}</ul>`
        : `<p class="hint" style="margin:6px 0 0">No taps yet.</p>`}</div>
    </div></div>`;
}

function readDesc(form, g) {
  const fd = new FormData(form);
  const d = g.description || {};
  const out = {};
  for (const f of DESC_SINGLE) { const v = fd.get(f) || null; if ((d[f] || null) !== v) out[f] = v; }
  for (const f of DESC_SCALE) { const v = fd.get(f) ? Number(fd.get(f)) : null; if ((d[f] ?? null) !== v) out[f] = v; }
  for (const f of DESC_MULTI) {
    const v = fd.getAll(f).map(String);
    const cur = f === "colors" ? (d.colors || []).map((c) => c.name) : d[f] || [];
    if (v.join("|") !== cur.join("|")) out[f] = v.length ? v : null;
  }
  const summary = (fd.get("summary") || "").toString().trim() || null;
  if ((d.summary || null) !== summary) out.summary = summary;
  return out;
}

async function attachCall(ctx, payload) {
  // Called directly so a "tag_in_use" answer can offer "Move tag here".
  try {
    return await ctx.hass.callWS({ type: "laundry_hub/garment/attach_tag", ...payload });
  } catch (err) {
    console.error("Laundry Hub: laundry_hub/garment/attach_tag failed", err);
    if (err && err.code === "tag_in_use") {
      ctx.state.tagConflict = { garment: payload.garment_id, uid: payload.uid, replace: Boolean(payload.replace), name: ctx.lib.errorText(err, "").replace(/^That tag belongs to (.*?);.*$/, "$1") };
      ctx.render();
      return null;
    }
    ctx.toast(ctx.lib.errorText(err), true);
    return null;
  }
}

export const actions = {
  closeGarment: (ctx) => ctx.close(),
  async manualState(ctx, el) {
    const res = await ctx.ws("garment/manual_state", { garment_id: el.dataset.garment, action: el.dataset.action });
    if (res) { ctx.toast(el.dataset.action === "to_laundry" ? "In the laundry" : "Clean and put away"); ctx.reload(); }
  },
  async attachTag(ctx, el) {
    ctx.state.tagConflict = null;
    const res = await attachCall(ctx, { garment_id: el.dataset.garment, replace: el.dataset.replace === "1" });
    if (res) ctx.reload();
  },
  async typedTag(ctx, form) {
    const uid = new FormData(form).get("uid").toString().trim();
    if (!uid) return;
    const replace = Boolean(ctx.data.link?.replace);
    const res = await attachCall(ctx, { garment_id: form.dataset.garment, uid, replace });
    if (res) { ctx.toast("Tag attached"); ctx.reload(); }
  },
  async moveTag(ctx, el) {
    const c = ctx.state.tagConflict;
    if (!c) return;
    const res = await attachCall(ctx, { garment_id: el.dataset.garment, uid: c.uid, replace: c.replace, move: true });
    if (res) { ctx.state.tagConflict = null; ctx.toast("Tag moved here"); ctx.reload(); }
  },
  async cancelAttach(ctx, el) {
    if (await ctx.ws("garment/attach_cancel", { garment_id: el.dataset.garment })) ctx.reload();
  },
  async removeTag(ctx, el) {
    const g = ctx.garment(el.dataset.garment);
    if (!g || !window.confirm(`Remove the tag from ${g.name}? Its history is kept, and its laundry state is then set by hand.`)) return;
    if (await ctx.ws("garment/remove_tag", { garment_id: g.id })) { ctx.toast("Tag removed"); ctx.reload(); }
  },
  toggleMenu: (ctx) => ctx.update({ menu: !ctx.state.menu }),
  editBasics: (ctx) => ctx.update({ editing: "basics", menu: false }),
  editDesc: (ctx) => ctx.update({ editing: "desc" }),
  editPurchase: (ctx) => ctx.update({ editing: "purchase" }),
  cancelEdit: (ctx) => ctx.update({ editing: null }),
  async deleteGarment(ctx) {
    const g = ctx.garment(ctx.state.garment);
    if (!g || !window.confirm(`Delete ${g.name}? Its tag becomes unknown again.`)) return;
    if (await ctx.ws("garment/delete", { garment_id: g.id })) { ctx.toast("Deleted"); ctx.close(); }
  },
  async saveGarmentBasics(ctx, form) {
    const g = ctx.garment(form.dataset.garment);
    const fd = new FormData(form);
    const payload = { garment_id: g.id, name: fd.get("name").toString().trim(), owner: fd.get("owner") || null, wash_bin: fd.get("wash_bin"), dry_type: fd.get("dry_type"), notes: fd.get("notes").toString() };
    const addUid = fd.get("add_uid").toString().trim();
    if (addUid) payload.add_uid = addUid;
    if (!(await ctx.ws("garment/update", payload))) return;
    const loc = fd.get("location");
    if (loc && !(await ctx.ws("garment/set_location", { garment_id: g.id, location: loc }))) return;
    ctx.toast("Saved");
    ctx.state.editing = null;
    ctx.reload();
  },
  async removeUid(ctx, el) {
    if (await ctx.ws("garment/update", { garment_id: el.dataset.garment, remove_uid: el.dataset.uid })) ctx.reload();
  },
  async saveDesc(ctx, form) {
    const g = ctx.garment(form.dataset.garment);
    const fields = readDesc(form, g);
    if (Object.keys(fields).length && !(await ctx.ws("garment/describe_fields", { garment_id: g.id, fields }))) return;
    ctx.state.editing = null;
    ctx.toast("Saved");
    ctx.reload();
  },
  async resetField(ctx, el) {
    if (await ctx.ws("garment/reset_field", { garment_id: el.dataset.garment, field: el.dataset.field })) ctx.reload();
  },
  async savePurchase(ctx, form) {
    const g = ctx.garment(form.dataset.garment);
    const payload = ctx.mods.add.readPurchaseForm(form, g.purchase?.currency || ctx.data.wardrobe.currency);
    if (await ctx.ws("garment/wardrobe", { garment_id: g.id, ...payload })) { ctx.state.editing = null; ctx.toast("Saved"); ctx.reload(); }
  },
  async describe(ctx, el) {
    ctx.state.busy.describe = true;
    ctx.render();
    const res = await ctx.ws("garment/describe", { garment_id: el.dataset.garment });
    ctx.state.busy.describe = false;
    if (res) ctx.toast("Description updated");
    ctx.reload();
  },
  async removePhoto(ctx, el) {
    if (await ctx.ws("photo/remove", { garment_id: el.dataset.garment, photo: el.dataset.photo })) ctx.reload();
  },
  async makeCover(ctx, el) {
    const g = ctx.garment(el.dataset.garment);
    const order = [el.dataset.photo, ...g.photos.filter((p) => p !== el.dataset.photo)];
    if (await ctx.ws("photo/reorder", { garment_id: g.id, photos: order })) ctx.reload();
  },
};
