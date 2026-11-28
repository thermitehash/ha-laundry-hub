# Laundry Hub build report: v0.2.2
- Version: 0.2.2 / Date: 2026-11-28 / Repo: https://github.com/thermitehash/ha-laundry-hub
- PR: https://github.com/thermitehash/ha-laundry-hub/pull/5 (not merged). v0.2.1 was merged in PR #4 (`0aa9994`) and released as `v0.2.1`.
- Commit: `81bcf9e` (code); this report is committed on top as a docs-only commit
- CI: workflow **Validate**, run 46089072250 on `81bcf9e`: all 16 ✅ (new: `ha-internals`)
  - Version consistency ✅ · Version bump ✅ (0.2.2 > 0.2.1) · lint ✅ · tests ubuntu/macOS ✅ · mutation ✅ · panel ✅
  - firmware-host-tests ×2 ✅ · esphome compile ×4 ✅ · hassfest ✅ · hacs ✅
  - ha-internals ✅: HA 2026.11.3 ✅ and dev branch ✅. The private attribute behind the hidden media source exists in both.

## Summary
Purchase inbox polish: partial shipments, an Archived view with Restore, Heather's stores (Narvar subdomains only), Shopify stores by name, uniform suppliers (Blauer, First Tactical) with a preset and a bulk offer, and a Settings status row.

## What shipped
- **Partial shipments:** a shipping email for a known order adds cards for clothing items (or items with no apparel flag) not yet on a card. Existing cards are still only updated.
- **Archived + Restore:** dismissed and archived cards are shown newest first. Restore re-fetches the photo once through the pinned pipeline from the stored `image_url` and lifts a Not-clothing rule. Auto-archive counts from the last status change.
- **Catalog:** Heather's 21 stores, with Narvar per-brand subdomains. `narvar.com`, `narvarnotify.com` and `shopifyemail.com` are refused as custom domains and ignored if stored. The supplier list from the original prompt was dropped (follow-up). Life is Best Outside is left out because apparel couldn't be confirmed.
- **Shopify by name:** First Tactical is built in, and user-added names are supported. A name matches as whole words in the From display name or the Subject.
  - Gmail is searched with `from:shopifyemail.com ("Name" …)`; generic IMAP with `FROM shopifyemail.com`, then a local header check.
  - Step B counts read headers only, never bodies.
- **Uniform suppliers (Blauer, First Tactical):** matching a card presets uniform and wear_for=work. A once-per-store "Mark all matched garments from <Store> as uniform" offer has accept and decline.
- **Settings:** a read-only Purchase emails row.
- **CI:** `ha-internals` job and `script/check_ha_internals.py`.

## Tests
- 300 pytest (coverage 96 %), green on ubuntu and macOS. New suite `tests/test_purchases_v022.py` (15) and new email fixtures.
- Panel smoke test extended: Archived view and Restore, uniform offer, Settings row.

## Needs a person
- A real mailbox: Lands' End and Columbia Narvar emails, a First Tactical Shopify email, and a Blauer order.
- Restore on a real dismissed card (photo re-fetch).

---

# Laundry Hub build report: v0.2.1
- Version: 0.2.1 / Date: 2026-11-27 / Repo: https://github.com/thermitehash/ha-laundry-hub
- PR: https://github.com/thermitehash/ha-laundry-hub/pull/4 (not merged)
- Commit: `276ec93` (code); this report is committed on top as a docs-only commit
- CI: workflow **Validate**, run 46083905681 on `276ec93`: all 15 ✅
  - Version consistency ✅ · Version bump ✅ (0.2.1 > 0.2.0) · lint (ruff, mypy --strict, yamllint) ✅
  - tests (ubuntu) ✅ · tests (macOS) ✅ · mutation ✅ · panel (Playwright, screenshots artifact) ✅
  - firmware-host-tests ubuntu/macOS ✅ · esphome compile st25r_i2c/rc522 × ubuntu/macOS ✅ · hassfest ✅ · hacs ✅
  - The first run (on `a889e26`) failed because CI didn't install `aioimaplib`; fixed in `276ec93` (`requirements_test.txt`).
- Release: `v0.2.1` is tagged automatically by the Release workflow once Validate passes on `main` after merge.

## Summary
- **Purchase inbox:** order emails from opted-in stores become purchase cards that can be matched to garments (Add flow, Closet → Purchases). Read-only IMAP owned by Laundry Hub, AI Task extraction, links verified against the email text.
- **Carry-overs:** HEIC → JPEG in the browser, DNS pinning for every outbound fetch, Laundry Hub hidden from the media browser.

## What shipped
- **0. Carry-overs:** `lib.prepareUpload` (HEIC detection by type or name, `createImageBitmap` + canvas, friendly message); `importer.PinnedResolver` + one-off pinned session per hop; `media.py` registers a hidden source (no `media_source.py` platform); answers to v0.2.0 questions applied (auto-describe after import has its own test).
- **1. Settings:** options menu with Wardrobe on; Step A account (Gmail or generic IMAP, login + EXAMINE tested, friendly `invalid_auth` / `cannot_connect` / `folder_not_found`, no partial save); Step B 37 stores with 90-day counts (UID searches only), matches first, none preselected, custom domains validated; Step C interval (≥15), backfill (0/30/90/365), clothing only, auto-archive. Status line, Edit stores, Change password, Check now, Disconnect (keep/delete cards). Repair issue + fix flow on login failure.
- **2. Fetching:** `X-GM-RAW` for Gmail, `SINCE` + `FROM` per domain for generic IMAP; UIDVALIDITY + last UID per folder; headers first; body only for exact/subdomain sender matches; text/plain + text/html parts only via `BODY.PEEK[part]`, 500 KB cap; de-dup by Message-ID and order ref + store; shipping emails merge; one run at a time, backoff; `laundry_hub.check_purchase_emails`.
- **3. Extraction:** stdlib HTML → text with `[image: …]` / `[link: …]` markers, 12k chars; selector-built `structure` with `items[]`; URLs kept only if verbatim in the sent text; non-apparel dropped when clothing-only; photos through the v0.2 pipeline; only card fields stored.
- **4. Data + UI:** `purchases` + `purchase_meta` in the store (3.1 → 3.2, lossless); Add "Is this from a recent order?" row (copies photos, prefills, matches on save); Closet Purchases chip/inbox with match (garment search), dismiss, not clothing; detail origin line; `sensor.laundry_hub_unmatched_purchases`.
- **5. Privacy:** EXAMINE only, never SELECT/STORE/COPY/MOVE/EXPUNGE; aioimaplib DEBUG/INFO muted during sessions; `Account.__repr__` hides address and password; diagnostics show store keys, counts, last error class only.

