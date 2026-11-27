# Laundry Hub build report
- Version: 0.1.0
- Date: 2026-11-27
- Repo: https://github.com/thermitehash/ha-laundry-hub
- PR: https://github.com/thermitehash/ha-laundry-hub/pull/1 (branch `claude/laundry-hub-build-rmcoqp`, not merged)
- Commit: @@COMMIT@@
- CI (per job): @@CI@@

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
- Tests: 158 pytest tests (97 % coverage), Hypothesis property tests, mutation score 98.2 % on the state machine, 484 C++ host-test checks, panel smoke test.
- CI: lint, mypy --strict, hassfest, HACS validation; tests on ubuntu + macOS; `esphome config` and `compile` for both readers.

## Assumptions (choices made without asking)
1. **HA version / Python:** tests run against HA 2026.10.4 (latest the `pytest-homeassistant-custom-component` release pins); Python 3.14. `hacs.json` minimum is 2026.10.0. HA 2026.10 replaced voluptuous with `probatio`, so the integration imports `probatio` (see Bugs #3).
2. **Garment state `new`:** added for a garment that has been enrolled but never placed. It has no location; it is not in a closet or the wash. If you enroll with a closet location, the garment starts as `put_away`.
3. **Repeat window:** "last laundry tap" counts every tap at the laundry station, repeats included. A repeat therefore restarts the 5-minute window.
4. **Repeat applies to drying too:** a laundry tap in auto mode within 5 minutes of the last laundry tap re-flashes the current zone if the garment is `in_wash` or `drying`. After 5 minutes a `drying` garment goes back to `in_wash` (rewash), per "anything other than in_wash → in_wash".
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
7. Should a repeat tap extend the 5-minute window (current behaviour) or not?
8. Should `drying` + laundry tap after 5 minutes mean rewash (current), or should it stay `drying`?
9. Repository topics and description for HACS (`home-assistant`, `hacs`, `esphome`, `nfc`): the owner needs to set these in GitHub settings. The HACS action ignores `topics`, `brands` and `images` until then.
10. Real brand icons: `brand/*.png` are placeholders.

## Test results (tests, coverage, property tests, mutation score, firmware host tests, compile)
- **pytest** (HA 2026.10.4, Python 3.14): 158 passed. Coverage 97.2 % (gate 90 %).
  - Covers every flow (config, options, wash bin and dry type subentries), seeding, block-on-remove (flow, websocket, restore after a generic delete), every state-machine transition, the repeat window, closet moves, unknown tags, missing detection with a frozen clock, flash / replacement / unavailable / missing / failing light, storage migration, quarantine and repair, websocket and action validation (bad UID, unknown subentry, duplicate UID, admin-only), and diagnostics redaction.
- **Hypothesis:** 200 random sequences of up to 40 operations each. Operations: taps across stations and modes (including bogus modes and unknown tags), time jumps, enroll and remove, zone add, edit, remove and generic delete, reassign, set_location. After every step the test checks for no invalid state, no reference to a missing or wrong-type subentry, a consistent UID index, and every sensor equal to the catalog. Ran 3 × 200 locally.
- **Mutation (mutmut 3.8.1, `statemachine.py`):** 111/113 killed = **98.2 %** (target 85 %). The 2 survivors are equivalent mutants (`state` → `None` where `changed` is always true).
- **Firmware host tests:** 484 checks passed (g++ `-std=c++17 -Wall -Wextra -Werror -pedantic`, ASan + UBSan).
- **esphome config:** passed locally for all 3 stations × both readers (ESPHome 2026.11.1).
- **esphome compile:** blocked locally. This sandbox's network policy denies `dl.espressif.com` and `components-file.espressif.com` (ESP-IDF registry). Compile runs in CI: @@COMPILE@@.
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
@@CIBUGS@@

Self-review checklist:
- Blocking calls: photo and file I/O in the executor; no sync I/O in the loop. Flagged by a test check (`ASYNC` ruff rules).
- Exceptions in callbacks: the tap handler catches everything and returns `error` feedback; light, notify and feedback calls never raise. Tested.
- Rapid taps: serialised by a lock, lights last-wins under their own lock. Tested with 5 taps in one loop iteration and concurrent flashes.
- Timezones: everything is aware UTC; naive stored timestamps are quarantined; the repeat window is symmetric for small clock regressions. Tested.
- Storage on restart: atomic writes, debounced plus a final write on stop and unload, quarantine and repair on load; HA core handles corrupt JSON. Tested.

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
10. Full loop: closet → laundry (bin lights) → laundry again after more than 5 min (dry zone lights) → closet. Check the sensors and the panel's Status page.
11. Answer the open questions marked [hardware], and adjust `frequency`, `chipset` and the LED indexes if needed.
