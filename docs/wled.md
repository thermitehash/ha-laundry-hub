# WLED setup for Laundry Hub

> **Status: unverified on hardware.** Indexes and limits below are calculated, not measured.
> Adjust them to your measured strip lengths.

One prebuilt WLED controller drives about 3 m of 5 V SK6812/WS2812B strip (60 LED/m) in
a diffused channel:

- **Sort bar**: 5 wash-bin zones × ~40 cm = 2.0 m = 120 LEDs
- **Dry bar**: 3 dry-type zones × ~30 cm = 0.9 m = 54 LEDs
- 6 spare LEDs at the end (wiring loop / trim)

## Segment table (as shipped)

WLED's segment editor uses **Start LED** (inclusive) and **Stop LED** (exclusive).

| Seg | Zone (default subentry) | Bar  | Start | Stop (excl.) | Last LED | LEDs | Length |
|----:|-------------------------|------|------:|-------------:|---------:|-----:|-------:|
| 0   | Darks                   | Sort |     0 |           24 |       23 |   24 |  40 cm |
| 1   | Lights & whites         | Sort |    24 |           48 |       47 |   24 |  40 cm |
| 2   | Colors                  | Sort |    48 |           72 |       71 |   24 |  40 cm |
| 3   | Reds                    | Sort |    72 |           96 |       95 |   24 |  40 cm |
| 4   | Delicates               | Sort |    96 |          120 |      119 |   24 |  40 cm |
| 5   | Dryer                   | Dry  |   120 |          138 |      137 |   18 |  30 cm |
| 6   | Hang rod                | Dry  |   138 |          156 |      155 |   18 |  30 cm |
| 7   | Lay flat                | Dry  |   156 |          174 |      173 |   18 |  30 cm |
| –   | spare / not a segment   | –    |   174 |          180 |      179 |    6 |  10 cm |

Set **LED count = 180** in WLED (Config → LED Preferences). If the dry bar is a separate
run of strip, keep it on the same data line (daisy-chained) so the indexes stay
contiguous, or use a second WLED output and adjust the starts accordingly.

**Adjust to measured lengths:** count the LEDs under each bin opening, then rebuild the
table so that each segment's Start is the previous segment's Stop. Leave a 1-LED gap
between zones if light bleeds between bins in the diffuser.

## Power

- Full white is about **2 A per metre** at 5 V (60 LED/m), so 3 m is about 6 A if every
  LED is white at full brightness. Laundry Hub lights only one zone at a time, but WLED
  effects or manual control can light everything.
- Supply: 5 V, 6–10 A. In WLED **Config → LED Preferences → Automatic brightness limiter**:
  - 6 A supply: set *Maximum current* to **5000 mA**
  - 10 A supply: set *Maximum current* to **8500 mA**
  - (leaves about 15 % headroom for the controller and supply tolerance)
- Brightness cap: set the master brightness to about **50 % (128)**. A diffused channel
  is bright enough at that level, and it halves worst-case current.
- Inject power at both ends of the 3 m run (≥ 18 AWG), fuse the supply output, and
  share ground between supply, strip and controller.

## Home Assistant

1. Add the WLED device via **Settings → Devices & services → WLED** (core integration).
   With more than one segment, WLED creates one `light` entity per segment, e.g.
   `light.wled_laundry_segment_1`.
2. In WLED, make sure every segment is **off** when idle (turn the segments off, then save
   a preset and set it as the boot preset).
3. Map each segment to its zone in **Settings → Devices & services → Laundry Hub**: open
   the wash bin or dry type entry, choose **Reconfigure**, pick the segment light in
   **Light (WLED segment)** and set the **Color**.
4. Test each zone with the **Test light** button on the panel's Status page, or with the
   `laundry_hub.flash_zone` action (`zone: Darks`).

Laundry Hub calls `light.turn_on` with the zone's `rgb_color` for the flash duration
(default 15 s), then `light.turn_off`. A new tap replaces the current flash (last one wins).
If a light is missing or unavailable, the tap still updates the garment and the problem is logged.
