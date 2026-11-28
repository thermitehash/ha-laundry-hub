// Shared helpers for the Laundry panel views. No dependencies.

export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

export const STATION_LABEL = { laundry: "Laundry room", closet_1: "Closet 1", closet_2: "Closet 2" };
// Station name for display; phone taps (v0.2.3) arrive as "phone:<device>".
export const stationLabel = (station) => {
  const s = String(station || "");
  if (s.startsWith("phone:")) return `Phone · ${s.slice(6) || "phone"}`;
  return STATION_LABEL[s] || s;
};
export const LOCATION_LABEL = {
  closet_1: "Closet 1", closet_2: "Closet 2", in_wash: "In wash", drying: "Drying",
  new: "Not placed", missing: "Missing",
};
export const WEAR_FOR_LABEL = {
  any: "Anything", work: "Work", off_duty: "Never to work", occasion: "Occasions", athletic: "Athletic",
};
export const FIELD_LABEL = {
  type: "Type", subtype: "Kind", colors: "Colour", pattern: "Pattern", sleeve_length: "Sleeve",
  neckline: "Neckline", length: "Length", fit: "Fit", rise: "Rise", material: "Material",
  formality: "Formality", warmth: "Warmth", seasons: "Seasons", occasions: "Occasions",
  style_tags: "Style", summary: "Summary", wear_for: "Wear for", uniform: "Uniform",
  owner: "Owner", wash_bin: "Wash bin", dry_type: "Dry type",
};

const svg = (d, size = 22, extra = "") =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;

export const ICON = {
  home: svg('<path d="M3 11l9-7 9 7v9H3z"/>'),
  closet: svg('<path d="M12 4l8 5-8 5-8-5zM4 14l8 5 8-5"/>'),
  add: svg('<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>'),
  settings: svg('<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>', 22, 'stroke-width="2.4"'),
  back: svg('<path d="M15 6l-6 6 6 6"/>', 20, 'stroke-width="2.2"'),
  more: svg('<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>', 20),
  search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>', 18),
  alert: svg('<path d="M12 8v5M12 17h.01"/><circle cx="12" cy="12" r="9"/>', 20),
  chevron: svg('<path d="M9 6l6 6-6 6"/>', 18),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>', 18),
  check: svg('<path d="M5 12l5 5 9-10"/>', 16, 'stroke-width="3"'),
  tag: svg('<path d="M6 8a8 8 0 0 1 0 8M9.5 9.5a4 4 0 0 1 0 5M14 4l6 6-8 10-6-6z"/>', 40, 'stroke-width="1.8"'),
  camera: svg('<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>', 20),
  image: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>', 20),
  link: svg('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>', 20),
  lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>', 12, 'class="lock" stroke-width="2.4"'),
  sparkle: svg('<path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4zM19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>', 18),
  filter: svg('<path d="M4 6h16M7 12h10M10 18h4"/>', 18),
};

export const initials = (name) => {
  const words = String(name || "?").trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2);
  return letters.toUpperCase();
};

export const rgb = (color) => (Array.isArray(color) ? `rgb(${color.map(Number).join(",")})` : color || "#9aa5a8");

// Readable text on a coloured tile (WCAG relative luminance).
export const onColor = (color) => {
  const [r, g, b] = Array.isArray(color) ? color.map(Number) : [154, 165, 168];
  const lin = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  const lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return lum > 0.4 ? "#1C1B19" : "#FFFFFF";
};

export const timeAgo = (iso) => {
  if (!iso) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
};

export const money = (amount, currency) => {
  if (amount === null || amount === undefined || amount === "") return "";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD" }).format(amount);
  } catch (_err) {
    return `${amount} ${currency || ""}`.trim();
  }
};

export const capitalize = (text) => String(text || "").replace(/^./, (c) => c.toUpperCase());

// Where a garment is, as a short label and whether it needs attention.
export const whereLabel = (g, zoneName) => {
  if (g.missing) return { text: "Missing", warn: true };
  if (g.state === "put_away") return { text: STATION_LABEL[g.location] || g.location, warn: false };
  if (g.state === "in_wash") return { text: "In wash", warn: false, long: `In wash · ${zoneName(g.wash_bin)}` };
  if (g.state === "drying") return { text: "Drying", warn: false, long: `Drying · ${zoneName(g.dry_type)}` };
  return { text: "Not placed", warn: false };
};

// Small, labelled tile used when a garment has no photo.
export const tile = (g, bin, size = "") => {
  const color = bin ? bin.color : [154, 165, 168];
  return `<div class="${size}" style="background:${rgb(color)};color:${onColor(color)};width:100%;height:100%;display:flex;align-items:center;justify-content:center" aria-hidden="true">${esc(initials(g.name))}</div>`;
};

export const coverThumb = (g) => (g.photo_urls && g.photo_urls[0] ? g.photo_urls[0].thumb : null);

export async function downscaleIfHuge(file, maxBytes = 9.5 * 1024 * 1024) {
  // The server resizes everything; this only keeps huge camera files under the 10 MB cap.
  if (file.size <= maxBytes || !file.type.startsWith("image/") || typeof createImageBitmap !== "function") return file;
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
}

// iPhones save HEIC/HEIF, which the server (Pillow) can't read. Convert it to
// JPEG in the browser; Safari can decode it, most other browsers can't.
export const HEIC_UNSUPPORTED =
  "This photo format isn't supported — change your iPhone camera setting to Most Compatible, or take the photo from the Add screen.";

export const isHeic = (file) =>
  /^image\/hei[cf](-sequence)?$/i.test(file?.type || "") || /\.(heic|heif)$/i.test(file?.name || "");

export async function heicToJpeg(file, maxEdge = 4000) {
  if (typeof createImageBitmap !== "function") throw new Error(HEIC_UNSUPPORTED);
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch (_err) {
    throw new Error(HEIC_UNSUPPORTED);
  }
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
  if (!blob) throw new Error(HEIC_UNSUPPORTED);
  const base = String(file.name || "photo").replace(/\.[^.]*$/, "") || "photo";
  return new File([blob], `${base}.jpg`, { type: "image/jpeg" });
}

// Everything the browser does to a photo before upload.
export async function prepareUpload(file) {
  if (isHeic(file)) return downscaleIfHuge(await heicToJpeg(file));
  return downscaleIfHuge(file);
}

// --- purchase cards (v0.2.1) ------------------------------------------------------

export const shortDate = (iso) => {
  if (!iso) return "";
  const d = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

export const unmatchedCards = (data) => (data.purchases?.cards || []).filter((c) => c.status === "new");

// One purchase card: photo, name, store · date · price.
export function purchaseCard(card, { act = "", attrs = "", pressed = null } = {}) {
  const thumb = card.photo_urls && card.photo_urls[0] ? card.photo_urls[0].thumb : null;
  const meta = [card.store, shortDate(card.order_date), money(card.unit_price, card.currency)].filter(Boolean).join(" · ");
  const tag = act ? "button" : "div";
  return `<${tag} ${act ? `type="button" data-act="${act}"` : ""} class="pcard" data-card="${esc(card.id)}" ${attrs} ${pressed === null ? "" : `aria-pressed="${pressed}"`}>
    <div class="pmedia">${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : `<span aria-hidden="true">${esc(initials(card.name))}</span>`}</div>
    <div class="pbody"><span class="name">${esc(card.name)}</span><span class="meta">${esc(meta)}</span>
      ${card.size || card.color ? `<span class="meta">${esc([card.color, card.size].filter(Boolean).join(" · "))}</span>` : ""}</div></${tag}>`;
}