## Tests
- pytest: 285 passed locally (Python 3.14, HA 2026.10.4), coverage 96 % (gate 90 %); CI green on ubuntu and macOS.
- New: `tests/test_purchases.py` (17), `tests/test_purchase_flow.py` (15), `tests/test_pinning.py` (4), plus media-browser, hidden-source and auto-describe-after-import tests; panel smoke test extended (order row, inbox, match sheet, origin line, HEIC).
- Fake aioimaplib server (`tests/fake_imap.py`) with recorded `.eml` fixtures: Amazon multi-item with a PDF attachment, Old Navy + shipping update, Target with a lamp, a look-alike non-opted sender.
- Mutation gate (state machine) unchanged and passing.

## Needs a person or hardware
- A real Gmail account with an app password: connect, check store counts, run Check now, confirm cards look right.
- A real AI Task provider: extraction quality on real order emails.
- iPhone Safari: HEIC conversion on a real photo.
- Everything from earlier reports that needs hardware still does.

---

# Laundry Hub build report: v0.2.0
- Version: 0.2.0 / Date: 2026-11-27 / Repo: https://github.com/thermitehash/ha-laundry-hub
- PR: https://github.com/thermitehash/ha-laundry-hub/pull/3 (not merged)
- Commit: `f2d691b` (v0.2.0 code), CI verified on `9277f46` (code + report draft); this report is committed on top as a docs-only commit
- CI: workflow **Validate**, run 46078028297 on `9277f46`: all 15 ✅
  - Version consistency ✅ · Version bump ✅ (0.2.0 > 0.1.2) · lint (ruff, mypy --strict, yamllint) ✅
  - tests (ubuntu) ✅ · tests (macOS) ✅ · mutation ✅ · panel (Playwright, screenshots artifact) ✅
  - firmware-host-tests ubuntu/macOS ✅ · esphome compile st25r_i2c/rc522 × ubuntu/macOS ✅ · hassfest ✅ · hacs ✅
- Release: `v0.2.0` is tagged automatically by the Release workflow (wired to `workflow_run: [Validate]`) once Validate passes on `main` after merge.

## Summary
- **Wardrobe catalog (optional, off by default):** purchase details, structured sizes, up to 8 photos per garment, product URL import, and AI descriptions from a fixed taxonomy. This lays the groundwork for wear tracking (v0.3) and outfit suggestions (v0.4).
- **Phone-first panel redesign:** Home, Closet, Add and Settings tabs, plus a garment detail sheet. Live updates, light/dark from HA, and the Linen & Teal palette from shared `tokens.css`.
- With Wardrobe off, v0.1.2 behaviour is unchanged.

## What shipped
- **1.1 Feature switch:** options `wardrobe_enabled` (off), `ai_task_entity` (`ai_task` entity selector, validated) and `follow_ha_theme` (off). When off, wardrobe commands refuse with `wardrobe_disabled`, and no AI calls or network fetches happen.
- **1.2 Model / Store v3:**
  - New fields: `photos[]`, `brand`, `size` (label, system, cut, note), nested `purchase` (store, date, price, currency, link, order ref, source), `description` plus per-field `description_provenance`, `description_confidence`, `description_ai`, `description_model`, `description_updated`.
  - Wear context: `wear_for`, `uniform` (uniform forces `wear_for = work`).
  - `recent_taps` (last 10), `lifecycle` plus `lifecycle_changed` and `sale_price`.
  - Reserved for v0.3: `wear_log`, `out_since`, `fit_feedback`, `fit_score`, `skip_count`, `last_suggested`, and owner `body_profile` / `schedule`.
  - Per-owner `work_label`.
  - The migration is lossless (unknown keys kept) and writes `.storage/laundry_hub.v2.backup` first.
- **1.3 Photos:** Pillow pipeline applies EXIF orientation, strips metadata, converts to RGB and saves a 1600 px JPEG (q85) plus a 400 px thumbnail.
  - Up to 8 per garment, 10 MB per upload.
  - Multipart upload view, admin only. Authenticated serving with pre-signed URLs.
  - Files are deleted with the photo or the garment. Legacy photos get thumbnails in the background.
  - The panel uses `capture="environment"`, with multiple selection.
- **1.4 URL import:** `laundry_hub/import_product_url` websocket command and `laundry_hub.import_product_url` service.
  - Fetch limits: 10 s timeout, 3 MB, browser User-Agent, manual redirects (at most 5), each hop re-checked.
  - Extraction order: JSON-LD `Product`, then OG / product meta, then Twitter cards.
  - Up to 4 images through the photo pipeline. Preview first; nothing is written until apply.
  - SSRF guard after DNS resolution: private, loopback, link-local, multicast, reserved, non-global, IPv4-mapped, localhost and `.local` are all refused.
  - Failures come back as friendly messages.
- **1.5 AI description:** `laundry_hub/garment/describe` and `laundry_hub.describe_garment`.
  - Calls `ai_task` with a selector-built `structure`, the cover plus at most 2 more photos (1600 px) via a `media_source` platform, and the name, brand and notes.
  - The result is validated against `taxonomy.py`; confidence below 0.5 → null.
  - User fields are locked and survive re-describe; "Reset to AI" restores the AI value.
  - Import/purchase data beats the AI.
  - Taxonomy remap runs on load.
  - Auto-describe runs after the first photo; there is a manual Re-describe; it never runs on a tap.
  - Failures keep the old description and log a warning without image payloads.
  - Privacy note in the options flow, the panel and the README.
