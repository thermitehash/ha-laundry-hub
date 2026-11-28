// Home: overview tiles per closet and wash bin, drying, missing. Tiles drill into Closet.

const active = (g) => (g.lifecycle || "active") === "active";

function thumbs(ctx, items) {
  const { lib } = ctx;
  const shown = items.slice(0, 3);
  const more = items.length - shown.length;
  return `<div class="thumbs">${shown.map((g) => {
    const url = lib.coverThumb(g);
    const bin = ctx.zone(g.wash_bin);
    return `<div class="thumb">${url ? `<img src="${lib.esc(url)}" alt="" loading="lazy">` : lib.tile(g, bin)}</div>`;
  }).join("")}${more > 0 ? `<div class="thumb more">+${more}</div>` : ""}</div>`;
}

function tileBtn(ctx, { name, count, items, strip, filters, hint }) {
  const { lib } = ctx;
  return `<button type="button" class="tile" data-act="drill" data-filters='${lib.esc(JSON.stringify(filters))}'>
    ${strip ? `<span class="strip" style="background:${strip}" aria-hidden="true"></span>` : ""}
    <span class="tile-head"><span class="tile-name">${lib.esc(name)}</span><span class="tile-count">${count}</span></span>
    ${items.length ? thumbs(ctx, items) : `<span class="hint">${lib.esc(hint || "Empty")}</span>`}
  </button>`;
}

// v0.2.8: Purchases is easy to find: a top card whenever new cards wait.
function purchasesCard(ctx) {
  const { lib, data } = ctx;
  if (!ctx.wardrobe) return "";
  const cards = lib.unmatchedCards(data);
  if (!cards.length) return "";
  const shown = cards.slice(0, 3).map((c) => {
    const url = c.photo_urls?.[0]?.thumb;
    return `<div class="thumb">${url ? `<img src="${lib.esc(url)}" alt="" loading="lazy">` : `<span aria-hidden="true">${lib.esc(lib.initials(c.name))}</span>`}</div>`;
  }).join("");
  return `<button type="button" class="card attention purchases-card" data-act="openPurchases" data-section="purchases-card">
      <span style="flex:1;display:flex;flex-direction:column;gap:6px"><strong>Purchases · ${cards.length} to review</strong>
      <div class="thumbs">${shown}</div></span>${lib.ICON.chevron}</button>`;
}

export function render(ctx) {
  const { lib, data } = ctx;
  const garments = data.garments.filter(active);
  const inWash = garments.filter((g) => g.state === "in_wash");
  const head = `<div class="page-head"><div><h1>Laundry Hub</h1>
      <div class="sub">${garments.length} garment${garments.length === 1 ? "" : "s"} · ${inWash.length} in the wash</div></div>
      <button type="button" class="icon-btn" data-act="tab" data-tab="add" aria-label="Add garment">${lib.ICON.plus}</button></div>`;

  if (!garments.length) {
    return `${head}${purchasesCard(ctx)}<div class="card empty">
      <div class="ring"><div>${lib.ICON.tag}</div></div>
      <strong style="font-size:20px">No garments yet</strong>
      <p class="hint" style="margin:0;font-size:16px">Tap a new tag on any station to add your first one.</p>
      <button type="button" class="btn primary" data-act="tab" data-tab="add">Add a garment</button></div>`;
  }

  const purchases = purchasesCard(ctx);
  const missing = garments.filter((g) => g.missing);
  const attention = missing.length
    ? `<button type="button" class="card attention" data-act="drill" data-filters='{"location":["missing"]}'>
        <span class="badge">${lib.ICON.alert}</span>
        <span style="flex:1;display:flex;flex-direction:column;gap:2px"><strong>Missing (not seen in ${data.missing_after_days} days) · ${missing.length}</strong>
        <span class="sub">${lib.esc(missing.slice(0, 3).map((g) => g.name).join(", "))}${missing.length > 3 ? "…" : ""}</span></span>${lib.ICON.chevron}</button>`
    : "";

  const closets = ["closet_1", "closet_2"].map((c) => {
    const items = garments.filter((g) => g.state === "put_away" && g.location === c);
    return tileBtn(ctx, { name: lib.STATION_LABEL[c], count: items.length, items, filters: { location: [c] } });
  }).join("");

  const bins = data.wash_bins.map((b) => {
    const items = inWash.filter((g) => g.wash_bin === b.id);
    return tileBtn(ctx, { name: b.name, count: items.length, items, strip: lib.rgb(b.color), filters: { location: ["in_wash"], bin: [b.id] } });
  }).join("");

  const drying = garments.filter((g) => g.state === "drying");
  const oldest = drying
    .map((g) => [...(g.recent_taps || [])].reverse().find((t) => t.event === "drying")?.time)
    .filter(Boolean).sort()[0];
  const dryTile = tileBtn(ctx, {
    name: "Drying", count: drying.length, items: drying, filters: { location: ["drying"] },
    hint: "Nothing drying",
  }).replace("</button>", `${drying.length && oldest ? `<span class="hint">Oldest: ${lib.esc(lib.timeAgo(oldest))}</span>` : ""}</button>`);

  // "Out" tile is reserved for v0.3: only rendered when out_since data exists.
  const out = garments.filter((g) => g.out_since);
  const outTile = out.length ? tileBtn(ctx, { name: "Out", count: out.length, items: out, filters: {} }) : "";

  return `${head}
    <div class="stack">${purchases}${attention}</div>
    <div class="two-col">
      <section><h2 class="section-label">Closets</h2><div class="tiles">${closets}${outTile}</div>
        <h2 class="section-label">Drying</h2><div class="tiles">${dryTile}</div></section>
      <section><h2 class="section-label">In the wash</h2><div class="tiles">${bins}</div></section>
    </div>`;
}

export const actions = {
  openPurchases(ctx) {
    ctx.state.filters = { ...ctx.state.filters, purchases: true, archivedPurchases: false };
    ctx.go("closet");
  },
  drill(ctx, el) {
    const filters = JSON.parse(el.dataset.filters || "{}");
    const base = { owner: [], location: [], bin: [], needsPhoto: false, needsReview: false, tax: {}, q: "" };
    ctx.state.filters = { ...ctx.state.filters, ...base, ...filters };
    ctx.go("closet");
  },
};
