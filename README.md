# Laundry Hub

Home Assistant integration (HACS) plus ESPHome tap-station firmware for NFC garment
tracking and laundry sorting lights. Tap a tagged garment on a station and Laundry Hub
works out where it goes next. The matching wash bin or dry zone lights up on a WLED strip,
and the station's LED confirms the tap.

> **Status: v0.2.0, unverified on hardware.** Everything is built and tested against fakes
> (see [`docs/build-report.md`](docs/build-report.md)). Stations, readers, tags and WLED
> have not been tested on real hardware yet.

## How it works

| Tap at…            | Garment was…                         | Result                                   | Light            |
|--------------------|--------------------------------------|------------------------------------------|------------------|
| Laundry (auto)     | new or put away                      | → **in wash**                            | its wash bin     |
| Laundry (auto)     | in wash, last laundry tap ≥ 5 min ago | → **drying**                            | its dry type     |
| Laundry (auto)     | in wash, last laundry tap < 5 min ago | no change (repeat)                      | its wash bin again |
| Laundry (auto)     | drying (any time)                    | no change, stays drying                  | its dry type again |
| Laundry (sort)     | anything (use this to rewash)        | → **in wash**                            | its wash bin     |
| Laundry (dry)      | anything                             | → **drying**                             | its dry type     |
| Closet 1 / 2       | anything                             | → **put away** in that closet            | –                |
| Any                | unknown tag                          | recorded, enrollment notification sent   | –                |

Station LED: green = known, blue = unknown, red = error, white = moved to a closet,
amber = Home Assistant did not answer. Garments not seen for *missing after* days
(default 14) are listed as **missing**. They are never deleted automatically.

