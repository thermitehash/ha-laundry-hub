# Decisions (v0.2.4)

Choices made while building v0.2.4 without asking. Each line: decision, then reason.

1. **Heather's answers to the v0.2.3 questions:**
   - (a) Members get AI descriptions only through the automatic describe after the first photo on their own garment. It runs server-side with the integration's AI Task entity. Manual describe and re-describe, `describe_fields` and `reset_field` are admin-only.
   - (b) Members still don't see unmatched purchase cards.
   - (c) Members may enrol garments for themselves.
   - (d) Phone taps still can't move a garment to another bin.
2. **Placeholder fix: with Wardrobe off, the options flow opens the `settings` step (no placeholders) instead of `init`.** `init` is now the Wardrobe-on menu only, so its "Purchase emails: {status}" always has its value. The two steps share the same handler and fields.
3. **The placeholder test records every form, menu and abort our flows render** (by wrapping HA's `async_show_form`, `async_show_menu` and `async_abort`) and checks them against `strings.json`. A coverage check fails if any step, or any placeholder-bearing abort, in `strings.json` is never rendered, so a new step can't skip the check. Repair issues are checked too.
4. **Member enrolment needs a recent phone tap of that tag by that member.** The unknown tag records `tapped_by` (the HA user id), and "recent" means within 30 minutes. A station tap clears `tapped_by`. That is how "a member sees only the unknown tag they just tapped" and "uids other than the tapped one stay admin-only" are enforced.
5. **A member sees at most one unknown tag: their latest phone tap within the window.** "Just tapped" means one tag. Older taps go back to the admins' list.
6. **The member's owner is forced on create**: no owner, or their own owner, is accepted; any other value gets `owner_not_allowed`, and a member without a mapping gets `no_owner_mapping`. A tag that isn't theirs gets `tag_not_yours`. Admin-only fields (`location`, plus brand, purchase and uniform, which `garment/create` never takes) get `unauthorized`.
7. **`tapped_by` never leaves the server for members** (removed from their view) **and is removed from diagnostics.** It's another HA user's id.
8. **The phone tap's unknown answer adds `can_enroll` and `owner` (additive).** The app knows straight away whether to offer "Add it" and for whom.
9. **`member_enroll` is listed whenever App access is Household**, as the prompt says, even for an admin caller. It describes the server, not the caller.
10. **Two v0.2.3 test assertions changed, as direct results of this release:** the member-allowed command set (create added; describe_fields and reset_field removed), and the exact unknown-tap answer (new fields). A third replaced a hard-coded "0.2.3" with `VERSION`. Everything else in the suite runs unchanged.
11. **Purchase import (Heather's install, stuck on one Narvar email): failures are tracked per Message-ID.** Each failure counts; on the 3rd the email is marked unreadable (Message-ID, sender domain, store, date, error class, UID) and its Message-ID is added to the seen list. Without a Message-ID, a `uid:<folder>:<uid>` key is used.
12. **A failing email no longer stops the run (`continue`, not `break`).** The run's `last_uid` stops just below the earliest email still being retried, and emails after it are imported and marked seen. The next run re-reads only the failing one; the others are skipped by Message-ID, which costs one header fetch. Unreadable emails count as handled, so the window moves past them.
13. **"Retry unreadable emails" clears the marks, removes those Message-IDs from the seen list and rewinds the folder's UID and date window to the oldest one**, then checks right away. It's available from Configure (menu, shown only when there are any), from Settings in the panel, and as `purchases/retry_unreadable` (admin).
14. **The extraction structure is now loose:** every field is optional plain text (prices and quantities too), items may be empty, and nothing is required. Prices like "$12.99" or "USD 1,234.50" and quantities like "2 pcs" are parsed locally, and a missing quantity is 1. A missing `is_order` counts as an order; an item without a name is skipped.
15. **If `generate_data` raises, it's retried once with a minimal structure** (is_order, store, order_ref, items[name, image_url]). Only if that fails too does the failure count.
16. **Failure logs include the exception message**, with email addresses replaced by `<address>` and cut to 300 characters. The email body is never logged. The Anthropic provider's own "Failed to parse JSON response" log, which includes the reply, is outside Laundry Hub.
17. **A shipping notice with no items for an unseen order isn't an error:** it's read, makes no card and is marked seen. Recording it against the order when the confirmation arrives isn't built; the confirmation creates the cards anyway.
18. **Heather's answers to the v0.2.4 open questions (no code change):** the 30-minute enrolment window stays fixed, not configurable. Members enrol one tag at a time, the latest they tapped, with no batch of several tags tapped in a row. Admins aren't notified when a member enrols a garment.

# Decisions (v0.2.3)

Choices made while building v0.2.3 without asking. Each line: decision, then reason.

1. **Revs come from fingerprints, not from instrumenting each change.** After any change (and before every "changed" signal, `data` and `changes`), each record is hashed. A changed hash gets the next rev, and a vanished record gets a tombstone. No mutation path can forget to bump a rev, including taps, purchase checks and zone edits made in HA's own UI.
2. **Revs live in the catalog store (`revs`, schema 3.3, a minor bump).** v0.2.2 can still load the file. If someone goes back and forth, the counter restarts, and the app's `since_rev` (now newer than the server's) triggers a `reset`.
3. **Zones get revs too, although they live in config subentries.** A zone edit reloads the entry; the tracker sees the new fingerprint at setup.
4. **`changes` resets when `since_rev` is older than the newest purged tombstone, or newer than the counter.** The first means the 30-day window has passed. The second means the store was restored or downgraded.
5. **"missing" is part of a garment's fingerprint.** Missing is derived from time, and an offline app needs to learn that a garment went missing. The hourly refresh bumps those garments' revs.
6. **Conflicts are a normal websocket error with extra fields (`kind`, `record_id`, `current`).** HA's `send_error` can't carry a record, so the result message is built by hand in HA's error format.
7. **`if_rev` maps to the record named in the message (`garment_id`, `card_id`, `zone_id`, `uid`); bulk commands take `if_revs`.** One rule for every command, with no per-command code.
8. **Policies are declared per command in one table (`access.Policy`), enforced by one wrapper.** A test fails if a command has no policy.
9. **Members may edit name and notes (`garment/update`), size and wear_for (`garment/wardrobe`), description fields, and photos.** That's the prompt's list. Brand, purchase details, owner, bins, tags, location and AI describe stay admin-only. AI describe calls a paid provider.
10. **A member's owner defaults to the person linked to their HA user.** The options mapping overrides it, and an empty value means no owner. Most households already link people to users, so household access works without typing.
11. **"App access" sits on the main settings form, and "Household" opens a people step.** This works whether Wardrobe is on or off, without changing the Wardrobe-off flow.
12. **Members never see unknown tags or unmatched purchase cards.** Neither has an owner, and both are household admin work. Members see cards matched to their own garments (`purchases.mine`).
13. **For members, `changes` reports other owners' changed garments as `deleted`.** If a garment moves to another owner, the member's app must drop it. The cost is that a member learns the ids (not the contents) of other garments that changed.
14. **Phone taps reuse the station state machine by acting as a station.** `put_away:<closet>` acts as that closet, `in_wash` as the laundry station in sort mode, and `drying` as the laundry station in dry mode. Recent taps and the tap event show `phone:<device>`. Phones don't count towards station health or send LED feedback.
15. **`available_actions` offers the garment's own bin and dry type, not every bin.** The state machine always uses the garment's own zones; moving to another bin is a reassignment (admin).
16. **The photo view now checks who's asking (fix).** v0.2.2 served a garment photo to any logged-in HA user who knew its filename. Now only admins, and members for their own garments, get it. The import-preview view is admin-only.
17. **The v0.2.2 test suite runs unchanged except one assertion.** `subscribe`'s "changed" event now carries `rev`, as the prompt requires, so the exact-equality check became "changed is true and rev is an integer". A new test also replays every command the panel calls, as an admin.

# Decisions (v0.2.2)

Choices made while building v0.2.2 without asking. Each line: decision, then reason.

1. **IMAP debug logging stays muted during mail sessions** (Heather's answer). Privacy over debug output.
2. **The Add flow keeps ranking cards by date only** (Heather's answer).
3. **A partial-shipment card needs `is_apparel` true or missing, even with "Clothing only" off.** That's the rule asked for; "Clothing only" still governs new orders.
4. **The "one card, slightly renamed item" merge only applies when the email lists exactly one item.** Otherwise a partial shipment's new item would wrongly merge into the order's only card.
5. **Cards now store the email's image link (`image_url`)** so Restore can re-fetch a deleted photo. Cards from v0.2.1 don't have it and come back without a photo. The link is never sent to the panel.
6. **Restore also lifts a "Not clothing" rule for that product**, since restoring says it is clothing after all.
7. **Auto-archive counts from the last status change, not from creation.** Otherwise a restored old card would be archived again at the next check.
8. **Uniform preset sets `wear_for` to work too**, matching v0.2's rule that a uniform is a work garment. Unticking in the details form undoes both.
9. **The uniform offer is once per store: accepting or "No thanks" both record the store.** It counts matched garments not marked uniform, which covers matches made before v0.2.2 and presets the user unticked.
10. **No store schema change.** New card keys (`image_url`) and meta keys (`uniform_offers_done`) fit the existing 3.2 dicts.
11. **Narvar and Shopify can't be added as custom sender domains** (`narvar.com`, `narvarnotify.com`, `shopifyemail.com` are refused, and ignored if already stored). Either would opt in to thousands of brands.
12. **Shopify shop names match as whole words, case-insensitively, in the From display name or the Subject** (as asked). The body is never used for matching.
13. **Shopify store counts in Step B read headers (never bodies).** Gmail's name search also matches body text, so UID counts alone would overcount other shops.
14. **User-added Shopify names get the key `shopify:<name>`** and aren't uniform suppliers. Only the built-in First Tactical entry is.
15. **Life is Best Outside is left out.** This session's network blocks the site and search finds no shop under that name, so apparel couldn't be confirmed. It can be added as a custom domain.
16. **Senders Heather listed as subdomains (e5.llbean.com, mail.duluthtrading.com, orders.backcountry.com, nat.carhartt.com, info.meundies.com, web.blauer.com) are covered by subdomain matching** of the brand domain; tests check each one.
17. **The CI job `ha-internals` checks the private media-source attribute against HA 2026.11.3 (blocking) and the dev branch (informational).** Both have it today.
18. **Heather's answers to the v0.2.2 open questions (no code change):** Life is Best Outside is not added, and the catalog stays exactly as built (Blauer and First Tactical remain the only uniform suppliers; First Tactical remains the built-in Shopify entry). Partial shipments keep the current rule: non-apparel never creates a card. Restore doesn't unmatch cards. The uniform offer stays limited to Blauer and First Tactical.

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
