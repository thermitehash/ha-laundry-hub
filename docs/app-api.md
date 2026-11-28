# Laundry Hub app API (api_version 1)

The contract a second client, such as the `laundry-hub-ios` app, builds against. Everything goes
through Home Assistant: the app logs in to HA (OAuth, as the HA companion app does), opens
HA's websocket (`/api/websocket`) and sends Laundry Hub commands on it. HA stays the only
source of truth; the sidebar panel uses the same commands.

- Available from integration **0.2.3**.
- `api_version` changes only when a field or command is **removed or renamed**. Adding
  fields, commands or capabilities never bumps it, so clients must ignore unknown fields.
  See **Compatibility** below.
- Errors use HA's websocket error format: `{"success": false, "error": {"code", "message"}}`.
  Codes the app should handle: `unauthorized`, `conflict` (with extra fields, see below),
  `not_loaded`, `unknown_garment`, `invalid_format`, `no_owner_mapping`, `owner_not_allowed`,
  `tag_not_yours`, `tag_not_enrolled` and `invalid_action`, plus the validation codes listed
  per command.

## Compatibility (0.2.6)

Policy:

- **`api_version`** is the server's preferred API version. It is bumped **only** when a
  field or command is removed or renamed. Additions (fields, commands, capabilities, error
  codes) **never** bump it.
- **`api_versions_supported`** lists every API version this server can still serve
  (currently `[1]`). When version 2 arrives, the server keeps serving 1 for a while, so
  older apps keep working.
- **`min_client`** is the lowest app build the server accepts (starts at `1`). It is raised
  **only** when a server change would break older builds; adding things never raises it.

Clients may say hello once after connecting (optional; the panel and older apps never do):

```json
{"type": "laundry_hub/hello", "client_api_versions": [1], "client_build": 42}
→ {"compatible": true, "reason": null, "api_version": 1,
   "min_client": 1, "api_versions_supported": [1]}
```

- `reason` is `client_too_old` (the build is below `min_client`, or the app only speaks
  versions the server no longer serves), `server_too_old` (the app only speaks newer
  versions), or `null`.
- `api_version` is the newest version both sides support. When incompatible, it's the
  server's preferred one.
- Both fields are optional; without `client_api_versions` the server assumes `[1]`.
- On `client_too_old`, ask the user to update the app. On `server_too_old`, ask them to update
  the Laundry Hub integration. `api_info` carries the same `min_client` and
  `api_versions_supported`, so a client can also decide on its own.

## Roles and access

| Who | When | Can |
|---|---|---|
| **admin** (HA administrator) | always | everything (what the panel does) |
| **member** (any other HA user) | only when Configure → **App access** is *Household* | read their own owner's garments (and all zones), phone-tap, **enrol a garment for themselves from a tag they just tapped** (0.2.4), edit their own garments' name, notes, photos, size and wear_for, and **use the purchase cards assigned to them** (0.2.6) |

With App access set to *Admins only* (the default), a member gets `unauthorized` for every
command, and 401/404 from the photo endpoints.

A member's **owner** is a `person` entity: the one chosen for their HA user under App access →
people. Otherwise it's the person linked to their HA user in Settings → People. A member without
an owner sees no garments.

Admin only, even with *Household*:

- deleting garments, zones, bulk reassign and set-field, location changes outside phone taps;
- unknown-tag dismissal and the unknown-tag list;
- URL import;
- manual AI describe and re-describe, `garment/describe_fields` and `garment/reset_field`
  (admin-only from 0.2.4);
- brand, purchase details and uniform;
- purchase emails and store settings; assigning cards, quantity, split and merge;
- purchase cards that aren't assigned to the member (or matched to their garments);
- other owners' garments.

**AI descriptions for members (0.2.4):** when a member adds the first photo to their own
garment, the automatic description runs, exactly as when an admin adds it in the panel. It runs
server-side, with the AI Task entity chosen in Laundry Hub's settings, not as the member.

