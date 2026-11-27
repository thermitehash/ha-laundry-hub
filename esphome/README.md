# Laundry Hub tap stations (ESPHome)

> **Status: unverified on hardware.** The firmware passes `esphome config` and
> `esphome compile` in CI and the protocol code has host tests, but no station has been
> flashed yet. See the first-run checklist in `docs/build-report.md`.

Three stations, one shared package:

| File                | `station_id` | Mode button | Notes              |
|---------------------|--------------|-------------|--------------------|
| `laundry-room.yaml` | `laundry`    | yes         | sort / dry station |
| `closet-1.yaml`     | `closet_1`   | ignored     | put away           |
| `closet-2.yaml`     | `closet_2`   | ignored     | put away           |

## Hardware and wiring

- **M5Stack AtomS3 Lite** (ESP32-S3): RGB LED on GPIO35, button on GPIO41.
- **M5Stack Unit NFC U216** (ST25R3916, I2C address **0x50**): plug the Grove cable into
  the AtomS3 Lite Grove port. Grove pinout: **G2 = SDA, G1 = SCL**, 5 V, GND. No other wiring.
- **Fallback: M5Stack Unit RFID2** (WS1850S, I2C 0x28) on the same Grove port.

### Which reader?

| Reader            | `reader:` substitution | ESPHome component                     | Notes |
|-------------------|------------------------|---------------------------------------|-------|
| Unit NFC (U216)   | `st25r_i2c` (default)  | external component `st25r_i2c` (this repo) | ST25R3916; upstream `st25r` logic with an I2C transport |
| Unit RFID2        | `rc522`                | core `rc522_i2c`                      | Proven ESPHome component; use if the U216 misbehaves |

Both read ISO 14443A tags (NTAG213). Garments are identified by **tag UID only**; NDEF is ignored.

## Flashing from the ESPHome Device Builder app

1. In Home Assistant open **ESPHome Device Builder**.
2. Copy this folder's contents into the builder's config folder (`/config/esphome/`):
   `packages/`, `components/`, and the three device files. Use the File editor or Samba add-on.
   Alternatively copy only the device files and `packages/`, and set
   `nfc_component_source: github://thermitehash/ha-laundry-hub/esphome/tap-station/components@main`
   in each device file's `substitutions:`.
3. Open **Secrets** in the Device Builder and add (see `secrets.yaml.example`):
   `wifi_ssid`, `wifi_password`, `fallback_ap_password`, `api_encryption_key`
   (32-byte base64), `ota_password`. Never commit `secrets.yaml`.
4. Pick the reader per device: edit `reader: st25r_i2c` or `reader: rc522`.
5. First flash: connect the AtomS3 Lite over USB-C. If it is not detected, hold the
   reset button about 2 s until the green LED lights (download mode). Then use
   **Install → Plug into this computer** (browser) or **Manual download** + ESPHome Web.
   Later updates go over Wi-Fi (OTA).
6. Home Assistant discovers the device. Add it under **Settings → Devices & services → ESPHome**,
   using the API encryption key.
7. In **Laundry Hub → Configure**, select each ESPHome device for its station so LED
   feedback reaches the right station.

## Behaviour

- **Tap**: the reader reports a UID (uppercase hex, no separators). The same UID is
  debounced for 3 s. The station fires the HA event `esphome.laundry_tap` with
  `{station_id, uid, mode}`.
- **Feedback LED**: Laundry Hub calls the action `esphome.<device>_feedback` with
  `result`: `known` = green, `unknown` = blue, `error` = red, `moved` = white. If Home Assistant does not answer within 2 s the LED gives a short **amber** blink.
- **Mode button** (laundry station only): a single click cycles `auto → sort → dry → auto`.
  Idle LED: auto = off, sort = dim purple, dry = dim teal. Returns to `auto` after 5 min with no
  taps or button presses. Exposed in HA as the select entity **Tap mode**. Closets have no
  mode select; they always send `auto`.
- **Reader errors** (`st25r_i2c`): I2C NACK or timeout aborts the scan and re-initialises the chip
  with exponential backoff (1 s up to `max_backoff`, default 60 s). The component is never
  marked failed, so a missing or stuck reader cannot cause a reboot loop. It recovers
  automatically, and the device shows a warning status while the reader is down.

Home Assistant events from ESPHome need no extra permission. The *Allow the device to
perform Home Assistant actions* option is **not** required.

## Development

- Protocol framing and recovery logic: `tap-station/components/st25r_i2c/st25r_i2c_proto.h`
  (pure C++). Host tests: `make -C tap-station/tests` (g++ `-Wall -Wextra -Werror`, ASan/UBSan).
- Validate: `esphome -s reader rc522 config tap-station/closet-1.yaml`.
