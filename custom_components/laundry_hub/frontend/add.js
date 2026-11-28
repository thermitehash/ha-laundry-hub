// Add: 1 tag (live) → 2 basics → 3 wardrobe (if on) → 4 saved.

const LAST_DRY_KEY = "laundry_hub.lastDryByBin";

function lastDry(binId) {
  try { return JSON.parse(localStorage.getItem(LAST_DRY_KEY) || "{}")[binId] || null; } catch (_e) { return null; }
}
function rememberDry(binId, dryId) {
  try {
    const map = JSON.parse(localStorage.getItem(LAST_DRY_KEY) || "{}");
    map[binId] = dryId;
    localStorage.setItem(LAST_DRY_KEY, JSON.stringify(map));
  } catch (_e) { /* storage blocked: defaults only */ }
}

export function reset(state) {
  state.add = { step: 1, uid: "", noTag: false, manual: false, form: {}, garmentId: null, preview: null, busy: false, seenAt: null, known: null, card: null, askOrder: false };
}

// Step 2 starts with "Is this from a recent order?" when unmatched purchase cards exist.
function enterBasics(ctx, patch) {
  Object.assign(ctx.state.add, patch, { step: 2, card: null, askOrder: ctx.wardrobe && ctx.lib.unmatchedCards(ctx.data).length > 0 });
}

// Live tap from any station while Add is open.
export function onTap(ctx, tap) {
  const add = ctx.state.add;
  if (ctx.state.tab !== "add" || add.step !== 1) return;
  if (ctx.data.link) return; // "Attach tag" is waiting: that tap links instead
  if (tap.event_type === "unknown") {
    enterBasics(ctx, { uid: tap.uid, noTag: false, seenAt: tap.station, known: null });
    ctx.render();
  } else if (tap.garment_id) {
    add.known = tap;
    ctx.render();
  }
}

function stepsBar(step, total) {
  return `<div class="steps" aria-hidden="true">${Array.from({ length: 4 }, (_v, i) => `<div class="${i < step ? "on" : ""}"></div>`).join("")}</div>
    <span class="sub">Step ${Math.min(step, total)} of ${total}</span>`;
}

function stepTag(ctx) {
  const { lib, data, state } = ctx;
  const add = state.add;
  const recent = (data.unknown_tags || []).slice(0, 4);
  const known = add.known ? `<div class="card row"><span style="flex:1">That tag already belongs to <strong>${lib.esc(add.known.name)}</strong>.</span>
      <button type="button" class="btn" data-act="openGarment" data-id="${add.known.garment_id}">Open</button></div>` : "";
  return `<div class="row wrap" role="group" aria-label="How to add">
      <button type="button" class="chip tall" aria-pressed="true">Tap a tag</button>
      <button type="button" class="chip tall" data-act="noTag">No tag (wardrobe only)</button></div>
    <div class="card listen">
      <div class="ring"><div>${lib.ICON.tag}</div></div>
      <div class="stack" style="gap:6px"><strong style="font-size:20px">Waiting for a tag…</strong>
        <span class="hint" style="font-size:16px">Tap it on any station. The details fill in on their own.</span></div>
      ${add.manual ? `<form data-submit="manualUid" class="stack" style="width:100%">
          <label class="field">Tag ID<input class="input" name="uid" data-keep="uid" autocomplete="off" placeholder="04A1B2C3D4E5F6" value="${lib.esc(add.uid)}" required></label>
          <button class="btn primary block">Next</button></form>`
        : `<button type="button" class="link" data-act="manual">Enter tag ID by hand</button>`}
    </div>
    ${known}
    ${recent.length ? `<h2 class="section-label">Seen recently</h2><div class="stack">${recent.map((t) => `<div class="row wrap">
      <button type="button" class="chip tall" data-act="useUid" data-uid="${lib.esc(t.uid)}" data-station="${lib.esc(t.station)}">
        …${lib.esc(t.uid.slice(-6))} · ${lib.esc(lib.stationLabel(t.station))} · ${lib.esc(lib.timeAgo(t.last_seen))}</button>
      ${untagged(ctx).length ? `<button type="button" class="btn small" data-act="openSheet" data-kind="linkTag" data-field="${lib.esc(t.uid)}">Link to an untagged garment…</button>` : ""}</div>`).join("")}</div>` : ""}`;
}

export const untagged = (ctx) => (ctx.data.garments || []).filter((g) => g.tagged === false);

