# Laundry Hub app API (api_version 1)

The contract a second client, such as the `laundry-hub-ios` app, builds against. Everything goes
through Home Assistant: the app logs in to HA (OAuth, as the HA companion app does), opens
HA's websocket (`/api/websocket`) and sends Laundry Hub commands on it. HA stays the only
source of truth; the sidebar panel uses the same commands.

- Available from integration **0.2.3**.
- `api_version` changes only when a field or command is **removed or renamed**. Adding
  fields, commands or capabilities never bumps it, so clients must ignore unknown fields.
- Errors use HA's websocket error format: `{"success": false, "error": {"code", "message"}}`.
  Codes the app should handle: `unauthorized`, `conflict` (with extra fields, see below),
  `not_loaded`, `unknown_garment`, `invalid_format`, and the validation codes listed per command.

## Roles and access

| Who | When | Can |
|---|---|---|
| **admin** (HA administrator) | always | everything (what the panel does) |
| **member** (any other HA user) | only when Configure → **App access** is *Household* | read their own owner's garments (and all zones), phone-tap, and edit their own garments' name, notes, photos, size, description fields and wear_for |

With App access set to *Admins only* (the default), a member gets `unauthorized` for every
command, and 401/404 from the photo endpoints.

A member's **owner** is a `person` entity: the one chosen for their HA user under App access →
people. Otherwise it's the person linked to their HA user in Settings → People. A member without
an owner sees no garments.

Admin only, even with *Household*: deleting garments, creating garments, zones, bulk reassign
and set-field, location changes outside phone taps, unknown-tag dismissal, URL import, AI
describe, purchase emails and purchase cards that aren't matched to the member's garments,
and other owners' garments.

## Commands

All types are prefixed with `laundry_hub/`. "Policy" is the access rule from the table above:

- **read**: anyone allowed in; results are filtered to the caller's owner.
- **own**: admins, or a member acting on their own garment.
- **tap**: admins, or members (actions on own garments only).
- **admin**: admins only.

### `api_info` (read)

What the server supports and who you are.

```json
{"api_version": 1, "integration_version": "0.2.3", "wardrobe_enabled": true,
 "capabilities": ["phone_tap", "changes", "if_rev", "url_import", "ai_describe", "purchases"],
 "role": "member", "owner": "person.alex", "app_access": "household", "rev": 412}
```

`capabilities` lists only what this server has *now*:

- `purchases` needs a connected mailbox.
- `ai_describe` needs an AI Task entity.
- `url_import` and `ai_describe` need Wardrobe on.
- v0.3 will add `out_state` and `wear_log`. Show a feature only when its capability is listed.

### `data` (read)

The full snapshot, the same one the panel reads. Adds `rev` (top level), `role`, and per
record `rev` and `updated_at`.

For a member:

- `garments` holds only their owner's garments.
- `unknown_tags` is `[]` and `last_unknown` is `null`.
- `owners` holds only theirs.
- `purchases` is `{connected, cards: [], archived: [], uniform_offers: [], mine: [...]}`, where
  `mine` holds the cards matched to their garments.

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
  so the app drops them. Unknown tags never appear for members.
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
    `{"known": false, "uid": "99AABBCC", "rev": n}`.
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

### Existing commands

Unchanged from v0.2.2; they gain only `if_rev` / `if_revs` and the policies.

- **own**:
  - `garment/update` (members: `name`, `notes` only)
  - `garment/wardrobe` (members: `size`, `wear_for` only)
  - `garment/describe_fields`
  - `garment/reset_field`
  - `photo/upload`, `photo/delete`, `photo/remove`, `photo/reorder`
- **admin**: everything else:
  - `garment/create`, `garment/delete`, `garment/set_location`, `garment/describe`,
    `garment/set_field`
  - `reassign`, `zone/remove`, `zone/flash`, `unknown/dismiss`
  - `import_product_url`, `import/apply`, `owner/update`
  - `purchases/*`

## HTTP

- `POST /api/laundry_hub/photos` (multipart: `garment_id`, `file`) uploads one photo of up to
  10 MB. JPEG, PNG and WebP are accepted; HEIC must be converted on the device. Members may
  upload to their own garments only (otherwise 403).
- `GET /api/laundry_hub/photos/<filename>` serves a photo or thumbnail.
  - Use the signed URLs from `data`, `changes` or `photo/url`, or send a bearer token.
  - Members only get their own garments' photos, and photos of cards matched to them;
    anything else is 404.
- Signed URLs expire after 24 h. Ask `photo/url` again when one has expired.

## Suggested sync loop

1. Call `api_info`; show features by `capabilities` and `role`.
2. Read `data`, and store `rev` and the records.
3. Call `subscribe`. On `changed`, call `changes {since_rev}` and apply records and `deleted`.
   On `reset`, go back to step 2.
4. Offline edits: queue them with the `rev` the record had, and send them with `if_rev`. On a
   `conflict`, show `current` and let the user choose.