**Member enrolment (0.2.4, capability `member_enroll`):**

1. The member taps the new tag with their phone (`tap`, no action). The answer has
   `"known": false, "can_enroll": true, "owner": "<their person>"`.
2. The member calls `garment/create` with `uid` (that tag), `name`, `wash_bin`, `dry_type` and
   optionally `notes`. `owner` may be left out or set to their own owner; it is forced to it.
3. Photos, size and wear_for follow with the usual commands (`photo/upload`, `garment/wardrobe`).

Rules:

- A member may enrol only the unknown tag they tapped last, with their own phone, within
  30 minutes. A station tap of that tag hands it back to the admins.
- A member sees only that one unknown tag in `data` and `changes`, never the household list.
- Enrolling removes the tag from the unknown list, as for admins.
- `location`, `brand`, purchase, uniform and lifecycle are admin-only (`unauthorized`).
- Errors:
  - `no_owner_mapping`: the member's HA user isn't linked to a person.
  - `owner_not_allowed`: another owner was given.
  - `tag_not_yours`: not the member's recent phone tap.

**Purchase cards for members (0.2.6, capabilities `purchase_assign`, `purchase_groups`):**

- An admin assigns a card to an owner (`purchases/assign`). A member sees only cards
  assigned to their owner (plus cards already matched to their garments), in `data`,
  `changes` and the photo endpoints. This replaces the 0.2.4 rule that members never see
  cards.
- A member may `purchases/match` an assigned card to one of their own garments (to make a
  garment from it: enrol the tag with `garment/create`, then match), and `purchases/dismiss`
  it (with `not_clothing` too). Other cards and other garments get `unauthorized`.
- A card that stops being assigned to the member comes back in `changes` under `deleted`.

## Garments without a tag (0.2.8, capability `untagged_garments`)

A garment no longer needs an NFC tag. Every garment has always had its own `id` (a ULID),
independent of its tags; `uids` may now be empty. Nothing about existing garments changes
(ids, tags, photos, history), and every command still accepts a tag UID wherever it takes a
`garment_id`; answers carry the `id` as well.

New garment fields (additive):

- `tagged`: `false` when `uids` is empty.
- `state_source`: `"tap"` (a station or phone tap) or `"manual"`; `state_updated`: when.
- `state_confidence`: how far to trust the laundry state, 0..1. Tagged garments: 1.
  Untagged: 1 for 2 days after a manual update, fading to 0.25 at 30 days; 0.5 if never
  set. Use it to weigh an untagged garment's state.

An untagged garment's `location` is `null` (no station has seen it): `state: "put_away"`
with `location: null` means "clean, somewhere". It's never `missing`, and it's left out of
the closet, in-wash and drying counts and the bin sensors.

Commands:

- `garment/create` without `uid` (or `uid: null`) creates an untagged garment. `location`
  must then be empty (`untagged_location`). Members may do this for themselves (owner
  forced).
- `garment/manual_state {garment_id, action}` (own): `to_laundry` → in the wash,
  `put_away` → clean and put away. Untagged garments only (`garment_has_tag`).
- `garment/attach_tag {garment_id, uid?, replace?, move?}` (own):
  - With `uid`: attach that tag now. A tag on another garment → `tag_in_use` (the message
    names it); send `move: true` to move it here (the other garment may become untagged).
  - Without `uid`: the next **unknown** tap, at any station or from the app, within
    **2 minutes** links to this garment → `{linked: false, waiting_until}`. `data.link` is
    `{garment_id, replace, expires}` while waiting (members see only their own). A phone
    `tap` that completes it answers `{known: true, linked: true, garment, …}`.
  - `replace: true` removes the garment's current tags first (a lost tag).
- `garment/attach_cancel {garment_id}` (own): stop waiting.
- `garment/remove_tag {garment_id, uid?}` (own): remove one tag, or all (back to untagged;
  history is kept).