Every laundry-station tap, repeats included, restarts the 5-minute window.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/thermitehash/ha-laundry-hub`
   as type **Integration**.
2. Install **Laundry Hub**, then restart Home Assistant.
3. **Settings → Devices & services → Add integration → Laundry Hub**.

Requires Home Assistant **2026.10** or newer (developed against 2026.10/2026.11).

Manual install: copy `custom_components/laundry_hub` into `/config/custom_components/`.

## Setup

Everything is configured in the UI.

**Main settings** (set at setup, change later with **Configure**):

| Setting                | Default | Meaning |
|------------------------|---------|---------|
| Laundry / Closet 1 / Closet 2 station | – | ESPHome device of each tap station (optional until flashed; used for LED feedback) |
| Feedback timeout       | 2 s     | How long to wait for a station to accept the feedback action |
| Light flash duration   | 15 s    | How long a zone light stays on |
| Missing after          | 14 days | Not tapped for this long → missing |
| Notify target          | –       | `notify.xyz` entity or a notify action such as `mobile_app_phone` (enrollment prompts) |
| Wardrobe catalog       | off     | Photos, purchase details and AI descriptions per garment (see below) |
| AI Task entity         | –       | Used to describe garments from their photos (optional) |
| Follow HA theme colour | off     | Use your HA theme's primary colour instead of Laundry teal |

**Wash bins and dry types** are config subentries under **Settings → Devices & services →
Laundry Hub**. Use *Add wash bin* / *Add dry type*, or **Reconfigure** an existing one to
edit or delete it. Each has a name, a color, an optional light (WLED segment) and an order.
First setup seeds **Darks, Lights & whites, Colors, Reds, Delicates** and **Dryer, Hang rod,
Lay flat**.

Deleting a bin or dry type that garments still use is blocked, with an error that names the
number of garments. Move them first with **Laundry panel → Garments → Bulk reassign**.
If an in-use entry is deleted through the generic subentry menu (which cannot be blocked),
Laundry Hub restores it immediately and raises a repair issue that explains what to do.

## Laundry panel

The **Laundry** sidebar panel is admin-only and built for phones first: a bottom tab bar on
phones, a top bar on wide screens. It follows Home Assistant's light and dark mode, and taps,
moves and new garments appear without a refresh.

- **Home**: a tile per closet and wash bin with a count and cover photos, drying items, and a
  card for missing garments. Tap a tile to open those garments in Closet.
- **Closet**: photo cards (or initials on the bin colour). Search, plus filter chips for owner,
  location and wash bin. **Select** lets you move several garments to a bin, change their dry
  type or owner, set a field, or delete them.
- **Add**: tap the new tag on any station and the form fills in by itself. Then name, owner, wash
  bin and dry type (it remembers your last dry type per bin). With Wardrobe on, you can also add
  photos, purchase details and a description.
- **Settings**: bins, dry types and station health (online, last tap), with a link to the
  integration options for changes.
- **Garment detail**: photos, where it is now, bin and dry type, wardrobe details and the last
  10 taps. Edit and Delete are in the ⋯ menu.

Old links (`/laundry?page=enroll&uid=…`) still open the right screen.

Design tokens shared with Pantry Hub: [`docs/design/tokens.md`](docs/design/tokens.md).

## Wardrobe catalog (optional)

Turn on **Wardrobe catalog** in Laundry Hub → Configure. It's off by default; with it off,
everything works exactly as in v0.1.2.

- **Photos**: up to 8 per garment, taken straight from the phone camera. Each photo is resized to
  1600 px plus a 400 px thumbnail, rotated upright, and stripped of metadata such as location.
  Photos are stored in `/config/laundry_hub/photos/` and only shown to logged-in admins, never
  from `/local` or `www/`.
- **Purchase details**: brand, size (as printed, plus size system and cut), store, date, price
  (in your Home Assistant currency), product link and order reference.
- **Import from link**: paste a product page link to fill in name, brand, price and up to 4 photos.
  You see a preview first; nothing is saved until you tap Save. Only public http/https pages are
  fetched; local and private network addresses are refused.
- **AI descriptions** (needs an **AI Task entity** in the options): the cover and up to two more
  photos are sent to your AI Task provider, which classifies the garment from a fixed list (type,
  kind, colours, pattern, sleeve, neckline, length, fit, rise, material, formality, warmth, seasons,
  occasions) plus a one-line summary. Uncertain answers are left as "Not set". Anything you change
  by hand is locked, and re-describing never overwrites it (**Reset to AI** unlocks it).
- **Wear context**: what each garment is for (anything, work, never to work, occasions, athletic)
  and whether it's a uniform. Each person's "work" label can be renamed, for example "School".
- **Filters**: every detail above is a Closet filter with counts, plus **Needs photo** and **Needs
  review**. Search also matches details, for example "navy long sleeve".

> **Privacy:** when a garment is described, its photos and details are sent to the AI
> provider behind the AI Task entity you chose. Nothing is sent unless Wardrobe is on and an
> AI Task entity is set. Product-link imports fetch only the page you paste and its images.

## Entities

| Entity | State | Attributes |
|--------|-------|------------|
| `sensor.laundry_hub_closet_1_count`, `sensor.laundry_hub_closet_2_count` | garments put away there | – |
| `sensor.laundry_hub_in_wash` | garments in the wash | `bins` (name → count), `bin_ids` |
| `sensor.laundry_hub_drying` | garments drying | `dry_types` (name → count) |
| `sensor.laundry_hub_missing` | missing garments | `garments` (up to 50), `truncated` |
| `sensor.laundry_hub_unknown_tags` | unknown tags not yet enrolled | `last_unknown` |
| `sensor.laundry_hub_bin_<slug>` | garments in that wash bin (one per bin, created and removed with the bin) | – |
| `event.laundry_hub_tap` | event types `in_wash`, `drying`, `put_away`, `unknown` | garment, uid, station, mode, zone, repeat |

## Actions

| Action | Fields |
|--------|--------|
| `laundry_hub.enroll_garment` | `uid`, `name`, `wash_bin`?, `dry_type`?, `owner`?, `notes`?, `location`? (returns `garment_id`) |
| `laundry_hub.update_garment` | `garment` (id or UID), `name`?, `owner`?, `wash_bin`?, `dry_type`?, `notes`?, `add_uid`?, `remove_uid`? |
| `laundry_hub.remove_garment` | `garment` |
| `laundry_hub.set_location` | `garment`, `location` (`closet_1`, `closet_2`, `in_wash`, `drying`) |
| `laundry_hub.reassign` | `field` (`wash_bin`/`dry_type`), `to`, and `garments` or `from` (returns `moved`) |
| `laundry_hub.flash_zone` | `zone` (wash bin or dry type, name or id) |
| `laundry_hub.import_product_url` | `url`, `garment`?, `apply`? (returns the details found; with `apply` saves them). Needs Wardrobe on |
| `laundry_hub.describe_garment` | `garment` (describes it with the AI Task entity). Needs Wardrobe on |

Wash bins and dry types accept a name (case-insensitive) or a subentry id. A garment can
have several tags (replacement tags), and a tag belongs to at most one garment.

Example automation, announcing when something goes in the dryer:

```yaml
triggers:
  - trigger: state
    entity_id: event.laundry_hub_tap
    attribute: event_type
    to: drying
