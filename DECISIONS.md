# Decisions (v0.2.1)

Choices made while building v0.2.1 without asking. Each line: decision, then reason.

1. **`aioimaplib>=2.0.1,<3`**, not an exact pin. Core IMAP pins `==2.0.1`; a range accepts that version and won't conflict when core moves to a 2.0.x patch.
2. **The app password is stored in the config entry's data, in plain text**, the same way core IMAP stores its password. HA has no secret store for integrations.
3. **aioimaplib's own DEBUG/INFO logging is muted while a Laundry Hub mail session is open.** It echoes server data, including message bodies and the login name. A side effect: core IMAP's debug lines are also dropped during those few seconds.
4. **`EXAMINE` sets aioimaplib's state to SELECTED by hand.** aioimaplib only tracks `SELECT`, and `UID SEARCH`/`FETCH` refuse to run otherwise. `SELECT` would open the mailbox read-write.
5. **The media source is registered directly in media_source's lazy platform cache, not as a `media_source.py` platform.** Any platform file is listed at the media browser's root. This touches a private attribute. If HA changes it, Laundry Hub logs a warning, AI descriptions stop attaching photos, and a test fails in CI.
6. **DNS pinning uses a one-off connector whose resolver only knows the checked host and addresses.** The URL keeps its hostname, so the Host header, SNI and certificate check stay normal. Each fetch opens its own small session (no connection reuse), which is fine for a handful of requests.
7. **With Wardrobe on, Configure opens a menu (Main settings, Purchase emails, …); with it off, the old settings form opens directly.** Nothing changes for people who don't use Wardrobe.
8. **Step A refuses to connect when no AI Task entity is set** (`no_ai_entity`). Without one, emails could be fetched but never read, which would be surprising.
9. **Generic IMAP asks for host, port and folder on a second step.** HA flows can't show or hide fields based on another field.
10. **Store counts use the same search as the real checks** (senders, last 90 days, order words in the subject), so a count matches what could become cards. If counting fails, store names are shown without counts and the flow continues.
11. **37 stores, with each Gap Inc. brand as its own entry**, so labels read "Old Navy — 6 emails". A custom domain is shown by name (`shop.example.com` → "Example").
12. **Sender domains match exactly or as a subdomain.** IMAP `FROM` and Gmail `from:` match substrings, so `news@oldnavy.com.evil.net` comes back from the search; the header check rejects it before any body is fetched.
13. **One card per item**, with the quantity kept on the card.
14. **A known order (same store and order reference) is only ever updated.** Later emails fill empty fields and add a photo if the card has none, but never create cards: shipping emails often list only part of an order.
15. **An item whose `is_apparel` is missing counts as clothing.** Dropping on missing information would lose real garments; "Not clothing" fixes the rest.
16. **Incremental search window = days since the last check + 2, at least 7**, then only UIDs above the last one handled. If the AI fails, the run stops at that message and keeps the old window, so it retries and loses nothing. An unfinished backfill is simply repeated; Message-IDs de-duplicate.
17. **Backfill "Nothing" records the server's next UID** and only reads mail that arrives afterwards.
18. **At most 200 messages per check; the last 3000 Message-IDs are remembered.** This keeps a 365-day backfill and the store file bounded.
19. **Backoff after errors: interval × 2^failures, at most 6 hours.** "Check now" ignores the backoff.
20. **Dismissing or auto-archiving a card deletes the card's photo; matching copies it.** The garment's copy is never touched.
21. **"Not clothing" remembers the normalized product name per store**, and items with that name from that store are skipped from then on.
22. **The Add flow's order question ranks cards by date only**, because owner and bin aren't chosen yet at that step. Full ranking (date, the owner's usual stores, colour versus bin) is in `purchases/suggest` for later use.
23. **Matching fills brand and size only if you didn't set them, and replaces the purchase details with `source: email`.** It never renames a garment from Closet; in Add, the name is pre-filled and stays editable.
24. **Disconnect keeps the store choices and options**, so reconnecting remembers them. It deletes the credentials, UID tracking, seen Message-IDs and the repair issue.
25. **`sensor.laundry_hub_unmatched_purchases` always exists** (0 when not connected). No notifications, as asked.
26. **The first check runs 60 seconds after setup**, then on the interval. Checking during HA startup would slow it down.
27. **Store schema 3.1 → 3.2 is a minor bump without a backup file.** v0.2.0 can load 3.2 (it ignores the new keys). Unknown top-level keys are now kept on save.
28. **HEIC is converted at up to 4000 px on the long edge**, then the usual 9.5 MB check applies. The server resizes to 1600 px anyway.
29. **Diagnostics show only the number of custom sender domains**, not the domains: a small shop's domain can say a lot about someone.
30. **"Check now" in Configure ends with a message** (new and updated counts). HA flows can't show a toast and return to the menu.
31. **v0.2.0's open questions, as answered:** one cover photo with Wardrobe off and 0.7 for Needs review are unchanged; auto-describe after a URL import adds the first photo is kept and now has its own test; the work label stays editable in the panel.

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