- **1.6 Purchase inbox:** nested `purchase` dict with `source: manual | url | email`; design stub in `docs/design/purchase-inbox.md`.
- **2 Panel:**
  - Vanilla web components split into modules (`laundry-panel.js`, `lib.js`, `home.js`, `closet.js`, `add.js`, `settings.js`, `detail.js`, `panel.css`, `tokens.css`), all loaded with `?v=0.2.0`.
  - Bottom tabs on phones, a segmented control at ≥900 px with a two-column layout.
  - Home: closet and wash-bin tiles with counts and up to 3 covers, a drying tile with an "oldest" hint, a missing card, a single-card empty state, and an Out tile only if `out_since` exists.
  - Closet: search (including taxonomy and summary), chips for owner / location / bin / Needs photo / Needs review / Filters / Archived, a filter sheet over every taxonomy field with counts (zero options hidden, type → subtype cascade, colour swatches, formality and warmth ranges), photo grid, Select mode with a sticky action bar (Move to bin, Change dry type, Change owner, Set field…, Delete, "Select all in this filter"), and a FAB.
  - Add: live tag listening (pulsing), manual UID entry, recently seen chips, basics with owner avatars, bin swatches and dry type chips (last choice remembered per bin), collapsed location, a wardrobe step (camera / choose / import link, purchase and size, description preview), and a confirmation with "Add another".
  - Settings: read-only bins, dry types and wardrobe status, station health (online and last tap), per-owner work label, and a link to the HA options.
  - Detail: photo carousel, status chip, wardrobe sections, description chips with locks, Re-describe, last 10 taps, and a ⋯ menu with Edit and Delete.
  - Live state: websocket subscription with reconnect, backoff and a "Reconnecting…" pill.
  - Accessibility: labels everywhere, never colour alone, visible focus, `prefers-reduced-motion`.
- **Tokens:** `frontend/tokens.css` with shared `--hub-*` structure tokens, the Laundry `--lh-*` palette, the `--app-*` mapping, a dark mode and Follow HA. Documented in `docs/design/tokens.md`.