actions:
  - action: notify.mobile_app_phone
    data:
      message: "{{ state_attr('event.laundry_hub_tap', 'name') }} → {{ state_attr('event.laundry_hub_tap', 'zone') }}"
```

## Tap stations and lights

- Station firmware, wiring and flashing: [`esphome/README.md`](esphome/README.md)
- WLED segment table and power limits: [`docs/wled.md`](docs/wled.md)

| Seg | Zone            | LEDs (start–stop excl.) |
|----:|-----------------|-------------------------|
| 0–4 | Darks, Lights & whites, Colors, Reds, Delicates | 0–24, 24–48, 48–72, 72–96, 96–120 |
| 5–7 | Dryer, Hang rod, Lay flat | 120–138, 138–156, 156–174 |

## Data and privacy

The catalog is stored locally in `/config/.storage/laundry_hub` (format v3): versioned, written
atomically and debounced. Photos live in `/config/laundry_hub/photos/`. Diagnostics redact names,
owners, notes, tag UIDs, photos, purchase details and notify targets.

**Going back to v0.1.2:** v0.2 converts the catalog to a new format and keeps the old one as
`/config/.storage/laundry_hub.v2.backup`. To roll back: stop Home Assistant, install v0.1.2,
replace `/config/.storage/laundry_hub` with the backup file (rename it), then start Home Assistant.
Changes made under v0.2 are not in the backup.

## Troubleshooting

- **No LED feedback**: select the station's ESPHome device in Laundry Hub → Configure. The
  log says `Feedback action esphome.<name>_feedback not available` if the device is offline.
- **Taps ignored**: check the log for `Ignoring tap`. A bad UID, an unknown `station_id`, or a
  tap from a device other than the configured one is logged and dropped.
- **Light does not flash**: the zone needs a light entity, and the entity must be available.
  Use *Test light* on the Status page.

## Known limitations

- Hardware-dependent behaviour (reader timing, RF range through silicone tags, WLED segment
  power-on behaviour) is untested.
- One flash at a time across all zones (last tap wins), by design.
- The missing sensor refreshes hourly (and on every change).

## Removal

Delete the integration under **Settings → Devices & services → Laundry Hub**, remove it
in HACS, and optionally delete `/config/.storage/laundry_hub` and `/config/laundry_hub/`.

## Releases and versions

`custom_components/laundry_hub/manifest.json` → `version` is the single source of truth.
`const.py`, `pyproject.toml` and the firmware's `firmware_version` must match it (CI checks this).

To release, bump all four in a PR that also adds `docs/releases/v<version>.md` (line 1:
`# v<version>: <short title>`, then plain-language bullets). A PR that changes
`custom_components/` or `esphome/` fails CI unless it raises the version. After merge, the
**Release** workflow runs when **Validate** passes on `main` and publishes the GitHub release
`v<version>` from that exact commit. If that version was already released, it does nothing.
Never tag by hand.

## Development

```bash
pip install -r requirements_test.txt
pytest tests --cov            # 90 % coverage gate
ruff check . && mypy --strict && yamllint .
mutmut run && mutmut export-cicd-stats && python script/mutation_gate.py
make -C esphome/tap-station/tests
```