function tagRow(ctx) {
  const { lib, state } = ctx;
  const add = state.add;
  return add.noTag
    ? `<div class="card row"><span class="hint">Tag</span><strong>No tag (wardrobe only)</strong><span class="spacer"></span>
      <button type="button" class="link" data-act="backToTag">Change</button></div>`
    : `<div class="card row"><span class="hint">Tag</span><strong style="font-family:monospace">${lib.esc(add.uid)}</strong><span class="spacer"></span>
      <button type="button" class="link" data-act="backToTag">Change</button></div>`;
}

function stepOrder(ctx) {
  const { lib, data } = ctx;
  const cards = lib.unmatchedCards(data);
  return `${tagRow(ctx)}
    <h2 class="section-label" style="margin:8px 0 0">Is this from a recent order?</h2>
    <span class="hint">Pick it to fill in the name, brand, size, purchase details and photo.</span>
    <div class="order-row" role="list">${cards.map((c) => `<div role="listitem">${lib.purchaseCard(c, { act: "pickOrder" })}</div>`).join("")}</div>
    <button type="button" class="btn block" data-act="noOrder">None of these</button>`;
}

function orderBanner(ctx) {
  const { lib, data, state } = ctx;
  const card = (data.purchases?.cards || []).find((c) => c.id === state.add.card);
  if (!card) return "";
  return `<div class="card row origin">${lib.ICON.sparkle}<span style="flex:1">From your ${lib.esc(card.store)} order${card.order_date ? `, ${lib.esc(lib.shortDate(card.order_date))}` : ""}${card.uniform_supplier ? " · Uniform will be ticked (you can untick it)" : ""}</span>
    <button type="button" class="link" data-act="changeOrder">Change</button></div>`;
}

function stepBasics(ctx) {
  const { lib, data, state } = ctx;
  const add = state.add;
  const f = add.form;
  const bin = f.wash_bin || data.wash_bins[0]?.id;
  const dry = f.dry_type || lastDry(bin) || data.dry_types[0]?.id;
  const loc = f.location ?? (["closet_1", "closet_2"].includes(add.seenAt) ? add.seenAt : "");
  if (!data.wash_bins.length || !data.dry_types.length) {
    return `<div class="card">Add at least one wash bin and one dry type under Settings → Devices &amp; services → Laundry Hub.</div>`;
  }
  const people = [{ id: "", name: "No owner" }, ...data.persons.map((p) => ({ id: p.entity_id, name: p.name }))];
  return `<form data-submit="saveBasics" class="stack">${orderBanner(ctx)}
    ${tagRow(ctx)}
    <label class="field">Name<input class="input" name="name" data-keep="name" required maxlength="100" autocomplete="off" placeholder="Blue hoodie" value="${lib.esc(f.name || "")}" data-input="formField" data-field="name"></label>
    <div class="field">Owner<div class="people" role="group" aria-label="Owner">${people.map((p) => `
      <button type="button" class="person" data-act="pick" data-field="owner" data-value="${lib.esc(p.id)}" aria-pressed="${(f.owner || "") === p.id}">
        <span class="avatar lg">${lib.esc(p.id ? lib.initials(p.name) : "—")}</span>${lib.esc(p.name)}</button>`).join("")}</div></div>
    <div class="field">Wash bin<div class="swatches" role="group" aria-label="Wash bin">${data.wash_bins.map((b) => `
      <button type="button" class="swatch-btn" data-act="pick" data-field="wash_bin" data-value="${b.id}" aria-pressed="${bin === b.id}">
        <span class="sw" style="background:${lib.rgb(b.color)}"></span>${lib.esc(b.name)}</button>`).join("")}</div></div>
    <div class="field">Dry type<div class="chips wrap" role="group" aria-label="Dry type">${data.dry_types.map((d) => `
      <button type="button" class="chip tall" data-act="pick" data-field="dry_type" data-value="${d.id}" aria-pressed="${dry === d.id}">${lib.esc(d.name)}</button>`).join("")}</div></div>
    ${add.noTag ? `<span class="hint">Without a tag, its laundry state is set by hand from the garment ("To laundry", "Clean / put away"). You can attach a tag later.</span>` : `<details class="more" ${loc ? "open" : ""}><summary>Location (optional)</summary>
      <div class="chips wrap">${[["", "Not placed yet"], ["closet_1", "Closet 1"], ["closet_2", "Closet 2"]].map(([v, l]) => `
        <button type="button" class="chip tall" data-act="pick" data-field="location" data-value="${v}" aria-pressed="${loc === v}">${l}</button>`).join("")}</div></details>`}
    <button class="btn primary block" ${add.busy ? "disabled" : ""}>${ctx.wardrobe ? "Next" : "Save garment"}</button>
  </form>`;
}

