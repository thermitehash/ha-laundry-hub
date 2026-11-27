// Settings: read-only summary with links to HA's integration options, station health,
// per-owner "work" label (not part of the config flow, so editable here).

const CONFIG_PATH = "/config/integrations/integration/laundry_hub";

export function render(ctx) {
  const { lib, data } = ctx;
  const zoneRows = (zones) => zones.map((z) => `
    <div class="health"><span class="dot" style="background:${lib.rgb(z.color)}"></span><span>${lib.esc(z.name)}</span>
      <span class="state unknown">${z.light_entity ? lib.esc(z.light_entity) : "No light"} · ${z.usage} garment${z.usage === 1 ? "" : "s"}</span></div>`).join("");
  const stations = (data.station_health || []).map((s) => {
    const state = !s.configured ? ["unknown", "Not set up"] : s.online === true ? ["ok", "Online"] : s.online === false ? ["problem", "Offline"] : ["unknown", "Unknown"];
    return `<div class="health"><span>${lib.esc(lib.STATION_LABEL[s.station] || s.station)}</span>
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
  openConfig: (ctx) => ctx.navigate(CONFIG_PATH),
  async saveOwner(ctx, form) {
    const label = new FormData(form).get("work_label").toString().trim();
    if (await ctx.ws("owner/update", { owner: form.dataset.owner, work_label: label })) ctx.toast("Saved");
  },
};
