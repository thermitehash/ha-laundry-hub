// Settings: read-only summary with links to HA's integration options, station health,
// per-owner "work" label (not part of the config flow, so editable here).

const CONFIG_PATH = "/config/integrations/integration/laundry_hub";

// "1 email couldn't be read · Outdoor Research · Nov 20"
function unreadableText(lib, items) {
  const first = items[0];
  const more = items.length > 1 ? ` (+${items.length - 1} more)` : "";
  return `${items.length} email${items.length === 1 ? "" : "s"} couldn't be read · ${first.store || first.sender_domain} · ${lib.shortDate(first.date)}${more}`;
}

// Read-only status of the purchase-email connection; changes happen in Configure.
function purchaseRow(ctx) {
  const { lib, data } = ctx;
  const p = data.purchases;
  const rows = p.connected
    ? `<div class="kv"><span>Status</span><span class="state ok">Connected</span></div>
       <div class="kv"><span>Stores</span><span>${p.stores}</span></div>
       <div class="kv"><span>Last check</span><span>${lib.esc(lib.timeAgo(p.last_check))}</span></div>
       <div class="kv"><span>New cards</span><span>${p.new_cards}</span></div>
       <p class="hint" style="margin:0" data-section="purge-note">Archived cards and their photos are deleted 1 year after archiving.</p>
       ${p.cleanup?.skipped ? `<div class="row wrap" data-section="purchase-cleanup"><span class="state problem" style="flex:1">Cleanup skipped: AI unavailable — use Reclassify old cards</span>
         <button type="button" class="btn small" data-act="reclassify" ${ctx.state.busy?.reclassify ? "disabled" : ""}>${ctx.state.busy?.reclassify ? "Reclassifying…" : "Reclassify old cards"}</button></div>`
         : p.cleanup ? `<div class="row wrap" data-section="purchase-cleanup"><span style="flex:1">Cleaned up: ${p.cleanup.not_clothing} marked not clothing (restorable), ${p.cleanup.health} health items removed, ${p.cleanup.grouped} duplicates grouped</span>
         <button type="button" class="btn small" data-act="cleanupSeen">OK</button></div>` : ""}
       <div class="kv"><span>Last problem</span><span class="${p.last_error ? "state problem" : ""}">${lib.esc(p.last_error || "None")}</span></div>
       ${(p.unreadable || []).length ? `<div class="kv"><span>Couldn't read</span><span class="state problem">${lib.esc(unreadableText(lib, p.unreadable))}</span></div>
       <button type="button" class="btn" data-act="retryUnreadable" ${ctx.state.busy?.retry ? "disabled" : ""}>${ctx.state.busy?.retry ? "Reading again…" : "Retry unreadable emails"}</button>` : ""}`
    : `<div class="kv"><span>Status</span><span class="state unknown">Not connected</span></div>`;
  return `<h2 class="section-label">Purchase emails</h2><div class="card stack" data-section="purchase-emails">${rows}
    <a class="link" href="${CONFIG_PATH}" data-act="openConfig">${p.connected ? "Change in Configure" : "Connect in Configure"}</a></div>`;
}

export function render(ctx) {
  const { lib, data } = ctx;
  const zoneRows = (zones) => zones.map((z) => `
    <div class="health"><span class="dot" style="background:${lib.rgb(z.color)}"></span><span>${lib.esc(z.name)}</span>
      <span class="state unknown">${z.light_entity ? lib.esc(z.light_entity) : "No light"} · ${z.usage} garment${z.usage === 1 ? "" : "s"}</span></div>`).join("");
  const stations = (data.station_health || []).map((s) => {
    const state = !s.configured ? ["unknown", "Not set up"] : s.online === true ? ["ok", "Online"] : s.online === false ? ["problem", "Offline"] : ["unknown", "Unknown"];
    return `<div class="health"><span>${lib.esc(lib.stationLabel(s.station))}</span>
      <span class="sub">Last tap ${lib.esc(lib.timeAgo(s.last_tap))}</span><span class="state ${state[0]}">${state[1]}</span></div>`;
  }).join("");
  const w = data.wardrobe || {};
  const owners = Object.entries(data.owners || {});
  return `<div class="page-head"><h1>Settings</h1>
      <a class="btn" href="${CONFIG_PATH}" data-act="openConfig">Change in HA</a></div>
    <div class="two-col">
      <section>
        <h2 class="section-label">Wash bins</h2><div class="card">${zoneRows(data.wash_bins) || '<span class="hint">None</span>'}</div>
        <h2 class="section-label">Dry types</h2><div class="card">${zoneRows(data.dry_types) || '<span class="hint">None</span>'}</div>
      </section>
      <section>
        <h2 class="section-label">Stations</h2><div class="card">${stations}</div>
        <h2 class="section-label">Wardrobe</h2><div class="card stack">
          <div class="kv"><span>Wardrobe catalog</span><span>${w.enabled ? "On" : "Off"}</span></div>
          ${w.enabled ? `<div class="kv"><span>AI descriptions</span><span>${w.ai_entity ? lib.esc(w.ai_entity) : "Not set"}</span></div>
            <p class="hint" style="margin:0">When describing a garment, its photos and details are sent to the AI provider you chose.</p>`
            : `<p class="hint" style="margin:0">Turn on Wardrobe in the integration options to add photos, purchase details and AI descriptions.</p>`}
        </div>
        ${w.enabled && data.purchases ? purchaseRow(ctx) : ""}
        ${owners.length ? `<h2 class="section-label">People</h2><div class="card stack">${owners.map(([id, s]) => `
          <form data-submit="saveOwner" data-owner="${lib.esc(id)}" class="row">
            <span class="avatar lg">${lib.esc(lib.initials(ctx.personName(id)))}</span>
            <label class="field" style="flex:1">${lib.esc(ctx.personName(id))}: name for “work”
              <input class="input" name="work_label" maxlength="30" value="${lib.esc(s.work_label)}"></label>
            <button class="btn" style="align-self:flex-end">Save</button></form>`).join("")}</div>` : ""}
        <p class="hint">Laundry Hub ${lib.esc(data.version || "")}</p>
      </section>
    </div>`;
}

export const actions = {
  async reclassify(ctx) {
    ctx.state.busy = { ...(ctx.state.busy || {}), reclassify: true };
    ctx.render();
    const res = await ctx.ws("purchases/reclassify", {});
    ctx.state.busy = { ...(ctx.state.busy || {}), reclassify: false };
    if (res) ctx.toast(res.classified ? "Old cards reclassified" : "The AI is still unavailable");
    ctx.reload();
  },
  async cleanupSeen(ctx) {
    if (await ctx.ws("purchases/cleanup_seen", {})) ctx.reload();
  },
  async retryUnreadable(ctx) {
    ctx.state.busy = { ...(ctx.state.busy || {}), retry: true };
    ctx.render();
    const res = await ctx.ws("purchases/retry_unreadable", {});
    ctx.state.busy = { ...(ctx.state.busy || {}), retry: false };
    if (res) ctx.toast(`Read ${res.retried} email(s) again: ${res.new_cards || 0} new card(s)`);
    ctx.reload();
  },
  openConfig: (ctx) => ctx.navigate(CONFIG_PATH),
  async saveOwner(ctx, form) {
    const label = new FormData(form).get("work_label").toString().trim();
    if (await ctx.ws("owner/update", { owner: form.dataset.owner, work_label: label })) ctx.toast("Saved");
  },
};