export function photoInputs(ctx, garmentId, { importLink = true } = {}) {
  const { lib } = ctx;
  return `<div class="row wrap">
    <label class="btn">${lib.ICON.camera}Take photo<input class="sr-only" type="file" accept="image/*" capture="environment" multiple data-change="photos" data-garment="${garmentId}"></label>
    <label class="btn">${lib.ICON.image}Choose<input class="sr-only" type="file" accept="image/*" multiple data-change="photos" data-garment="${garmentId}"></label>
    ${importLink ? `<button type="button" class="btn" data-act="showImport">${lib.ICON.link}Import from link</button>` : ""}</div>`;
}

export function importBlock(ctx, garmentId) {
  const { lib, state } = ctx;
  const add = state.add;
  const p = add.preview;
  if (!add.importOpen && !p) return "";
  if (!p) {
    return `<form data-submit="importUrl" class="card stack"><label class="field">Product link<input class="input" type="url" name="url" data-keep="importUrl" required placeholder="https://…"></label>
      <button class="btn primary" ${add.busy ? "disabled" : ""}>${add.busy ? "Reading page…" : "Import"}</button></form>`;
  }
  const rows = [["name", "Name", p.name], ["brand", "Brand", p.brand], ["purchase", "Store and price", [p.purchase?.store, lib.money(p.purchase?.price, p.purchase?.currency)].filter(Boolean).join(" · ")],
    ["colors", "Colour", (p.colors || []).map((c) => c.name).join(", ")], ["material", "Material", (p.material || []).join(", ")]].filter((r) => r[2]);
  return `<form data-submit="applyImport" data-garment="${garmentId}" class="card stack"><strong>From ${lib.esc(p.purchase?.store || "that page")}</strong>
    ${rows.map(([k, l, v]) => `<label class="row"><input type="checkbox" name="field" value="${k}" checked> <span class="hint" style="width:110px">${l}</span><span>${lib.esc(v)}</span></label>`).join("")}
    ${(p.images || []).length ? `<div class="preview-imgs">${p.images.map((url, i) => `<label><img src="${lib.esc(url)}" alt="Imported photo ${i + 1}"><input type="checkbox" name="image" value="${i}" checked aria-label="Use photo ${i + 1}"></label>`).join("")}</div>` : `<span class="hint">No photos found on the page.</span>`}
    <div class="row"><button type="button" class="btn" data-act="cancelImport">Cancel</button><span class="spacer"></span><button class="btn primary">Save these</button></div></form>`;
}

export function purchaseForm(ctx, g, submit) {
  const { lib, data } = ctx;
  const p = g.purchase || {};
  const s = g.size || {};
  const tax = data.taxonomy || {};
  const opt = (list, sel, labels = {}) => list.map((v) => `<option value="${v}" ${v === sel ? "selected" : ""}>${lib.esc(labels[v] || lib.capitalize(v))}</option>`).join("");
  return `<form data-submit="${submit}" data-garment="${g.id}" class="stack">
    <div class="row wrap"><label class="field" style="flex:1 1 140px">Brand<input class="input" name="brand" value="${lib.esc(g.brand || "")}"></label>
      <label class="field" style="flex:1 1 90px">Size<input class="input" name="size_label" value="${lib.esc(s.label || "")}" placeholder="M, 10, 32x30"></label></div>
    <div class="row wrap"><label class="field" style="flex:1 1 140px">Size system<select class="input" name="size_system">${opt(tax.size_systems || [], s.system || "letter")}</select></label>
      <label class="field" style="flex:1 1 140px">Cut<select class="input" name="size_cut">${opt(tax.size_cuts || [], s.cut || "regular")}</select></label></div>
    <label class="field">Size note<input class="input" name="size_note" value="${lib.esc(s.brand_size_note || "")}" placeholder="runs small"></label>
    <div class="row wrap"><label class="field" style="flex:1 1 140px">Wear for<select class="input" name="wear_for">${opt(tax.wear_for || [], g.wear_for || "any", lib.WEAR_FOR_LABEL)}</select></label>
      <label class="row" style="flex:1 1 140px;min-height:44px"><input type="checkbox" name="uniform" ${g.uniform ? "checked" : ""}> Uniform</label></div>
    <details class="more" ${p.store || p.price ? "open" : ""}><summary>Purchase details (optional)</summary><div class="stack">
      <div class="row wrap"><label class="field" style="flex:1 1 140px">Store<input class="input" name="store" value="${lib.esc(p.store || "")}"></label>
        <label class="field" style="flex:1 1 140px">Bought on<input class="input" type="date" name="purchased_on" value="${lib.esc(p.purchased_on || "")}"></label></div>
      <div class="row wrap"><label class="field" style="flex:1 1 120px">Price (${lib.esc(p.currency || data.wardrobe?.currency || "")})<input class="input" type="number" min="0" step="0.01" name="price" value="${p.price ?? ""}"></label>
        <label class="field" style="flex:1 1 120px">Order ref<input class="input" name="order_ref" value="${lib.esc(p.order_ref || "")}"></label></div>
      <label class="field">Product link<input class="input" type="url" name="product_url" value="${lib.esc(p.product_url || "")}"></label></div></details>
    <button class="btn primary">Save details</button></form>`;
}

