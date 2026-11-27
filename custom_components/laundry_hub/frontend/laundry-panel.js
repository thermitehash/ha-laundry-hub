// Laundry Hub sidebar panel (v0.2): phone-first shell. No build step, no dependencies.
// Views live in sibling modules loaded with the same ?v=<version> cache buster.
// Every write goes through laundry_hub/* websocket commands (validated server-side).

const BASE = new URL(".", import.meta.url).href;
const VERSION = new URL(import.meta.url).search; // "?v=0.2.0"
const MODULES = ["lib", "home", "closet", "add", "settings", "detail"];
const TABS = [
  { id: "home", label: "Home", icon: "home" },
  { id: "closet", label: "Closet", icon: "closet" },
  { id: "add", label: "Add", icon: "add" },
  { id: "settings", label: "Settings", icon: "settings" },
];

class LaundryHubPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.mods = null;
    this.data = null;
    this.state = {
      tab: "home",
      garment: null,
      sheet: null,
      menu: false,
      editing: null,
      filters: { q: "", owner: [], location: [], bin: [], needsPhoto: false, needsReview: false, archived: false, tax: {} },
      select: { on: false, ids: new Set() },
      add: { step: 1, uid: "", manual: false, form: {}, garmentId: null, preview: null, busy: false, seenAt: null },
      toast: null,
      reconnecting: false,
      busy: {},
    };
    this._unsub = null;
    this._backoff = 1000;
    this._reloadTimer = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    this.toggleAttribute("dark", Boolean(hass?.themes?.darkMode));
    if (first) this._init();
  }
  get hass() { return this._hass; }
  set narrow(value) { this._narrow = value; }
  set panel(value) { this._panel = value; }

  async _init() {
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="${BASE}tokens.css${VERSION}">
      <link rel="stylesheet" href="${BASE}panel.css${VERSION}">
      <div id="root" aria-live="polite"><p style="padding:24px">Loading…</p></div>`;
    const loaded = await Promise.all(MODULES.map((name) => import(`${BASE}${name}.js${VERSION}`)));
    this.mods = Object.fromEntries(MODULES.map((name, i) => [name, loaded[i]]));
    this._readUrl();
    this._bind();
    await this._load();
    this._subscribe();
    this._connListeners();
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub(); this._unsub = null; }
    clearTimeout(this._retryTimer);
  }

  // --- URL state (tab, garment) ------------------------------------------
  _readUrl() {
    const params = new URLSearchParams(window.location.search);
    const page = params.get("page");
    const tab = params.get("tab") || (page === "enroll" ? "add" : page === "garments" ? "closet" : page === "status" ? "home" : null);
    if (TABS.some((t) => t.id === tab)) this.state.tab = tab;
    if (params.get("uid")) { this.state.tab = "add"; this.state.add.uid = params.get("uid"); this.state.add.step = 2; }
    if (params.get("garment")) this.state.garment = params.get("garment");
  }

  _writeUrl() {
    const params = new URLSearchParams();
    if (this.state.tab !== "home") params.set("tab", this.state.tab);
    if (this.state.garment) params.set("garment", this.state.garment);
    const query = params.toString();
    const url = `${window.location.pathname}${query ? `?${query}` : ""}`;
    if (url !== `${window.location.pathname}${window.location.search}`) window.history.replaceState(null, "", url);
  }

  // --- data & live updates ----------------------------------------------------
  async _load() {
    try {
      this.data = await this.hass.callWS({ type: "laundry_hub/data" });
      this.toggleAttribute("follow-ha", Boolean(this.data.follow_ha_theme));
    } catch (err) {
      this.toast(err.message || String(err), true);
    }
    this.render();
  }

  _scheduleReload() {
    clearTimeout(this._reloadTimer);
    this._reloadTimer = setTimeout(() => this._load(), 150);
  }

  async _subscribe() {
    try {
      this._unsub = await this.hass.connection.subscribeMessage((msg) => this._onMessage(msg), { type: "laundry_hub/subscribe" });
      this._backoff = 1000;
      if (this.state.reconnecting) { this.state.reconnecting = false; this._load(); }
    } catch (_err) {
      this.state.reconnecting = true;
      this.render();
      this._retryTimer = setTimeout(() => this._subscribe(), this._backoff);
      this._backoff = Math.min(this._backoff * 2, 30000);
    }
  }

  _connListeners() {
    const conn = this.hass.connection;
    if (!conn || typeof conn.addEventListener !== "function") return;
    conn.addEventListener("disconnected", () => { this.state.reconnecting = true; this.render(); });
    conn.addEventListener("ready", () => { this.state.reconnecting = false; this._load(); });
  }

  _onMessage(msg) {
    if (msg.tap) this.mods.add.onTap(this.ctx(), msg.tap);
    if (msg.changed || msg.tap) this._scheduleReload();
  }

  // --- context passed to views ---------------------------------------------------
  ctx() {
    const data = this.data || { garments: [], wash_bins: [], dry_types: [], persons: [], unknown_tags: [] };
    const zones = [...(data.wash_bins || []), ...(data.dry_types || [])];
    const zone = (id) => zones.find((z) => z.id === id);
    return {
      lib: this.mods.lib,
      mods: this.mods,
      data,
      state: this.state,
      hass: this.hass,
      zone,
      zoneName: (id) => zone(id)?.name ?? "—",
      person: (id) => (data.persons || []).find((p) => p.entity_id === id),
      personName: (id) => (id ? (data.persons || []).find((p) => p.entity_id === id)?.name ?? id.replace("person.", "") : "No owner"),
      garment: (id) => (data.garments || []).find((g) => g.id === id),
      wardrobe: Boolean(data.wardrobe?.enabled),
      ws: (type, payload) => this.ws(type, payload),
      update: (patch) => { Object.assign(this.state, patch || {}); this.render(); },
      render: () => this.render(),
      reload: () => this._load(),
      toast: (text, error) => this.toast(text, error),
      go: (tab, extra = {}) => this.go(tab, extra),
      open: (id) => { this.state.garment = id; this.state.menu = false; this.state.editing = null; this.render(); },
      close: () => { this.state.garment = null; this.state.menu = false; this.state.editing = null; this.render(); },
      upload: (garmentId, files) => this.upload(garmentId, files),
      navigate: (path) => { window.history.pushState(null, "", path); window.dispatchEvent(new CustomEvent("location-changed")); },
    };
  }

  async ws(type, payload = {}) {
    try {
      return await this.hass.callWS({ type: `laundry_hub/${type}`, ...payload });
    } catch (err) {
      this.toast(err.message || String(err), true);
      return null;
    }
  }

  async upload(garmentId, files) {
    const list = Array.from(files || []);
    let last = null;
    for (const original of list) {
      const file = await this.mods.lib.downscaleIfHuge(original);
      const form = new FormData();
      form.append("garment_id", garmentId);
      form.append("file", file, file.name || "photo.jpg");
      try {
        const resp = await this.hass.fetchWithAuth("/api/laundry_hub/photos", { method: "POST", body: form });
        const body = await resp.json();
        if (!resp.ok) { this.toast(body.message || "Upload failed", true); break; }
        last = body;
      } catch (err) {
        this.toast(err.message || "Upload failed", true);
        break;
      }
    }
    if (last) { this.toast(list.length > 1 ? "Photos saved" : "Photo saved"); this._load(); }
    return last;
  }

  go(tab, extra = {}) {
    this.state.tab = tab;
    this.state.garment = null;
    this.state.sheet = null;
    this.state.menu = false;
    if (extra.filters) this.state.filters = { ...this.state.filters, ...extra.filters };
    if (tab === "add" && extra.reset !== false) this.mods.add.reset(this.state);
    this.render();
    window.scrollTo?.(0, 0);
  }

  toast(text, error = false) {
    this.state.toast = { text, error };
    this.render();
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this.state.toast = null; this.render(); }, error ? 7000 : 3500);
  }

  // --- rendering ---------------------------------------------------------------
  render() {
    if (!this.mods) return;
    const root = this.shadowRoot.getElementById("root");
    if (!root) return;
    const { lib } = this.mods;
    const ctx = this.ctx();
    const active = this.shadowRoot.activeElement;
    const keep = active?.dataset?.keep;
    const caret = keep && "selectionStart" in active ? active.selectionStart : null;
    const detailScroll = this.shadowRoot.querySelector(".detail")?.scrollTop ?? 0;
    const sheetScroll = this.shadowRoot.querySelector(".sheet")?.scrollTop ?? 0;

    const view = this.mods[this.state.tab] || this.mods.home;
    const tabs = (cls) => TABS.map((t) => `
      <button type="button" data-act="tab" data-tab="${t.id}" ${this.state.tab === t.id ? 'aria-current="page"' : ""}>
        ${cls === "tabbar" ? lib.ICON[t.icon] : ""}<span>${t.label}</span></button>`).join("");
    const body = this.data ? view.render(ctx) : `<div class="card empty"><p>Laundry Hub isn't loaded yet.</p></div>`;
    root.innerHTML = `
      <div class="app">
        <div class="topbar"><strong style="font-size:20px">Laundry</strong><nav class="segmented" aria-label="Sections">${tabs("segmented")}</nav></div>
        <main>${body}</main>
        <nav class="tabbar" aria-label="Sections">${tabs("tabbar")}</nav>
        ${this.state.garment && this.data ? this.mods.detail.render(ctx) : ""}
        ${this.state.sheet && this.data ? this.mods.closet.renderSheet(ctx) : ""}
        ${this.state.toast ? `<div class="toast ${this.state.toast.error ? "error" : ""}" role="status">${lib.esc(this.state.toast.text)}</div>` : ""}
        ${this.state.reconnecting ? `<div class="pill-status" role="status">Reconnecting…</div>` : ""}
      </div>`;
    if (keep) {
      const el = this.shadowRoot.querySelector(`[data-keep="${keep}"]`);
      if (el) { el.focus(); if (caret !== null && el.setSelectionRange) el.setSelectionRange(caret, caret); }
    }
    const detail = this.shadowRoot.querySelector(".detail");
    if (detail) detail.scrollTop = detailScroll;
    const sheet = this.shadowRoot.querySelector(".sheet");
    if (sheet) sheet.scrollTop = sheetScroll;
    this._writeUrl();
  }

  // --- events (delegated) --------------------------------------------------------
  _handlers() {
    const merged = {};
    for (const name of ["home", "closet", "add", "settings", "detail"]) Object.assign(merged, this.mods[name].actions || {});
    merged.tab = (ctx, el) => ctx.go(el.dataset.tab);
    merged.toastClose = (ctx) => ctx.update({ toast: null });
    return merged;
  }

  _bind() {
    const handlers = this._handlers();
    const run = async (name, el, event) => {
      const fn = handlers[name];
      if (!fn) return;
      try {
        await fn(this.ctx(), el, event);
      } catch (err) {
        this.toast(err.message || String(err), true);
      }
    };
    this.shadowRoot.addEventListener("click", (event) => {
      const el = event.target.closest("[data-act]");
      if (!el || el.disabled) return;
      if (el.tagName === "A") event.preventDefault();
      run(el.dataset.act, el, event);
    });
    this.shadowRoot.addEventListener("input", (event) => {
      const el = event.target.closest("[data-input]");
      if (el) run(el.dataset.input, el, event);
    });
    this.shadowRoot.addEventListener("change", (event) => {
      const el = event.target.closest("[data-change]");
      if (el) run(el.dataset.change, el, event);
    });
    this.shadowRoot.addEventListener("submit", (event) => {
      const form = event.target.closest("form[data-submit]");
      if (!form) return;
      event.preventDefault();
      run(form.dataset.submit, form, event);
    });
    this.shadowRoot.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (this.state.sheet) { this.state.sheet = null; this.render(); } else if (this.state.garment) { this.ctx().close(); }
    });
  }
}

if (!customElements.get("laundry-hub-panel")) {
  customElements.define("laundry-hub-panel", LaundryHubPanel);
}