- `unknown/link_candidates {uid?}` (read): untagged garments to link an unknown tag to, the
  tapper's own garments first, then the newest. Members get only their own.

Members may attach a tag they just tapped with their phone (or one already on another of
their own garments) to their own garments; anyone else's tag or garment → `unauthorized`.

## Commands

All types are prefixed with `laundry_hub/`. "Policy" is the access rule from the table above:

- **read**: anyone allowed in; results are filtered to the caller's owner.
- **purchase**: admins, or a member acting on a card assigned to their owner (0.2.6).
- **own**: admins, or a member acting on their own garment.
- **tap**: admins, or members (actions on own garments only).
- **admin**: admins only.

### `api_info` (read)

What the server supports and who you are.

```json
{"api_version": 1, "api_versions_supported": [1], "min_client": 1,
 "integration_version": "0.2.8", "wardrobe_enabled": true,
 "capabilities": ["phone_tap", "changes", "if_rev", "untagged_garments", "member_enroll", "url_import", "ai_describe", "purchases", "purchase_groups", "purchase_assign"],
 "role": "member", "owner": "person.alex", "app_access": "household", "rev": 412}
```

`capabilities` lists only what this server has *now*:

- `member_enroll` is listed when App access is *Household* (members may enrol their own tags).
- `purchases` needs a connected mailbox; `purchase_groups` and `purchase_assign` (0.2.6) are
  listed with it.
- `ai_describe` needs an AI Task entity.
- `url_import` and `ai_describe` need Wardrobe on.
- v0.3 will add `out_state` and `wear_log`. Show a feature only when its capability is listed.

### `hello {client_api_versions?, client_build?}` (read, 0.2.6)

Optional compatibility handshake; see **Compatibility**.

### `data` (read)

The full snapshot, the same one the panel reads. Adds `rev` (top level), `role`, and per
record `rev` and `updated_at`.

For a member:

- `garments` holds only their owner's garments.
- `unknown_tags` holds at most the one tag they just tapped with their phone (to enrol it), and
  `last_unknown` is `null`.
- `owners` holds only theirs.
- `purchases` is `{connected, cards, archived, uniform_offers: [], mine}`: `cards` are the
  waiting cards assigned to them, `archived` their dismissed or archived ones, and `mine`
  the matched ones (assigned to them or matched to their garments). No status fields.

Photo URLs in `photo_urls` are pre-signed for 24 h.

### `subscribe` (read)

A websocket subscription. Each event carries the store `rev`:

- `{"changed": true, "rev": 413}`: something changed. Call `changes` with your last rev.
- `{"tap": {...}, "rev": 413}`: a tap happened. The tap object has `event_type` (`in_wash`,
  `drying`, `put_away` or `unknown`), `garment_id`, `name`, `owner`, `uid`, `station`, `mode`,
  `zone`, `repeat` and `time`.

`station` is `laundry`, `closet_1` or `closet_2` for a station, or `phone:<device_name>` for a
phone. Members get only taps of their own owner's garments, and never unknown-tag taps.

### `changes {since_rev}` (read)

Offline sync. Returns every record with `rev > since_rev`, plus deletions:

```json
{"reset": false, "rev": 420,
 "garments": [...], "zones": [...], "unknown_tags": [...], "purchases": [...],
 "deleted": [{"id": "01J…", "kind": "garment", "rev": 418, "deleted": true}]}
```

- Each record has the same shape as in `data`, including `rev` and `updated_at`.
- `kind` is one of `garment`, `zone`, `unknown_tag` or `purchase`.
- Tombstones are kept for **30 days**. If `since_rev` is older than that, or newer than the
  server's `rev` (for example after an HA restore), the answer is `{"reset": true, "rev": n}`.
  Re-read `data` then.
- For a member, garments and cards that aren't (or are no longer) theirs come back in `deleted`,
  so the app drops them. The only unknown tag a member gets is the one they just tapped.
