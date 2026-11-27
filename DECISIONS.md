# Decisions (v0.2.0)

Choices made while building v0.2 without asking. Each line: decision, then reason.

1. **Store v2 → v3 is a major version, and a full v2 backup is written first** (`.storage/laundry_hub.v2.backup`). The spec asked for v3, and HA refuses to load a newer major version, so the backup is what makes a downgrade to v0.1.2 possible.
2. **Unknown storage keys are kept** (`Garment.extra`). This makes the migration and every later save lossless, even for keys a newer version wrote.
3. **Photos with Wardrobe off: one cover photo, as in v0.1.** "Off = v0.1.2 behaviour" and v0.1 already had one photo per garment. Uploads use the new resize/strip pipeline either way.
4. **Photo uploads use a multipart HTTP view** (`POST /api/laundry_hub/photos`, admin only), not base64 over the websocket. A 10 MB upload doesn't fit reliably in a websocket message. The old base64 command still works.
5. **Photo URLs are pre-signed in `laundry_hub/data`** (24 h, tied to the session's refresh token). `<img>` can't send auth headers, and signing in bulk avoids one round trip per thumbnail.
6. **AI attachments go through a non-browsable `media_source` platform.** `ai_task.async_generate_data` only accepts media-source IDs that resolve to local files. The platform resolves only photos that belong to the named garment.
7. **AI Task is called through its Python API with a selector-built `structure`**, the same way the `ai_task.generate_data` service builds it, then re-validated against `taxonomy.py`. The provider validates the shape; we validate the vocabulary.
8. **Description provenance is per field: `ai`, `user` or `import`.** Re-describing skips `user` and `import` fields, because purchase/import data beats an AI guess as the spec says. "Reset to AI" restores the last AI value from `description_ai`.
9. **"Needs review" = any of type, subtype, colours, pattern, material, formality, warmth or seasons unset, or an AI field with confidence < 0.7.** Values below 0.5 are already stored as null; 0.7 catches shaky ones.
10. **Taxonomy remap runs on every load**, mapping old values to the closest valid one or null and logging the change. The spec asked for this when the taxonomy changes; it costs nothing when nothing changed.
11. **Product-page attributes are scanned for vocabulary terms** ("95% Cotton, 5% Elastane" → cotton, spandex blend). Product pages use free text, not our enums.
12. **URL import downloads images into memory as a preview token** (30 min, at most 24 previews), and only `import/apply` writes files. The spec says nothing is written until Save.
13. **Every redirect hop and image host passes the SSRF guard again**, with redirects followed manually (at most 5). A public page could otherwise redirect to a private address.
14. **New garments get `lifecycle: active`, and the Closet hides non-active garments unless "Archived" is on.** The chip only appears when an archived garment exists, so it never shows in v0.2.
15. **Per-owner settings (the "work" label) live in the catalog store**, and the panel's Settings tab can edit the label. It isn't part of the config flow and belongs to people, not the entry. `body_profile` and `schedule` are reserved (null).
16. **Station health "online" = any of the station device's entities isn't `unavailable`.** That's how ESPHome marks a device offline. With no device selected it shows "Not set up".
17. **Recent taps (last 10) are stored on each garment** (`recent_taps`). The detail page needs them, and HA's logbook isn't queryable per garment.
18. **Add flow creates the garment when you leave step 2.** Photos and imports need a garment id. Abandoning step 3 still leaves a valid garment.
19. **Dry type defaults to the last choice for that bin**, remembered in the browser's `localStorage` (the spec's "remember the last choice per bin"). It's a per-device convenience, not shared state.
20. **Panel modules are loaded with dynamic `import()` carrying the same `?v=<version>`.** Static imports can't carry the cache-busting query.
21. **No web font is downloaded.** Inter is used if installed, otherwise system-ui. This keeps "no network fetches" true.
22. **Huge camera photos are scaled to 2400 px in the browser before upload, but only when over 9.5 MB.** This keeps phones under the server's 10 MB cap; the server still does the real resize and strip.
23. **New actions `import_product_url` and `describe_garment` are registered even with Wardrobe off**, and refuse with `wardrobe_disabled`. HA registers services at startup, before options are known.
24. **Pantry Hub palette values in `docs/design/tokens.md` other than the four in the mockup** (pressed #8A4219) are my pick, matching the Laundry ratios.
