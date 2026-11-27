// Laundry Hub sidebar panel. Plain web component, no build step.
// All writes go through laundry_hub/* websocket commands (validated server-side).

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const STATION_LABEL = { laundry: "Laundry room", closet_1: "Closet 1", closet_2: "Closet 2" };
const STATE_LABEL = { new: "New", put_away: "Put away", in_wash: "In wash", drying: "Drying" };
const PAGES = ["garments", "enroll", "status"];

class LaundryHubPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._page = "garments";
    this._filter = { q: "", owner: "", bin: "", location: "" };
    this._selected = new Set();
    this._editing = null;
    this._message = null;
    this._enrollUid = "";
    this._unsub = null;
    this._photoUrls = {};
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
  }

  get hass() { return this._hass; }

  set narrow(value) { this._narrow = value; }
  set panel(value) { this._panel = value; }

  async _init() {
    const params = new URLSearchParams(window.location.search);
    const page = params.get("page");
    if (PAGES.includes(page)) this._page = page;
    if (params.get("uid")) this._enrollUid = params.get("uid");
    await this._load();
    try {
      this._unsub = await this._hass.connection.subscribeMessage(
        () => this._load(), { type: "laundry_hub/subscribe" });
    } catch (err) {
      this._flash(`Live updates unavailable: ${err.message || err}`, true);
    }
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub(); this._unsub = null; }
  }

  async _ws(type, payload = {}) {
    return this._hass.callWS({ type: `laundry_hub/${type}`, ...payload });
  }

  async _load() {
    try {
      this._data = await this._ws("data");
      if (!this._enrollUid && this._data.last_unknown) {
        this._enrollUid = this._data.last_unknown.uid;
      }
    } catch (err) {
      this._data = null;
      this._flash(err.message || String(err), true);
    }
    this._render();
  }

  async _do(type, payload, okMessage) {
    try {
      const result = await this._ws(type, payload);
      if (okMessage) this._flash(okMessage);
      await this._load();
      return result;
    } catch (err) {
      this._flash(err.message || String(err), true);
      return null;
    }
  }

  _flash(text, error = false) {
    this._message = { text, error };
    this._render();
    clearTimeout(this._msgTimer);
    this._msgTimer = setTimeout(() => { this._message = null; this._render(); }, 6000);
  }

  _zoneName(id) {
    const all = [...(this._data?.wash_bins || []), ...(this._data?.dry_types || [])];
    return all.find((z) => z.id === id)?.name ?? "—";
  }

  _personName(id) {
    if (!id) return "—";
    return this._data?.persons.find((p) => p.entity_id === id)?.name ?? id;
  }

  _where(g) {
    if (g.state === "put_away") return STATION_LABEL[g.location] || g.location;
    if (g.state === "in_wash") return `Wash: ${this._zoneName(g.wash_bin)}`;
    if (g.state === "drying") return `Dry: ${this._zoneName(g.dry_type)}`;
    return "Not placed yet";
  }

  _options(items, selected, { empty = null } = {}) {
    const opts = empty !== null ? [`<option value="">${esc(empty)}</option>`] : [];
    for (const item of items) {
      const value = item.id ?? item.entity_id ?? item.value;
      const label = item.name ?? item.label;
      opts.push(`<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`);
    }
    return opts.join("");
  }

  _filtered() {
    const f = this._filter;
    const q = f.q.trim().toLowerCase();
    return (this._data?.garments || [])
      .filter((g) => !q || g.name.toLowerCase().includes(q) || g.uids.some((u) => u.includes(q.toUpperCase())) || (g.notes || "").toLowerCase().includes(q))
      .filter((g) => !f.owner || g.owner === f.owner)
      .filter((g) => !f.bin || g.wash_bin === f.bin)
      .filter((g) => {
        if (!f.location) return true;
        if (f.location === "missing") return g.missing;
        if (STATION_LABEL[f.location]) return g.state === "put_away" && g.location === f.location;
        return g.state === f.location;
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  _garmentForm(g, prefillUid = "") {
    const d = this._data;
    const isNew = !g;
    g = g || { name: "", owner: "", wash_bin: d.wash_bins[0]?.id, dry_type: d.dry_types[0]?.id, notes: "", uids: [] };
    const lastStation = d.last_unknown?.station;
    const defaultLoc = isNew && STATION_LABEL[lastStation] && lastStation !== "laundry" ? lastStation : "";
    return `
      <form class="garment-form" data-id="${esc(g.id || "")}">
        ${isNew ? `<label>Tag UID<input name="uid" required value="${esc(prefillUid)}" placeholder="04A1B2C3D4E5F6"></label>` : ""}
        <label>Name<input name="name" required maxlength="100" value="${esc(g.name)}"></label>
        <label>Owner<select name="owner">${this._options(d.persons, g.owner || "", { empty: "No owner" })}</select></label>
        <label>Wash bin<select name="wash_bin">${this._options(d.wash_bins, g.wash_bin)}</select></label>
        <label>Dry type<select name="dry_type">${this._options(d.dry_types, g.dry_type)}</select></label>
        ${isNew ? `<label>Location<select name="location">${this._options(
          [{ value: "closet_1", label: "Closet 1" }, { value: "closet_2", label: "Closet 2" }], defaultLoc, { empty: "Not placed yet" })}</select></label>` : ""}
        <label class="wide">Notes<textarea name="notes" maxlength="1000">${esc(g.notes)}</textarea></label>
        ${!isNew ? `
          <div class="wide tags">Tags: ${g.uids.map((u) => `<span class="chip">${esc(u)}
            ${g.uids.length > 1 ? `<button type="button" class="link" data-action="remove-uid" data-uid="${esc(u)}" title="Remove tag">✕</button>` : ""}</span>`).join(" ")}
            <input name="add_uid" placeholder="Add / replacement tag UID"></div>
          <div class="wide">Photo: <input type="file" name="photo" accept="image/jpeg,image/png,image/webp">
            ${g.photo ? `<button type="button" class="link" data-action="delete-photo">Remove photo</button>` : ""}</div>` : ""}
        <div class="wide actions">
          <button type="submit" class="primary">${isNew ? "Create garment" : "Save"}</button>
          ${!isNew ? `<button type="button" data-action="cancel">Cancel</button>
            <button type="button" class="danger" data-action="delete">Delete</button>` : ""}
        </div>
      </form>`;
  }

  _renderGarments() {
    const d = this._data;
    const rows = this._filtered();
    const zonesFor = (field) => (field === "dry_type" ? d.dry_types : d.wash_bins);
    return `
      <div class="toolbar">
        <input id="q" placeholder="Search name, tag or notes" value="${esc(this._filter.q)}">
        <select id="f-owner">${this._options(d.persons, this._filter.owner, { empty: "All owners" })}</select>
        <select id="f-bin">${this._options(d.wash_bins, this._filter.bin, { empty: "All bins" })}</select>
        <select id="f-location">${this._options([
          { value: "closet_1", label: "Closet 1" }, { value: "closet_2", label: "Closet 2" },
          { value: "in_wash", label: "In wash" }, { value: "drying", label: "Drying" },
          { value: "new", label: "Not placed" }, { value: "missing", label: "Missing" },
        ], this._filter.location, { empty: "All locations" })}</select>
        <button data-action="add">Add garment</button>
      </div>
      ${this._editing === "new" ? `<div class="card">${this._garmentForm(null, "")}</div>` : ""}
      <div class="bulk">
        <span>${this._selected.size} selected</span>
        <select id="bulk-field"><option value="wash_bin">Wash bin</option><option value="dry_type">Dry type</option></select>
        <select id="bulk-to">${this._options(zonesFor(this._bulkField || "wash_bin"), "")}</select>
        <button data-action="bulk" ${this._selected.size ? "" : "disabled"}>Bulk reassign</button>
        <span class="hint">Or move everything from one zone:</span>
        <select id="bulk-from">${this._options(zonesFor(this._bulkField || "wash_bin"), "", { empty: "—" })}</select>
        <button data-action="bulk-from">Move all</button>
      </div>
      <table>
        <thead><tr><th><input type="checkbox" id="select-all"></th><th>Name</th><th>Owner</th><th>Wash bin</th><th>Dry type</th><th>Where</th><th>Last seen</th><th></th></tr></thead>
        <tbody>
          ${rows.map((g) => `
            <tr class="${g.missing ? "missing" : ""}">
              <td><input type="checkbox" class="sel" data-id="${esc(g.id)}" ${this._selected.has(g.id) ? "checked" : ""}></td>
              <td>${esc(g.name)}${g.missing ? ' <span class="chip warn">missing</span>' : ""}</td>
              <td>${esc(this._personName(g.owner))}</td>
              <td>${esc(this._zoneName(g.wash_bin))}</td>
              <td>${esc(this._zoneName(g.dry_type))}</td>
              <td>${esc(this._where(g))}</td>
              <td>${g.last_seen ? esc(new Date(g.last_seen.time).toLocaleString()) : "never"}</td>
              <td><button class="link" data-action="edit" data-id="${esc(g.id)}">Edit</button></td>
            </tr>
            ${this._editing === g.id ? `<tr><td colspan="8">${this._garmentForm(g)}</td></tr>` : ""}`).join("")}
          ${rows.length ? "" : `<tr><td colspan="8" class="empty">No garments match.</td></tr>`}
        </tbody>
      </table>`;
  }

  _renderEnroll() {
    const d = this._data;
    const last = d.last_unknown;
    return `
      <div class="card">
        <h2>Enroll a tag</h2>
        ${last ? `<p>Last unknown tag <b>${esc(last.uid)}</b> at ${esc(STATION_LABEL[last.station] || last.station)}
          (${esc(new Date(last.time).toLocaleString())}).</p>` : "<p>No unknown tag yet. Tap a new tag on any station.</p>"}
        ${d.wash_bins.length && d.dry_types.length ? this._garmentForm(null, this._enrollUid)
          : "<p class='error'>Add at least one wash bin and one dry type in Settings → Devices &amp; services → Laundry Hub.</p>"}
      </div>
      <div class="card">
        <h3>Recent unknown tags</h3>
        <table><thead><tr><th>UID</th><th>Station</th><th>Last seen</th><th>Taps</th><th></th></tr></thead><tbody>
          ${d.unknown_tags.map((t) => `<tr><td>${esc(t.uid)}</td><td>${esc(STATION_LABEL[t.station] || t.station)}</td>
            <td>${esc(new Date(t.last_seen).toLocaleString())}</td><td>${esc(t.count)}</td>
            <td><button class="link" data-action="use-uid" data-uid="${esc(t.uid)}">Use</button>
            <button class="link" data-action="dismiss" data-uid="${esc(t.uid)}">Dismiss</button></td></tr>`).join("")
            || '<tr><td colspan="5" class="empty">None.</td></tr>'}
        </tbody></table>
      </div>`;
  }

  _renderStatus() {
    const d = this._data;
    const g = d.garments;
    const list = (items) => items.length
      ? `<ul>${items.map((x) => `<li>${esc(x.name)}${x.owner ? ` <span class="muted">(${esc(this._personName(x.owner))})</span>` : ""}</li>`).join("")}</ul>`
      : '<p class="muted">Empty</p>';
    const closets = ["closet_1", "closet_2"].map((c) => {
      const items = g.filter((x) => x.state === "put_away" && x.location === c);
      return `<div class="card"><h3>${STATION_LABEL[c]} <span class="count">${items.length}</span></h3>${list(items)}</div>`;
    });
    const bins = d.wash_bins.map((b) => {
      const items = g.filter((x) => x.state === "in_wash" && x.wash_bin === b.id);
      return `<div class="card"><h3><span class="swatch" style="background: rgb(${b.color.map(Number).join(",")})"></span>${esc(b.name)} <span class="count">${items.length}</span>
        ${b.light_entity ? `<button class="link" data-action="flash" data-zone="${esc(b.id)}">Test light</button>` : ""}</h3>${list(items)}</div>`;
    });
    const dries = d.dry_types.map((z) => {
      const items = g.filter((x) => x.state === "drying" && x.dry_type === z.id);
      return `<div class="card"><h3><span class="swatch" style="background: rgb(${z.color.map(Number).join(",")})"></span>${esc(z.name)} <span class="count">${items.length}</span>
        ${z.light_entity ? `<button class="link" data-action="flash" data-zone="${esc(z.id)}">Test light</button>` : ""}</h3>${list(items)}</div>`;
    });
    const missing = g.filter((x) => x.missing);
    return `
      <h2>Closets</h2><div class="grid">${closets.join("")}</div>
      <h2>In the wash</h2><div class="grid">${bins.join("")}</div>
      <h2>Drying</h2><div class="grid">${dries.join("")}</div>
      <h2>Missing <span class="muted">(not seen for ${esc(d.missing_after_days)} days)</span></h2>
      <div class="card">${list(missing)}</div>`;
  }

  _render() {
    const root = this.shadowRoot;
    const d = this._data;
    const body = !d ? "<p>Loading… (is Laundry Hub set up?)</p>"
      : this._page === "enroll" ? this._renderEnroll()
      : this._page === "status" ? this._renderStatus()
      : this._renderGarments();
    root.innerHTML = `
      <style>
        :host { display: block; padding: 16px; font-family: var(--paper-font-body1_-_font-family, sans-serif);
          color: var(--primary-text-color); background: var(--primary-background-color); min-height: 100vh; box-sizing: border-box; }
        nav { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
        nav h1 { margin: 0 16px 0 0; font-size: 20px; }
        nav button.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        button { cursor: pointer; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; padding: 6px 12px; }
        button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); border: none; }
        button.danger { color: var(--error-color); }
        button.link { border: none; background: none; color: var(--primary-color); padding: 2px 4px; }
        button:disabled { opacity: .5; cursor: default; }
        input, select, textarea { font: inherit; padding: 6px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: inherit; }
        .toolbar, .bulk { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
        .toolbar #q { flex: 1; min-width: 180px; }
        table { width: 100%; border-collapse: collapse; background: var(--card-background-color); border-radius: 8px; overflow: hidden; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--divider-color); vertical-align: top; }
        tr.missing td { opacity: .75; }
        .card { background: var(--card-background-color); border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; box-shadow: var(--ha-card-box-shadow, none); }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
        .grid .card { margin: 0; }
        .garment-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px 16px; }
        .garment-form label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
        .garment-form .wide { grid-column: 1 / -1; }
        .actions { display: flex; gap: 8px; }
        .chip { display: inline-block; padding: 2px 8px; border-radius: 12px; background: var(--secondary-background-color); font-size: 12px; }
        .chip.warn { background: var(--warning-color); color: #000; }
        .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 6px; border: 1px solid var(--divider-color); }
        .count { float: right; font-weight: normal; color: var(--secondary-text-color); }
        .muted, .hint { color: var(--secondary-text-color); }
        .empty { text-align: center; color: var(--secondary-text-color); }
        .message { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; background: var(--success-color, #4caf50); color: #fff; }
        .message.error, .error { background: var(--error-color); color: #fff; padding: 8px 12px; border-radius: 6px; }
        h2 { font-size: 16px; margin: 16px 0 8px; } h3 { font-size: 15px; margin: 0 0 8px; }
        ul { margin: 0; padding-left: 18px; }
        @media (max-width: 600px) { th:nth-child(5), td:nth-child(5), th:nth-child(7), td:nth-child(7) { display: none; } }
      </style>
      <nav>
        <h1>Laundry</h1>
        ${PAGES.map((p) => `<button data-page="${p}" class="${p === this._page ? "active" : ""}">${p[0].toUpperCase() + p.slice(1)}</button>`).join("")}
      </nav>
      ${this._message ? `<div class="message ${this._message.error ? "error" : ""}">${esc(this._message.text)}</div>` : ""}
      ${body}`;
    this._bind();
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("nav button[data-page]").forEach((b) =>
      b.addEventListener("click", () => { this._page = b.dataset.page; this._editing = null; this._render(); }));
    const on = (sel, ev, fn) => root.querySelector(sel)?.addEventListener(ev, fn);
    on("#q", "input", (e) => { this._filter.q = e.target.value; this._renderKeepFocus("#q"); });
    on("#f-owner", "change", (e) => { this._filter.owner = e.target.value; this._render(); });
    on("#f-bin", "change", (e) => { this._filter.bin = e.target.value; this._render(); });
    on("#f-location", "change", (e) => { this._filter.location = e.target.value; this._render(); });
    on("#bulk-field", "change", (e) => { this._bulkField = e.target.value; this._render(); });
    const bulkField = root.querySelector("#bulk-field");
    if (bulkField) bulkField.value = this._bulkField || "wash_bin";
    on("#select-all", "change", (e) => {
      this._filtered().forEach((g) => (e.target.checked ? this._selected.add(g.id) : this._selected.delete(g.id)));
      this._render();
    });
    root.querySelectorAll("input.sel").forEach((cb) => cb.addEventListener("change", () => {
      cb.checked ? this._selected.add(cb.dataset.id) : this._selected.delete(cb.dataset.id);
      this._render();
    }));
    root.querySelectorAll("[data-action]").forEach((el) => el.addEventListener("click", (e) => this._action(e, el)));
    root.querySelectorAll("form.garment-form").forEach((form) => form.addEventListener("submit", (e) => {
      e.preventDefault();
      this._submit(form);
    }));
  }

  _renderKeepFocus(selector) {
    const pos = this.shadowRoot.querySelector(selector)?.selectionStart;
    this._render();
    const el = this.shadowRoot.querySelector(selector);
    if (el) { el.focus(); if (pos !== undefined) el.setSelectionRange(pos, pos); }
  }

  async _action(event, el) {
    const action = el.dataset.action;
    const form = el.closest("form");
    const garmentId = form?.dataset.id || el.dataset.id;
    switch (action) {
      case "add": this._editing = "new"; this._render(); break;
      case "edit": this._editing = this._editing === garmentId ? null : garmentId; this._render(); break;
      case "cancel": this._editing = null; this._render(); break;
      case "delete":
        if (confirm("Delete this garment?")) {
          if (await this._do("garment/delete", { garment_id: garmentId }, "Garment deleted")) this._editing = null;
        }
        break;
      case "remove-uid":
        await this._do("garment/update", { garment_id: garmentId, remove_uid: el.dataset.uid }, "Tag removed");
        break;
      case "delete-photo":
        await this._do("photo/delete", { garment_id: garmentId }, "Photo removed");
        break;
      case "bulk": {
        const field = this.shadowRoot.querySelector("#bulk-field").value;
        const to = this.shadowRoot.querySelector("#bulk-to").value;
        const res = await this._do("reassign", { field, to, garment_ids: [...this._selected] });
        if (res) { this._selected.clear(); this._flash(`Moved ${res.moved} garment(s)`); }
        break;
      }
      case "bulk-from": {
        const field = this.shadowRoot.querySelector("#bulk-field").value;
        const to = this.shadowRoot.querySelector("#bulk-to").value;
        const from = this.shadowRoot.querySelector("#bulk-from").value;
        if (!from) { this._flash("Pick a zone to move from", true); break; }
        const res = await this._do("reassign", { field, to, from });
        if (res) this._flash(`Moved ${res.moved} garment(s)`);
        break;
      }
      case "use-uid": this._enrollUid = el.dataset.uid; this._render(); break;
      case "dismiss": await this._do("unknown/dismiss", { uid: el.dataset.uid }, "Dismissed"); break;
      case "flash": await this._do("zone/flash", { zone_id: el.dataset.zone }, "Flashing"); break;
      default: break;
    }
  }

  async _submit(form) {
    const f = new FormData(form);
    const id = form.dataset.id;
    const val = (k) => (f.get(k) ?? "").toString();
    if (!id) {
      const payload = {
        uid: val("uid").trim(), name: val("name").trim(), wash_bin: val("wash_bin"), dry_type: val("dry_type"),
        owner: val("owner") || null, notes: val("notes"), location: val("location") || null,
      };
      const res = await this._do("garment/create", payload, `Created ${payload.name}`);
      if (res) { this._editing = null; this._enrollUid = ""; }
      return;
    }
    const payload = {
      garment_id: id, name: val("name").trim(), wash_bin: val("wash_bin"), dry_type: val("dry_type"),
      owner: val("owner") || null, notes: val("notes"),
    };
    if (val("add_uid").trim()) payload.add_uid = val("add_uid").trim();
    const res = await this._do("garment/update", payload, "Saved");
    const file = f.get("photo");
    if (res && file && file.size) await this._uploadPhoto(id, file);
    if (res) { this._editing = null; this._render(); }
  }

  async _uploadPhoto(id, file) {
    if (file.size > 2 * 1024 * 1024) { this._flash("Photo must be 2 MB or smaller", true); return; }
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    await this._do("photo/upload", { garment_id: id, content_type: file.type, data }, "Photo saved");
  }
}

if (!customElements.get("laundry-hub-panel")) {
  customElements.define("laundry-hub-panel", LaundryHubPanel);
}
