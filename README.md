# Laundry Hub

Home Assistant integration (HACS) plus ESPHome tap-station firmware for NFC garment
tracking and laundry sorting lights. Tap a tagged garment on a station and Laundry Hub
works out where it goes next. The matching wash bin or dry zone lights up on a WLED strip,
and the station's LED confirms the tap.

> **Status: v0.1.0, unverified on hardware.** Everything is built and tested against fakes
> (see [`docs/build-report.md`](docs/build-report.md)). Stations, readers, tags and WLED
> have not been tested on real hardware yet.

## How it works

| Tap at…            | Garment was…                         | Result                                   | Light            |
|--------------------|--------------------------------------|------------------------------------------|------------------|
| Laundry (auto)     | not in the wash                      | → **in wash**                            | its wash bin     |
| Laundry (auto)     | in wash, last laundry tap ≥ 5 min ago | → **drying**                            | its dry type     |
| Laundry (auto)     | tapped at laundry < 5 min ago        | no change (repeat)                       | same zone again  |
| Laundry (sort)     | anything                             | → **in wash**                            | its wash bin     |
| Laundry (dry)      | anything                             | → **drying**                             | its dry type     |
| Closet 1 / 2       | anything                             | → **put away** in that closet            | –                |
| Any                | unknown tag                          | recorded, enrollment notification sent   | –                |

Station LED: green = known, blue = unknown, red = error, white = moved to a closet,
amber = Home Assistant did not answer. Garments not seen for *missing after* days
(default 14) are listed as **missing**. They are never deleted automatically.

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

The **Laundry** sidebar panel is admin-only:

- **Garments**: search and filter by owner, bin or location; add, edit (name, owner, bin, dry
  type, notes, tags, photo) and delete garments; bulk reassign bin or dry type.
- **Enroll**: shows the last unknown tag with a create-garment form prefilled with its UID.
  The enrollment notification links here.
- **Status**: what is in each closet, in the wash per bin, drying, and missing; test each zone light.

![Garments page](docs/screenshots/garments.png)
![Enroll page](docs/screenshots/enroll.png)
![Status page](docs/screenshots/status.png)

*(Screenshot placeholders, to be added once running on real hardware.)*

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

The catalog is stored locally in `/config/.storage/laundry_hub`: versioned, written
atomically and debounced. Photos live in `/config/laundry_hub/photos/` (JPEG, PNG or WebP,
up to 2 MB). Diagnostics redact names, owners, notes, tag UIDs and notify targets.

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

## Development

```bash
pip install -r requirements_test.txt
pytest tests --cov            # 90 % coverage gate
ruff check . && mypy --strict && yamllint .
mutmut run && mutmut export-cicd-stats && python scripts/mutation_gate.py
make -C esphome/tap-station/tests
```