export function readPurchaseForm(form, currency) {
  const fd = new FormData(form);
  const v = (k) => (fd.get(k) ?? "").toString().trim();
  return {
    brand: v("brand") || null,
    size: v("size_label") ? { label: v("size_label"), system: v("size_system"), cut: v("size_cut"), brand_size_note: v("size_note") || null } : null,
    wear_for: v("wear_for") || "any",
    uniform: fd.get("uniform") === "on",
    purchase: { store: v("store") || null, purchased_on: v("purchased_on") || null, price: v("price") === "" ? null : Number(v("price")), currency, product_url: v("product_url") || null, order_ref: v("order_ref") || null, source: "manual" },
  };
}

function stepWardrobe(ctx) {
  const { lib, state, data } = ctx;
  const g = ctx.garment(state.add.garmentId);
  if (!g) return `<div class="card">Saving…</div>`;
  const desc = g.description;
  const describing = data.wardrobe.ai_entity && (g.photos || []).length && !g.description_updated;
  return `<div class="card stack"><strong>Photos</strong>
      ${(g.photo_urls || []).length ? `<div class="photo-strip">${g.photo_urls.map((p) => `<div class="ph"><img src="${lib.esc(p.thumb)}" alt=""></div>`).join("")}</div>` : `<span class="hint">Add a photo so the garment is easy to spot.</span>`}
      ${photoInputs(ctx, g.id)}${importBlock(ctx, g.id)}</div>
    <div class="card stack"><strong>Details</strong>${purchaseForm(ctx, g, "saveWardrobe")}</div>
    <div class="card stack"><strong>Description</strong>
      ${data.wardrobe.ai_entity ? (desc && desc.summary ? `<span>${lib.esc(desc.summary)}</span>` : `<span class="hint">${describing ? "Describing from your photo…" : "A description is generated after you add a photo."}</span>`)
        : `<span class="hint">Choose an AI Task entity in Laundry Hub's settings to describe garments from photos.</span>`}</div>
    <button type="button" class="btn primary block" data-act="finishAdd">Done</button>`;
}

function stepSaved(ctx) {
  const { lib, state } = ctx;
  const g = ctx.garment(state.add.garmentId);
  if (!g) return `<div class="card">Saved.</div>`;
  const url = lib.coverThumb(g);
  return `<div class="card stack" style="align-items:center;text-align:center;padding:24px">
      <div style="width:120px;height:120px;border-radius:16px;overflow:hidden">${url ? `<img src="${lib.esc(url)}" alt="" style="width:100%;height:100%;object-fit:cover">` : lib.tile(g, ctx.zone(g.wash_bin))}</div>
      <strong style="font-size:20px">${lib.esc(g.name)} saved</strong>
      <span class="hint">${lib.esc(ctx.zoneName(g.wash_bin))} · ${lib.esc(ctx.zoneName(g.dry_type))}</span>
      <button type="button" class="btn primary block" data-act="addAnother">Add another</button>
      <button type="button" class="btn block" data-act="openGarment" data-id="${g.id}">Open garment</button></div>`;
}

export function render(ctx) {
  const add = ctx.state.add;
  const total = ctx.wardrobe ? 4 : 3;
  const shown = !ctx.wardrobe && add.step === 4 ? 3 : add.step;
  const body = add.step === 1 ? stepTag(ctx) : add.step === 2 ? (add.askOrder ? stepOrder(ctx) : stepBasics(ctx)) : add.step === 3 ? stepWardrobe(ctx) : stepSaved(ctx);
  return `<div class="page-head" style="flex-direction:column;align-items:stretch;gap:10px"><h1>Add a garment</h1>${stepsBar(shown, total)}</div>
    <div class="stack" style="max-width:640px">${body}</div>`;
}

export const actions = {
  manual: (ctx) => { ctx.state.add.manual = true; ctx.render(); },
  noTag(ctx) { enterBasics(ctx, { uid: "", noTag: true, seenAt: null, known: null }); ctx.render(); },
  manualUid(ctx, form) {
    const uid = new FormData(form).get("uid").toString().trim();
    if (!uid) return;
    enterBasics(ctx, { uid, noTag: false, seenAt: null });
    ctx.render();
  },
  useUid(ctx, el) { enterBasics(ctx, { uid: el.dataset.uid, noTag: false, seenAt: el.dataset.station }); ctx.render(); },
  pickOrder(ctx, el) {
    const card = (ctx.data.purchases?.cards || []).find((c) => c.id === el.dataset.card);
    if (!card) return;
    const add = ctx.state.add;
    add.card = card.id;
    add.askOrder = false;
    add.form.name = card.name;
    ctx.render();
  },
  noOrder(ctx) { Object.assign(ctx.state.add, { askOrder: false, card: null }); ctx.render(); },
  changeOrder(ctx) { ctx.state.add.askOrder = true; ctx.render(); },
  backToTag(ctx) { ctx.state.add.step = 1; ctx.render(); },
  formField(ctx, el) { ctx.state.add.form[el.dataset.field] = el.value; },
  pick(ctx, el) {
    const f = ctx.state.add.form;
    f[el.dataset.field] = el.dataset.value;
    if (el.dataset.field === "wash_bin") f.dry_type = lastDry(el.dataset.value) || f.dry_type;
    ctx.render();
  },
  async saveBasics(ctx, form) {
    const { data, state } = ctx;
    const add = state.add;
    const f = add.form;
    f.name = new FormData(form).get("name").toString().trim();
    const bin = f.wash_bin || data.wash_bins[0].id;
    const dry = f.dry_type || lastDry(bin) || data.dry_types[0].id;
    const loc = add.noTag ? "" : f.location ?? (["closet_1", "closet_2"].includes(add.seenAt) ? add.seenAt : "");
    add.busy = true;
    ctx.render();
    const payload = { name: f.name, wash_bin: bin, dry_type: dry, owner: f.owner || null, location: loc || null };
    if (!add.noTag) payload.uid = add.uid;
    const res = await ctx.ws("garment/create", payload);
    add.busy = false;
    if (!res) { ctx.render(); return; }
    rememberDry(bin, dry);
    add.garmentId = res.id;
    if (add.card) {
      // Copies the card's photos and purchase details; the card itself is kept.
      if (await ctx.ws("purchases/match", { card_id: add.card, garment_id: res.id })) add.card = null;
    }
    add.step = ctx.wardrobe ? 3 : 4;
    ctx.reload();
  },
  async photos(ctx, el) {
    const files = el.files;
    if (!files || !files.length) return;
    await ctx.upload(el.dataset.garment, files);
  },
  showImport(ctx) { ctx.state.add.importOpen = true; ctx.render(); },
  cancelImport(ctx) { Object.assign(ctx.state.add, { importOpen: false, preview: null }); ctx.render(); },
  async importUrl(ctx, form) {
    const add = ctx.state.add;
    add.busy = true;
    ctx.render();
    const res = await ctx.ws("import_product_url", { url: new FormData(form).get("url").toString().trim() });
    add.busy = false;
    add.preview = res;
    ctx.render();
  },
  async applyImport(ctx, form) {
    const add = ctx.state.add;
    const fd = new FormData(form);
    const res = await ctx.ws("import/apply", {
      garment_id: form.dataset.garment, token: add.preview.token,
      fields: fd.getAll("field").map(String), images: fd.getAll("image").map(Number),
    });
    if (res) { Object.assign(add, { preview: null, importOpen: false }); ctx.toast("Imported"); ctx.reload(); }
  },
  async saveWardrobe(ctx, form) {
    const g = ctx.garment(form.dataset.garment);
    const res = await ctx.ws("garment/wardrobe", { garment_id: g.id, ...readPurchaseForm(form, g.purchase?.currency || ctx.data.wardrobe.currency) });
    if (res) { ctx.toast("Details saved"); ctx.reload(); }
  },
  finishAdd(ctx) { ctx.state.add.step = 4; ctx.render(); },
  addAnother(ctx) { reset(ctx.state); ctx.render(); },
};
