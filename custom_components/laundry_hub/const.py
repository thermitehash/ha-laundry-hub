"""Constants for Laundry Hub."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "laundry_hub"
NAME: Final = "Laundry Hub"
VERSION: Final = "0.1.2"

# Event fired by the tap stations (ESPHome `homeassistant.event`).
TAP_EVENT: Final = "esphome.laundry_tap"

# Station ids (firmware `station_id` substitution).
STATION_LAUNDRY: Final = "laundry"
STATION_CLOSET_1: Final = "closet_1"
STATION_CLOSET_2: Final = "closet_2"
CLOSET_STATIONS: Final = (STATION_CLOSET_1, STATION_CLOSET_2)
STATIONS: Final = (STATION_LAUNDRY, *CLOSET_STATIONS)

# Tap modes sent by the firmware.
MODE_AUTO: Final = "auto"
MODE_SORT: Final = "sort"
MODE_DRY: Final = "dry"
MODES: Final = (MODE_AUTO, MODE_SORT, MODE_DRY)

# Config entry keys.
CONF_STATION_DEVICES: Final = "station_devices"
CONF_FEEDBACK_TIMEOUT: Final = "feedback_timeout"
CONF_FLASH_DURATION: Final = "flash_duration"
CONF_MISSING_AFTER: Final = "missing_after_days"
CONF_NOTIFY_TARGET: Final = "notify_target"

DEFAULT_FEEDBACK_TIMEOUT: Final = 2.0
DEFAULT_FLASH_DURATION: Final = 15
DEFAULT_MISSING_AFTER: Final = 14

# Subentry types and keys.
SUBENTRY_WASH_BIN: Final = "wash_bin"
SUBENTRY_DRY_TYPE: Final = "dry_type"
ZONE_TYPES: Final = (SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE)
CONF_NAME: Final = "name"
CONF_COLOR: Final = "color"
CONF_LIGHT_ENTITY: Final = "light_entity"
CONF_ORDER: Final = "order"
CONF_DELETE: Final = "delete"

SEED_WASH_BINS: Final = (
    ("Darks", (40, 40, 160)),
    ("Lights & whites", (255, 255, 255)),
    ("Colors", (255, 160, 0)),
    ("Reds", (255, 0, 0)),
    ("Delicates", (255, 105, 180)),
)
SEED_DRY_TYPES: Final = (
    ("Dryer", (255, 120, 0)),
    ("Hang rod", (0, 200, 255)),
    ("Lay flat", (120, 255, 120)),
)

# Garment states.
STATE_NEW: Final = "new"
STATE_PUT_AWAY: Final = "put_away"
STATE_IN_WASH: Final = "in_wash"
STATE_DRYING: Final = "drying"
STATE_MISSING: Final = "missing"  # derived, never stored
STORED_STATES: Final = (STATE_NEW, STATE_PUT_AWAY, STATE_IN_WASH, STATE_DRYING)

# Tap results / LED feedback.
FEEDBACK_KNOWN: Final = "known"
FEEDBACK_UNKNOWN: Final = "unknown"
FEEDBACK_ERROR: Final = "error"
FEEDBACK_MOVED: Final = "moved"

# Event entity types.
EVENT_IN_WASH: Final = "in_wash"
EVENT_DRYING: Final = "drying"
EVENT_PUT_AWAY: Final = "put_away"
EVENT_UNKNOWN: Final = "unknown"
EVENT_TYPES: Final = [EVENT_IN_WASH, EVENT_DRYING, EVENT_PUT_AWAY, EVENT_UNKNOWN]

REPEAT_WINDOW: Final = timedelta(minutes=5)

# Storage.
STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 2
STORAGE_SAVE_DELAY: Final = 5  # seconds (debounced writes)

PHOTO_DIR: Final = "laundry_hub/photos"
PHOTO_MAX_BYTES: Final = 2 * 1024 * 1024
PHOTO_TYPES: Final = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# Panel.
PANEL_URL: Final = "laundry"
PANEL_TITLE: Final = "Laundry"
PANEL_ICON: Final = "mdi:washing-machine"
PANEL_COMPONENT: Final = "laundry-hub-panel"
PANEL_STATIC_URL: Final = "/laundry_hub_static"
PHOTO_URL: Final = "/api/laundry_hub/photo"

# Dispatcher signal: catalog changed.
SIGNAL_UPDATE: Final = f"{DOMAIN}_update"

# Limits / validation.
MAX_NAME_LEN: Final = 100
MAX_NOTES_LEN: Final = 1000
UID_MIN_HEX: Final = 8  # 4-byte UID
UID_MAX_HEX: Final = 20  # 10-byte UID