- Revs come from one store-wide counter. Store `rev` from each answer and use it as the next
  `since_rev`.

### Optimistic writes: `if_rev` / `if_revs`

Every mutating command accepts an optional `if_rev`, the rev of the record you based the change
on:

- `garment_id` → that garment
- `card_id` → that purchase card
- `zone_id` → that zone
- `uid` → that unknown tag (for `tap`, the garment with that tag)

Bulk commands (`reassign`, `garment/set_field`) take `if_revs: {garment_id: rev}`.

If any record moved on, nothing is written and the answer is:

```json
{"success": false, "error": {"code": "conflict", "message": "…", "kind": "garment",
 "record_id": "01J…", "current": {…the record as it is now…}}}
```

Without `if_rev`, writes behave exactly as before. The panel never sends it.

### `tap {uid, source: "phone", device_name?, action?}` (tap)

A phone acting as a tap station. `uid` is the tag's hex UID; separators are ignored.

- **Without `action`:**
  - An unknown tag is recorded like a station tap. It shows up as an unknown tag, the panel's
    Add page fills in, and the enrolment notification is sent. Answer:
    `{"known": false, "uid": "99AABBCC", "can_enroll": true, "owner": "person.alex", "rev": n}`.
    For an admin, `owner` is `null`. For a member without an owner, `can_enroll` is `false`.
  - A known tag answers
    `{"known": true, "uid", "garment": {…}, "available_actions": [...], "rev"}`, where
    `available_actions` is:
    - `put_away:closet_1`
    - `put_away:closet_2`
    - `in_wash:<the garment's wash bin id>`
    - `drying:<the garment's dry type id>`

    v0.3 will add `wearing` and `out`.
- **With `action`** (one of `available_actions`): applied through the same state machine as a
  station tap.
  - `put_away` acts as that closet station.
  - `in_wash` acts as the laundry station in *sort* mode, `drying` as the laundry station in
    *dry* mode.
  - The zone light flashes, the `event.laundry_hub_tap` entity fires (automations see phone
    taps, `station: "phone:<device_name>"`), and the tap goes into the garment's
    `recent_taps` with that station.
  - Phone taps never count towards station health and never send LED feedback to a station.
  - Answer: `{"known": true, "applied", "event_type", "repeat", "garment", "available_actions", "rev"}`.
- **Errors:**
  - `tag_not_enrolled`: an action on an unknown tag.
  - `invalid_action`: the action isn't one of `available_actions`.
  - `unauthorized`: a member tapping someone else's garment.

### `photo/url {garment_id, filename, size: "thumb"|"full"}` (read)

Signs one photo URL on demand: `{"url": "/api/laundry_hub/photos/…?authSig=…", "expires_in": 86400}`.
The signature is the auth, so the URL works in an image view without headers.

Errors: `unknown_photo`, and `unauthorized` for someone else's garment.

### Purchase import status (admins)

`data.purchases` includes `unreadable`: emails that couldn't be read after 3 attempts, as
`[{store, date, sender_domain, error}]`, newest first. They are skipped so the rest of the import
continues. `purchases/retry_unreadable` (admin) clears the marks, reads them again and answers
with the check summary plus `retried`.

Purchase cards gain `cut` (0.2.5): the fit or cut printed in the order email ("slim", "tall"),
or `null`. Like every added field, clients that don't know it should ignore it.

### Purchase cards are groups (0.2.6)

One card per item. Emails about one order (confirmation, shipping, delivery) are the same
units and never add up; a new order of the same item joins the card. Added fields:

- `quantity`: total units; `quantity_locked`: `true` once set by a person (later emails add
  history, not units, until unlocked).
- `history`: one line per purchase, oldest first: `{id, order_ref, order_date, store,
  unit_price, currency, quantity}`.