## Decisions made without asking
See `DECISIONS.md` (24 items with reasons). Highlights:
- **Store v3 is a major version bump**, so HA won't load it under v0.1.2. A full v2 backup is written before migrating, and the README gives the rollback steps.
- **Wardrobe off keeps v0.1's single cover photo.** Uploads still go through the new resize/strip pipeline.
- **Photo uploads use a multipart HTTP view** (10 MB doesn't fit reliably in a websocket message). Photo URLs are pre-signed in `data`.
- **AI attachments go through a non-browsable `media_source` platform**, because `ai_task` only accepts media-source attachments.
- **"Needs review"** = core fields unset, or AI confidence below 0.7.
- **Import data is never overwritten by re-describe.**
- **Free-text product attributes are scanned for vocabulary terms.**
- **The Add flow creates the garment when leaving step 2.**
- **No web font is downloaded** (Inter if installed, otherwise system-ui).

## Deviations from the prompt
1. **Settings edits the per-owner "work" label.** The spec makes Settings read-only, but there's no other place for this label. To revert, remove the People card from `settings.js` and the `owner/update` command.
2. **The `import_product_url` and `describe_garment` actions are registered even with Wardrobe off** (they refuse with `wardrobe_disabled`). HA registers services before options are known. To revert, register them conditionally in setup_entry.
3. **The rise field is forced to `n/a` for non-bottoms**, even if the AI returns a value. The schema says "bottoms only". To revert, see `taxonomy.coerce_field`.

## Tests
- pytest (HA 2026.10.4, Python 3.14): 246 passed, 96.5 % coverage (gate 90 %).
  - New `tests/test_wardrobe.py` (16 tests) and `tests/test_import_describe.py` (39 tests).
  - Updated `tests/test_websocket.py`, which now uses real images.
- Migration: lossless v2 → v3, v1 → v3, unknown keys round-trip, backup written, taxonomy remap on load.
- Wardrobe-off regression: a v0.1.2 store + config; taps, sensors and views unchanged; every wardrobe command refused; owner/bin bulk changes still work.
- Photos: EXIF orientation applied, metadata gone (EXIF, ICC, maker string), 1600 px / 400 px sizes, no upscaling, alpha → white, junk and oversize rejected, upload limit 8, admin only, reorder, delete removes files, garment delete removes all, legacy thumbnail fallback.
- URL import: JSON-LD, og-only, nothing, 403, 500, timeout, non-HTML, redirect without Location, redirect to private, non-image image URL. SSRF cases: 127.0.0.1, 192.168.x, 10.x, ::1, `::ffff:127.0.0.1`, 169.254.169.254, localhost, .local, ftp, file, bad port, a hostname resolving private, empty DNS, NXDOMAIN. No live network or DNS (DNS patched, aiohttp mocked).
- AI: mocked `ai_task` for success, schema mismatch, provider error and exception. At most 3 attachments with the cover first; low confidence → null; vocabulary mapping; per-type subtypes; locks survive re-describe; Reset to AI; manual-edit validation; auto-describe after the first photo only; bulk "Set field…"; Needs review; media-source resolution limited to the garment's own photos.
- Panel: Playwright at 390×844 and 1280×800, with Wardrobe on and off.
  - Renders all four tabs and the detail sheet.
  - Filter sheet with subtype cascade, taxonomy search, Select mode bar.
  - A live tap fills the UID and pre-selects the location from the station.
  - Create payload, Settings health, Reconnecting pill.
  - No horizontal scroll, no console errors, XSS inert.
  - Screenshots go to the `panel-screenshots` CI artifact.
- Mutation testing on the state machine (unchanged): 112/114 = 98.2 %. Firmware host tests: 484 checks. `esphome config` passes for both readers.
- Nothing was skipped.

## Bugs found and fixed
| # | found by | bug | fix | regression test |
|---|---|---|---|---|
| 1 | test_wardrobe | `thumb_name("noext")` returned `.thumb.jpg` | Handle names without an extension | `test_ensure_thumbnail` |
| 2 | test_import_describe | JSON-LD values kept HTML entities (`&amp;`) | Unescape extracted text | `test_parse_json_ld` |
| 3 | test_import_describe | Taxonomy didn't map a singular to a plural ("sneaker" → "sneakers") | Two-way plural matching | `test_taxonomy_coerce` |
| 4 | test_import_describe | Free-text materials ("Merino wool") didn't map | `taxonomy.extract` | `test_taxonomy_coerce`, `test_import_preview_and_apply` |
| 5 | panel smoke test | A long wash-bin name made the phone layout scroll sideways | Swatches and cards wrap and shrink | smoke test `noHScroll` |
| 6 | review | Two panel modules defined the same action name (`saveBasics`) | Renamed, plus a check for duplicates | — |

## Needs hardware or a person to verify
1. **Phone camera:** open the panel in the iPhone and Android companion apps, then Add → Take photo. The camera should open directly. Check that portrait photos come out upright and that thumbnails load.
2. **Live tag:** with a station flashed, open Add and tap a new tag. The form should advance with the UID filled in and the closet pre-selected.
3. **AI Task:** set an AI Task entity (e.g. OpenAI or Google Gemini) and add a photo. A description should appear within a minute. Then change a field and Re-describe: the change should stay.
4. **URL import:** try 3 real store links. Expect some stores to block automatic imports; you should see the friendly message.
5. **Dark mode and Follow HA theme colour:** switch the HA theme and check contrast.
6. **Rollback drill (optional):** check that `.storage/laundry_hub.v2.backup` exists after upgrading.
7. **Station health:** pull a station's power. Settings should show Offline within ESPHome's unavailable timeout.

## Known gaps and bugs
- **HEIC photos:** an iPhone HEIC file sent as-is (not converted by the browser) is refused, because Pillow has no HEIC support in HA core.
- **DNS rebinding:** the SSRF guard checks DNS before each request, but aiohttp resolves again when connecting. That leaves a small DNS-rebinding window.
- **Media browser:** "Laundry Hub" may appear as a source in HA's media browser. Browsing it shows an error by design.
- **Photo URLs expire after 24 h.** Reloading the panel signs them again.
- **Health "online" depends on ESPHome entities** existing for the device.
- **Pantry Hub accent palette:** the "pressed" value and the others not in the mockup are my picks.

## Open questions for Heather
1. **Wardrobe-off photos:** keep one cover photo (v0.1 parity), or allow 8 even when off? Recommendation: keep one.
2. **"Needs review" threshold:** is 0.7 right? Recommendation: keep it, and tune after real AI results.
3. **Auto-describe:** should it also run when a URL import adds the first photo? Currently yes, through the same first-photo path. Recommendation: yes.
4. **Media browser:** should garment photos be hidden from it entirely, by dropping the media-source listing? Recommendation: fine as is.
5. **Per-owner work label:** keep it editable in the panel, or move it to an options sub-step? Recommendation: keep it in the panel.

## Suggested next steps
- **v0.2.1:** purchase inbox (IMAP + AI extraction) on top of the `purchase` model.
- **v0.3:** "out" state and wear log using the reserved fields.
- Port `tokens.css` to Pantry Hub.
- Replace the README screenshot placeholders with the CI artifact images once the panel runs on real data.

---

# Laundry Hub build report
- Version: 0.1.2 (release versioning; no behaviour change)
- Date: 2026-11-27
- Repo: https://github.com/thermitehash/ha-laundry-hub
- PR: https://github.com/thermitehash/ha-laundry-hub/pull/2 (branch `claude/laundry-hub-build-rmcoqp`, not merged). v0.1.1 was merged in PR #1 (`0d2dc89`).
- Commit: `bdc2cc1` (last code change, all CI green); this report is committed on top as a docs-only commit
- CI (per job), workflow **Validate**, run 46066911209 on `bdc2cc1`: all 15 ✅
  - Version consistency ✅ · Version bump ✅ (0.1.2 > 0.1.1 on `main`)
  - lint (ruff, ruff format, mypy --strict, yamllint) ✅
  - tests (ubuntu-latest) ✅ · tests (macos-latest) ✅
  - mutation ✅
  - panel ✅
  - firmware-host-tests (ubuntu-latest) ✅ · (macos-latest) ✅
  - esphome (ubuntu-latest, st25r_i2c) ✅ · (ubuntu-latest, rc522) ✅
  - esphome (macos-latest, st25r_i2c) ✅ · (macos-latest, rc522) ✅
  - hassfest ✅
  - hacs ✅
  - Release workflow: not run on the PR (`workflow_run` only fires from the default branch); first runs after merge
  - Previous merge (v0.1.1, `0d2dc89`) on `main`: all 13 ✅, including the full HACS license check

## Release versioning (0.1.2)
- **The first release, v0.1.2, is created automatically on merge.** The **Release** workflow (`.github/workflows/release.yml`) is wired to `workflow_run: workflows: [Validate]`, and **`Validate`** is the exact `name:` of this repo's main CI workflow (`.github/workflows/ci.yml`, renamed from `CI` to match the other repos). When Validate passes for a `push` to `main`, Release checks out that run's `head_sha`, reads `manifest.json` → `0.1.2`, and runs `gh release create v0.1.2 --target <sha>`. The title and notes come from `docs/releases/v0.1.2.md`.
- **Copied pattern:** `release.yml` is thermitehash/ha-kroger-restock's file with only the manifest path changed (`kroger_restock` → `laundry_hub`), verified with `diff`. Trigger, `permissions: contents: write`, `concurrency: release` (no cancel), the job guard (dispatch, or a successful run of a `push` event), the `head_sha` checkout, the already-released → exit 0 rule, and the notes fallback to `--generate-notes` are all unchanged. It creates no draft or prerelease, and nothing is chained off the release event.
- **The tag is `v` + the manifest version exactly** (`v0.1.2` ↔ `"0.1.2"`), so the version HACS offers and the version HA shows agree.
- **One source of truth:** `manifest.json` → `version`. `script/check_versions.py` (CI job **Version consistency**, every event) fails unless `const.py` `VERSION`, `pyproject.toml` `[project].version` and the ESPHome `firmware_version` substitution all equal it. It also fails if `docs/releases/v<version>.md` is missing or doesn't start with `# v<version>: `.
- **Version bump on PRs:** `script/check_version_bump.py`, ported from thermitehash/aiochainway, in CI job **Version bump** (`pull_request` only, `fetch-depth: 0`, `python script/check_version_bump.py "origin/${{ github.base_ref }}"`). A PR that changes `custom_components/` or `esphome/` must raise the manifest version above both the base branch's manifest and the latest `v*` tag, compared as versions, not strings. Docs-only and CI-only PRs are exempt, including Markdown files inside those folders. The failure message names the version to beat and the files to bump.
- **Firmware:** `esphome.project.version: ${firmware_version}` (substitution, `"0.1.2"`). It tracks the integration version and is not released separately. Release notes say to reflash the stations only when files under `esphome/` changed since the previous tag, not counting the version number, comments or blank lines. The rule and a check command are in `docs/releases/README.md`. For v0.1.2 the only firmware change is the version plumbing, so its notes say no reflash is needed.
- **hacs.json:** unchanged. `render_readme: true`, no `zip_release` (HACS downloads the tagged source), and `homeassistant: 2026.10.0`, the true minimum (`probatio`).
- **Release notes:** `docs/releases/v0.1.0.md`, `v0.1.1.md` and `v0.1.2.md`, in Kroger Restock's format, plus `docs/releases/README.md` (format and reflash guidance).
- **Cannot be tested on the PR:** `workflow_run` only fires for workflow files on the default branch. The Release workflow therefore first runs after merge. If it doesn't fire, run it once by hand (Actions → Release → Run workflow); it releases the manifest version on `main` and does nothing if that tag already exists.
- **CI on `main` is never cancelled** (`cancel-in-progress` only for PRs), so no commit on `main` loses its Validate run, and with it its release.

## Owner decisions applied in 0.1.1 (kept)
1. License: MIT kept.
2. Repeat tap restarts the 5-minute window: kept (current behaviour).
3. **Changed:** a laundry tap in auto mode on a `drying` garment now stays `drying` and re-flashes its dry zone, whatever the time. Rewashing needs mode `sort`. State machine, README table, tests and the mutation check were updated.
4. Repo topics are set by the owner, so the HACS check now validates topics. Brand icons stay placeholders.

## Summary
Built in one pass against fakes, with no hardware on site. **Nothing is verified on hardware.**
- HACS integration `laundry_hub`:
  - UI config: main entry, options flow, wash bin and dry type subentries.
  - Versioned garment catalog.
  - Pure tap state machine.
  - WLED zone flashing.
  - Sensors and tap event entity.
  - 6 actions and a validated websocket API.
  - Admin sidebar panel (Garments / Enroll / Status).
  - Redacted diagnostics.
- ESPHome firmware for 3 AtomS3 Lite stations:
  - New `st25r_i2c` external component. It reuses the upstream `st25r` reader through subclassing; the I2C transport is the only new code.
  - The RFID2 (`rc522_i2c`) fallback is selectable by substitution.
- Tests: 162 pytest tests (97 % coverage), Hypothesis property tests, mutation score 98.2 % on the state machine, 484 C++ host-test checks, panel smoke test.
- CI: lint, mypy --strict, hassfest, HACS validation; tests on ubuntu + macOS; `esphome config` and `compile` for both readers.

## Assumptions (choices made without asking)
1. **HA version / Python:** tests run against HA 2026.10.4 (latest the `pytest-homeassistant-custom-component` release pins); Python 3.14. `hacs.json` minimum is 2026.10.0. HA 2026.10 replaced voluptuous with `probatio`, so the integration imports `probatio` (see Bugs #3).
2. **Garment state `new`:** added for a garment that has been enrolled but never placed. It has no location; it is not in a closet or the wash. If you enroll with a closet location, the garment starts as `put_away`.
3. **Repeat window (confirmed by owner):** "last laundry tap" counts every tap at the laundry station, repeats included. A repeat therefore restarts the 5-minute window.
4. **Drying garments (owner decision, 0.1.1):** a laundry tap in auto mode on a `drying` garment never changes its state. It re-flashes the dry zone, whatever the time. Rewash needs mode `sort`. An `in_wash` garment still re-flashes its wash bin within 5 minutes and moves to `drying` after that.
5. **Tap time:** decisions use the event's fire time, not the processing time.
6. **Feedback values:** a closet tap sends `known` when the garment was already in that closet, otherwise `moved`. A laundry step sends `known`. An unknown UID sends `unknown`. An internal problem (missing zone, corrupt state, exception) sends `error`.
7. **Feedback timeout setting** (integration, default 2 s) is how long HA waits on the station's feedback action. The 2 s amber fallback runs separately on the device.
8. **Station devices are optional.** The integration works before the hardware exists. Feedback is resolved from the selected ESPHome device's config-entry `device_name` → `esphome.<name>_feedback`.
9. **Station spoofing guard:** when a station device is configured, a tap event claiming that `station_id` from a different `device_id` is ignored.
10. **Unknown-tag notification throttle:** at most one notification per UID per hour. The panel link is `/laundry?page=enroll&uid=<UID>`. It is sent as `data.url`, `clickAction` and a `URI` action for mobile_app, or via `notify.send_message` for notify entities.
11. **UID rules:** 4, 7 or 10 bytes of hex (ISO 14443A). Separators (`-`, `:`, spaces) are stripped and letters uppercased, both in firmware and in HA.
12. **Missing detection** uses `last_seen`, or `created` if the garment was never seen. The sensor refreshes hourly and on every change. A manual `set_location` counts as seen.
13. **Lights:** one flash at a time across all zones (last one wins). A repeat flash on the same light extends the timer without an off/on in between. A light call has a 5 s timeout.
14. **Zone selection by name:** actions accept a wash bin or dry type name (case-insensitive) or a subentry id. An `enroll_garment` without a bin or dry type uses the first one by order.
15. **Photos:** JPEG, PNG or WebP up to 2 MB, uploaded as base64 over the websocket. Stored as `<garment_id>.<ext>` and served by an authenticated view. The panel can upload and remove photos but does not show thumbnails yet.
16. **Bin sensors:** `sensor.laundry_hub_bin_<slug>` sensors are deliberately *not* linked to their subentry in the registry; the integration manages their lifecycle (see Bugs #2). They have no device, so the entity ID keeps the `laundry_hub_bin_` prefix.
17. **Seed colours:** Darks (40,40,160), Lights & whites white, Colors orange, Reds red, Delicates pink; Dryer orange, Hang rod cyan, Lay flat green.
18. **Firmware:**
    - I2C at 100 kHz on the Grove cable.
    - Reader polled every 250 ms at RF power 15.
    - Recovery backoff 1 s doubling to 60 s.
    - Feedback LED shown for 1.5 s at 60 % brightness; amber fallback for 0.4 s.
    - Idle mode colours at 15 % brightness.
    - The mode button acts on a single click of 30–800 ms.
    - Wi-Fi fallback AP plus captive portal.
19. **ESP-IDF framework** on the AtomS3 (ESPHome's default for ESP32-S3).
20. **License: MIT** (HACS requires one; confirmed by owner).
21. **Panel smoke test CI job pinned to `ubuntu-24.04`:** Playwright 1.56 has no Chromium build for `ubuntu-latest` (26.04).
22. **(0.1.2) Firmware version tracks the integration version** and is part of the consistency check, so there is one version number everywhere.
23. **(0.1.2) The bump check compares against both the base branch's manifest and the latest `v*` tag.** aiochainway compares against the tag only; the spec asked for the base branch, and checking both is stricter.
24. **(0.1.2) Markdown files under `custom_components/` or `esphome/` don't need a bump** (docs-only exemption).
25. **(0.1.2) Scripts moved from `scripts/` to `script/`**, matching the owner's other repos.
26. **(0.1.2) `release.yml` is exempt from yamllint's line-length rule only**, so the copied file stays verbatim.

## Firmware (reader transport approach, upstream reuse vs vendored, pins, LED/button behaviour, events/actions)
- **Transport approach:** ESPHome 2026.11's `st25r` has an abstract transport: five pure-virtual methods (`read_register`, `write_register`, `write_command`, `write_fifo`, `read_fifo`), with `st25r_spi` as one implementation.
  - `components/st25r_i2c` subclasses `st25r::ST25R` + `i2c::I2CDevice` and implements only those five methods over I2C.
  - **Nothing is vendored.** Tag logic, register profiles, anticollision, `on_tag` / `on_tag_removed` and the listeners all stay upstream.
  - The YAML schema extends `st25r.ST25R_SCHEMA` (default address 0x50) and adds `max_backoff`.
- **I2C framing** lives in the pure header `components/st25r_i2c/st25r_i2c_proto.h`. The mode byte after the address:
  - `00AAAAAA` register write
  - `01AAAAAA` register read, then repeated START + read
  - `0x80` FIFO load
  - `0x9F` FIFO read
  - `0xC0–0xFF` direct command (except `0xFB`)
  - `0xFB` Space-B prefix (upstream encodes Space-B registers with bit 0x40)
- **Error handling:**
  - Every transfer records its I2C error code. Any error (NACK, timeout, ...) aborts the scan, resets the upstream state to IDLE, zeroes FIFO bytes that failed to read, and sets a warning status.
  - Re-init runs from `update()` (never blocking `loop()`), with exponential backoff that is safe across `millis()` wraparound.
  - `setup()` reimplements upstream's setup **without `mark_failed()`**, so a missing reader at boot is retried and can never cause a reboot loop.
  - Known blocking: upstream `transceive_ex_` can busy-wait up to 150 ms once per failed SELECT, and `reset_chip_` uses about 50 ms of `delay()`. Both happen only during recovery.
- **Pins (AtomS3 Lite):** Grove SDA = GPIO2 (G2), SCL = GPIO1 (G1); RGB LED (WS2812, GRB) = GPIO35; button = GPIO41, active low.
- **Reader selection:** `reader: st25r_i2c | rc522` → `packages/readers/${reader}.yaml`. The RFID2 uses core `rc522_i2c` at 0x28.
- **Tap event:** the UID is normalised (uppercase hex, no separators), then the same UID is debounced for 3 s. The station then fires `esphome.laundry_tap` `{station_id, uid, mode}` (HA adds `device_id`).
- **Feedback:**
  - HA action `esphome.<device>_feedback` (`result`): known = green, unknown = blue, error = red, moved = white. An unexpected value shows red and is logged.
  - If HA has not answered 2 s after a tap, the LED gives a short amber blink.
- **Mode (laundry station only, `packages/mode-button.yaml`):**
  - A click cycles auto → sort → dry → auto.
  - Exposed as the select **Tap mode**; HA can set it too.
  - Idle LED: auto = off, sort = dim purple, dry = dim teal.
  - Returns to auto after 5 min with no tap or button press (checked every 10 s).
  - Closets have no button or select and always send `auto`.
- **Secrets:** `wifi_ssid`, `wifi_password`, `fallback_ap_password`, `api_encryption_key`, `ota_password`, all via `!secret`. The repo ships only `secrets.yaml.example`.

## Integration (config + subentries, storage schema, state machine, entities, services, panel)
- **Config:**
  - Single instance (`single_config_entry`). Settings live in `options`: station devices (3 ESPHome device selectors, optional), feedback timeout, flash duration (15 s), missing after (14 d), notify target (validated as a notify entity or notify action).
  - Options flow uses the same form.
  - Subentry types `wash_bin` and `dry_type`, each with `name`, `color` (RGB selector), `light_entity` (light selector, optional) and `order`. Names must be unique per type.
  - Reconfigure edits a zone and has a **Delete** checkbox. Delete is blocked with `zone_in_use` ("{name} is used by {count} garment(s) … Bulk reassign") while garments use the zone.
  - HA's own subentry delete button cannot be vetoed, so the update listener re-adds an in-use zone with the same id and raises the repair issue `zone_restored`. At startup, a garment that points at a zone deleted while HA was down is moved to the first zone of that type, so no garment ever points at a deleted zone.
- **Storage:** `Store` key `laundry_hub`, version 2. Atomic writes, 5 s debounce, flushed on unload. Layout:
  - `{garments: {id: {id, uids[], name, owner, wash_bin, dry_type, notes, photo, state, location, last_seen{time,station}, last_laundry_tap, created}}, last_unknown, unknown_tags{uid: {first_seen,last_seen,station,count}} (max 50), quarantine[]}`
  - A v1 → v2 migration is included. v1 was a pre-release list format with a single `uid` and `bin`/`dry` keys.
  - Malformed records, records with duplicate UIDs, and records with naive timestamps are quarantined, never dropped. Inconsistent states are repaired on load.
- **State machine:** `statemachine.decide()`, pure and mutation-tested. See the README table. Unknown modes are treated as auto. Taps are serialised by a lock; flashes and feedback run as background tasks.
- **Entities:**
  - `sensor.laundry_hub_closet_1_count`, `_closet_2_count`
  - `sensor.laundry_hub_in_wash` (attributes: `bins` name → count, `bin_ids`)
  - `sensor.laundry_hub_drying` (attribute: `dry_types`)
  - `sensor.laundry_hub_missing` (attribute: `garments`, capped at 50)
  - `sensor.laundry_hub_unknown_tags` (attribute: `last_unknown`)
  - `sensor.laundry_hub_bin_<slug>` per wash bin
  - `event.laundry_hub_tap` (`in_wash`, `drying`, `put_away`, `unknown`; attributes: garment, uid, station, mode, zone, repeat)
- **Actions:** `enroll_garment` (response: garment_id), `update_garment` (incl. `add_uid` / `remove_uid`, never removing the last tag), `remove_garment`, `set_location`, `reassign` (`garments` or `from`; response: moved), `flash_zone`. All take a garment id or UID, are validated with schemas plus translated `ServiceValidationError`s, and changes are atomic.
- **Websocket (admin only):** `laundry_hub/data`, `subscribe`, `garment/create|update|delete|set_location`, `reassign`, `zone/remove|flash`, `unknown/dismiss`, `photo/upload|delete`.
- **Panel:** `/laundry`, admin-only, a plain web component served from the integration (no build step). Writes go only through the websocket commands, and all user strings are escaped.

## WLED segment table (as shipped)
60 LED/m, 180 LEDs. Start is inclusive and Stop exclusive, as in the WLED UI. Adjust to measured lengths.

| Seg | Zone | Start | Stop | LEDs |
|---:|---|---:|---:|---:|
| 0 | Darks | 0 | 24 | 24 |
| 1 | Lights & whites | 24 | 48 | 24 |
| 2 | Colors | 48 | 72 | 24 |
| 3 | Reds | 72 | 96 | 24 |
| 4 | Delicates | 96 | 120 | 24 |
| 5 | Dryer | 120 | 138 | 18 |
| 6 | Hang rod | 138 | 156 | 18 |
| 7 | Lay flat | 156 | 174 | 18 |
| – | spare | 174 | 180 | 6 |

Power: brightness limiter 5000 mA on a 6 A supply or 8500 mA on a 10 A supply; brightness cap of about 128. Full white is about 2 A/m. Details in `docs/wled.md`.

## Spec changes vs. this spec
0. (0.1.2) Main CI workflow renamed `CI` → `Validate` so Release can listen for it by the same name as in the other repos.
1. **Protocol header path:** `esphome/tap-station/components/st25r_i2c/st25r_i2c_proto.h` instead of `include/st25r_i2c_proto.h`. ESPHome copies only files directly inside an external component's directory into the build.
2. **Added garment state `new`** (see Assumption 2).
3. **Block-on-remove:** the subentry's Reconfigure → Delete path, the panel and the websocket path are blocked with a clear error. HA core's generic subentry delete button cannot be vetoed, so an in-use zone removed that way is **restored automatically**, with a repair issue that points to Bulk reassign.
4. **Bin sensors are not registry-linked to their subentry** (lifecycle managed by the integration; see Bugs #2). They are still created and removed with the bins.
5. **`probatio` instead of `voluptuous`** for schemas (HA 2026.10+), so the minimum HA version is 2026.10.
6. **`set_location` takes `location`:** `closet_1`, `closet_2`, `in_wash` or `drying`. It sets both state and location.
7. **Websocket and actions accept zone names as well as ids.**

## Open questions (numbered; mark which need hardware)
1. **[hardware]** Does the Unit NFC U216 answer at 0x50 with the ST25R3916 IC identity (0x28 or 0x30 in bits 7:3)? Does upstream's register profile give usable range through silicone and heat-seal tags at RF power 15?
2. **[hardware]** Is upstream `st25r` timing (field guard, 100 ms WUPA timeout) OK over I2C at 100 kHz, or is 400 kHz needed? (`frequency:` in the package.)
3. **[hardware]** Upstream writes `IO_CONF2` with SPI pull-down bits (0x18) even over I2C. They are believed harmless, so please confirm. `IO_CONF1` `i2c_thd` stays at 00 (standard and fast mode).
4. **[hardware]** Does the AtomS3 Lite LED need `chipset: SK6812` instead of `WS2812`, and is GRB the right order?
5. **[hardware]** Does turning on a WLED segment light from HA also turn on the WLED master when it is off? Expected yes with the core integration.
6. **[hardware]** Are the PPS buttons ISO 14443A? An ISO 15693 tag will not be read by either reader configuration.
7. ~~Repeat tap extends the window?~~ Resolved: yes (owner).
8. ~~Drying + laundry tap = rewash?~~ Resolved: stays drying; rewash needs sort (owner, 0.1.1).
9. HACS reads the license from the default branch (now on `main` since PR #1), so the license check is skipped on PRs and runs on `main` after merge. Topics are set and now validated.
10. Real brand icons: `brand/*.png` stay placeholders for now (owner).

## Test results (tests, coverage, property tests, mutation score, firmware host tests, compile)
- **pytest** (HA 2026.10.4, Python 3.14): 191 passed (162 integration + 29 release-script tests).
  - Release-script tests: the repo itself is consistent; every mismatched copy, missing copy, missing notes file and bad notes title is reported; the bump check runs against real temporary git repos (docs/CI/tests/Markdown-only exempt, code or firmware change without a bump fails, a bump passes, 1.9.0 → 1.10.0 compares as versions, a downgrade fails, the version must also beat the latest tag, first release with no base manifest).
- **(0.1.2)** `python script/check_versions.py` and `python script/check_version_bump.py origin/main` pass locally on this branch. Coverage 97.2 % (gate 90 %).
  - Covers every flow (config, options, wash bin and dry type subentries), seeding, block-on-remove (flow, websocket, restore after a generic delete), every state-machine transition, the repeat window, closet moves, unknown tags, missing detection with a frozen clock, flash / replacement / unavailable / missing / failing light, storage migration, quarantine and repair, websocket and action validation (bad UID, unknown subentry, duplicate UID, admin-only), and diagnostics redaction.
- **Hypothesis:** 200 random sequences of up to 40 operations each. Operations: taps across stations and modes (including bogus modes and unknown tags), time jumps, enroll and remove, zone add, edit, remove and generic delete, reassign, set_location. After every step the test checks for no invalid state, no reference to a missing or wrong-type subentry, a consistent UID index, and every sensor equal to the catalog. Ran 3 × 200 locally.
- **Mutation (mutmut 3.8.1, `statemachine.py`, rerun for 0.1.1):** 112/114 killed = **98.2 %** (target 85 %). The 2 survivors are equivalent mutants (`state` → `None` where `changed` is always true).
- **Firmware host tests:** 484 checks passed (g++ `-std=c++17 -Wall -Wextra -Werror -pedantic`, ASan + UBSan).
- **esphome config:** passed locally for all 3 stations × both readers (ESPHome 2026.11.1).
- **esphome compile** (firmware unchanged in 0.1.1): blocked locally. This sandbox's network policy denies `dl.espressif.com` and `components-file.espressif.com` (ESP-IDF registry). Compile is **green in CI** for both readers on ubuntu and macOS: the laundry station on both OSes, plus closet-1 on ubuntu. `st25r_i2c.cpp` compiles against upstream `st25r` for ESP32-S3 / ESP-IDF 5.5.6. Example closet-1 (st25r_i2c): RAM 31.0 % (106 KB of 342 KB), flash 49.0 % (898 KB of 1.8 MB).
- **Panel smoke test** (headless Chromium, fake `hass`): passed. It renders all 3 pages, checks filters and the enroll prefill, shows server errors, and confirms that an XSS payload stays inert. Verified that the test fails when escaping is removed.
- **Lint:** ruff, ruff format, mypy --strict (16 files, 0 issues), yamllint — clean.

## Bugs found and fixed (table: # | found by | bug | fix | regression test)
| # | found by | bug | fix | regression test |
|---|---|---|---|---|
| 1 | test run (deprecation log) | Feedback lookup used `DeviceEntry.config_entries`, deprecated in HA 2026.x and scheduled to break in 2027.10 | Use `device.config_entry_id` | `test_taps.py::test_feedback_device_resolution` |
| 2 | `test_zone_guard` | After a generic delete of an in-use bin, the restore worked but the bin's sensor vanished. HA's subentry removal deletes registry entries asynchronously, racing the reload | Bin sensors no longer linked to the subentry; stale entries pruned by the integration | `test_zone_guard.py::test_native_delete_of_used_zone_is_restored`, `test_property.py::test_regression_restore_then_reload` |
| 3 | mypy --strict | Schemas used `voluptuous`; HA 2026.10 APIs are typed for `probatio` (silently accepted at runtime today) | Switched to `probatio` | mypy --strict in CI |
| 4 | self-review (storage corruption) | A garment stored with an invalid or inconsistent state (e.g. `put_away` without a closet) answered every tap with `error` forever | `sanitize_garment()` repairs state on load and logs it | `test_store.py::test_inconsistent_states_repaired_on_load` |
| 5 | self-review (race) | Taps queued behind the tap lock were judged by processing time, so a busy HA could turn a repeat into a step | Use the event's `time_fired` | `test_taps.py::test_queued_taps_use_fire_time` |
| 6 | self-review (security) | Any ESPHome device could fire a tap claiming a configured station | Taps whose `device_id` mismatches the configured station device are ignored | `test_taps.py::test_tap_from_wrong_device_ignored` |
| 7 | self-review (resource) | The unknown-tag notification throttle map grew without bound | Prune expired entries on insert | `test_taps.py::test_notify_throttle_map_pruned` |
| 8 | Hypothesis model | Not a product bug: the model compared the missing sensor without letting its hourly refresh run. Documented as a known limitation (hourly granularity) | Model fires the refresh | `test_property.py` |
| 9 | CI (panel job) | Playwright 1.56 has no Chromium for `ubuntu-latest` (now 26.04) | Panel job pinned to `ubuntu-24.04` | panel CI job |
| 10 | CI (hacs job) | HACS failed: no license | Added MIT `LICENSE`. The license check is skipped on PRs only, because HACS reads default-branch metadata | hacs CI job (full check on `main`) |

Self-review checklist:
- Blocking calls: photo and file I/O in the executor; no sync I/O in the loop. Flagged by a test check (`ASYNC` ruff rules).
- Exceptions in callbacks: the tap handler catches everything and returns `error` feedback; light, notify and feedback calls never raise. Tested.
- Rapid taps: serialised by a lock, lights last-wins under their own lock. Tested with 5 taps in one loop iteration and concurrent flashes.
- Timezones: everything is aware UTC; naive stored timestamps are quarantined; the repeat window is symmetric for small clock regressions. Tested.
- Storage on restart: atomic writes, debounced plus a final write on stop and unload, quarantine and repair on load; HA core handles corrupt JSON. Tested.

## After merging this PR
1. Validate runs on `main`; when it passes, Release publishes **v0.1.2** (Actions → Release; Releases page). If Release does not run, start it once with *Run workflow*.
2. In HACS, Laundry Hub should show **0.1.2** as available, matching `manifest.json`.
3. Future releases: bump the version in a PR (all four places plus a new `docs/releases/v<version>.md`), then merge. Never tag by hand, and never change the version in a docs-only commit after a release.

## First-run checklist when hardware arrives
1. Copy `esphome/tap-station/` into the ESPHome Device Builder and add the secrets (see `esphome/README.md`). Flash `laundry-room.yaml` over USB.
2. Check the logs: the I2C scan should find **0x50**, followed by `st25r: IC: 0x2X/0x3X (ST25R3916…)`. If you see `Reader init failed`, check the Grove cable; if the problem persists, set `reader: rc522` with the RFID2.
3. Tap an NTAG213 sewn tag. The log should show `Tap <UID> at laundry` (14 hex characters), and HA should receive `esphome.laundry_tap` (Developer tools → Events → listen). Check read range through each tag type, including the PPS buttons (Open question 6).
4. Adopt the device in HA (ESPHome integration). In Laundry Hub → Configure, select it as the Laundry station, then tap again: an unknown tag gives a blue LED and an enrollment notification. Enroll it from the panel; the next tap gives green.
5. Unplug HA or the Wi-Fi and tap: you should see an amber blink after 2 s.
6. Press the button: the LED goes dim purple (sort), then dim teal (dry), then off; it returns to auto after 5 min. Check that **Tap mode** in HA follows.
7. Pull the Grove cable while running: the log shows an I2C error and the backoff. Plug it back in: `Reader recovered`, with no reboot.
8. Flash closet-1 and closet-2 and select them in Configure. A closet tap gives white (moved), then green when tapped again.
9. Set up the WLED segments per `docs/wled.md`. Set the brightness limiter and the boot preset with everything off. Map each segment in its bin or dry type → Reconfigure, then run *Test light* for every zone.
10. Full loop: closet → laundry (bin lights) → laundry again after more than 5 min (dry zone lights) → laundry again (dry zone re-flashes, stays drying) → closet. Check the sensors and the panel's Status page.
11. Answer the open questions marked [hardware], and adjust `frequency`, `chipset` and the LED indexes if needed.