- `matches`: `[{garment_id, units}]`; `remaining`: units neither matched nor dismissed;
  `dismissed_units`. A card stays `new` until `remaining` is 0. `garment_id` is the first
  matched garment (unchanged meaning for one-unit cards).
- `owner`: the assigned person, or `null`; `suggested_owner`: a suggestion for unassigned
  cards (admins act on it; it's never applied by itself).
- `brand` is read from the email again (0.2.6); `fit` (the cut mapped to a fit option, or `null`) and `size_cut` (`tall`,
  `petite`, `plus`, `maternity`, or `null`).

Commands (admin unless noted):

- `purchases/match {card_id, garment_id, units?}` (purchase): `units` 1..N, default all
  remaining units.
- `purchases/dismiss {card_id, not_clothing?}` (purchase): dismisses the remaining units.
- `purchases/set_quantity {card_id, quantity}`: `quantity` 1..999 locks it; `null` unlocks
  and recounts from the history. Error `quantity_too_low` below the matched units.
- `purchases/split {card_id, purchase_id}` → `{card, new_card}`. Error `cannot_split`.
- `purchases/merge {card_id, other_id}` → the kept card (the fuller one). Error
  `cannot_merge` for matched cards.
- `purchases/assign {card_id | card_ids, owner}`: `owner` a person entity or `null`.
  → `{cards}`. Error `invalid_owner`.
- `purchases/cleanup_seen`: hides the one-time cleanup line. `data.purchases.cleanup` is
  `{not_clothing, health, grouped}` until then (0.2.7), `{skipped: true}` when the AI was
  unavailable and the retries ran out, otherwise `null`.
- `purchases/reclassify` (0.2.7): runs the cleanup of old cards again now →
  `{classified, cleanup}`.

All take `if_rev` (of `card_id`).

### Existing commands

Unchanged from v0.2.2; they gain only `if_rev` / `if_revs` and the policies.

- **enrol** (0.2.4): `garment/create` (admins: any; members: their own recent tap, their own owner)
- **purchase** (0.2.6): `purchases/match`, `purchases/dismiss`
- **own**:
  - `garment/update` (members: `name`, `notes` only)
  - `garment/wardrobe` (members: `size`, `wear_for` only)
  - `photo/upload`, `photo/delete`, `photo/remove`, `photo/reorder`
- **admin**: everything else:
  - `garment/delete`, `garment/set_location`, `garment/describe`, `garment/set_field`
  - `garment/describe_fields`, `garment/reset_field` (admin-only from 0.2.4)
  - `reassign`, `zone/remove`, `zone/flash`, `unknown/dismiss`
  - `import_product_url`, `import/apply`, `owner/update`
  - `purchases/*` (including `purchases/retry_unreadable`, 0.2.4, and `set_quantity`,
    `split`, `merge`, `assign`, `cleanup_seen`, 0.2.6, and `reclassify`, 0.2.7), except
    match and dismiss

## HTTP

- `POST /api/laundry_hub/photos` (multipart: `garment_id`, `file`) uploads one photo of up to
  10 MB. JPEG, PNG and WebP are accepted; HEIC must be converted on the device. Members may
  upload to their own garments only (otherwise 403).
- `GET /api/laundry_hub/photos/<filename>` serves a photo or thumbnail.
  - Use the signed URLs from `data`, `changes` or `photo/url`, or send a bearer token.
  - Members only get their own garments' photos, and photos of cards assigned or matched
    to them;
    anything else is 404.
- Signed URLs expire after 24 h. Ask `photo/url` again when one has expired.

## Suggested sync loop

1. Call `api_info`; show features by `capabilities` and `role`.
2. Read `data`, and store `rev` and the records.
3. Call `subscribe`. On `changed`, call `changes {since_rev}` and apply records and `deleted`.
   On `reset`, go back to step 2.
4. Offline edits: queue them with the `rev` the record had, and send them with `if_rev`. On a
   `conflict`, show `current` and let the user choose.
