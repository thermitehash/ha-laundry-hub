"""Constants for Laundry Hub."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "laundry_hub"
NAME: Final = "Laundry Hub"
VERSION: Final = "0.2.4"

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
CONF_WARDROBE_ENABLED: Final = "wardrobe_enabled"
CONF_AI_TASK_ENTITY: Final = "ai_task_entity"
CONF_FOLLOW_HA_THEME: Final = "follow_ha_theme"

# Purchase emails (v0.2.1). Credentials live in entry.data, settings in options.
# App access (v0.2.3).
CONF_APP_ACCESS: Final = "app_access"  # "admins" (default) or "household"
CONF_USER_OWNERS: Final = "user_owners"  # {ha_user_id: person entity id or ""}
API_VERSION: Final = 1  # adding fields never bumps it; removing/renaming does

CONF_PURCHASE_EMAIL: Final = "purchase_email"
CONF_PURCHASE_STORES: Final = "purchase_stores"
CONF_PURCHASE_CUSTOM: Final = "purchase_custom_domains"
CONF_PURCHASE_SHOPIFY: Final = "purchase_shopify_names"
CONF_PURCHASE_INTERVAL: Final = "purchase_interval"
CONF_PURCHASE_BACKFILL: Final = "purchase_backfill"
CONF_PURCHASE_CLOTHING_ONLY: Final = "purchase_clothing_only"
CONF_PURCHASE_ARCHIVE_DAYS: Final = "purchase_archive_days"
DEFAULT_PURCHASE_INTERVAL: Final = 30  # minutes
MIN_PURCHASE_INTERVAL: Final = 15
DEFAULT_PURCHASE_BACKFILL: Final = 90  # days
PURCHASE_BACKFILL_CHOICES: Final = (0, 30, 90, 365)
DEFAULT_PURCHASE_ARCHIVE_DAYS: Final = 60
PURCHASE_COUNT_DAYS: Final = 90  # store list counts in the options flow
ISSUE_PURCHASE_AUTH: Final = "purchase_email_auth"

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
STORAGE_VERSION: Final = 3
STORAGE_MINOR_VERSION: Final = 3  # 3.2 purchases, 3.3 revs (lossless, minor bumps)
STORAGE_SAVE_DELAY: Final = 5  # seconds (debounced writes)

PHOTO_DIR: Final = "laundry_hub/photos"
PHOTO_MAX_BYTES: Final = 2 * 1024 * 1024  # legacy base64 websocket upload (v0.1)
PHOTO_UPLOAD_MAX_BYTES: Final = 10 * 1024 * 1024  # per upload, before resize
PHOTO_MAX_PER_GARMENT: Final = 8
PHOTO_LONG_EDGE: Final = 1600
PHOTO_THUMB_EDGE: Final = 400
PHOTO_TYPES: Final = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PHOTOS_URL: Final = "/api/laundry_hub/photos"
PHOTO_URL_TTL_HOURS: Final = 24

# Product URL import.
IMPORT_TIMEOUT: Final = 10
IMPORT_MAX_HTML: Final = 3 * 1024 * 1024
IMPORT_MAX_IMAGES: Final = 4
IMPORT_MAX_REDIRECTS: Final = 5
IMPORT_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# AI description.
DESCRIBE_MAX_ATTACHMENTS: Final = 3

# Panel.
PANEL_URL: Final = "laundry"
PANEL_TITLE: Final = "Laundry"
PANEL_ICON: Final = "mdi:washing-machine"
PANEL_COMPONENT: Final = "laundry-hub-panel"
PANEL_STATIC_URL: Final = "/laundry_hub_static"
PHOTO_URL: Final = "/api/laundry_hub/photo"

# Dispatcher signal: catalog changed.
SIGNAL_UPDATE: Final = f"{DOMAIN}_update"
SIGNAL_TAP: Final = f"{DOMAIN}_tap"

# Limits / validation.
MAX_NAME_LEN: Final = 100
MAX_NOTES_LEN: Final = 1000
UID_MIN_HEX: Final = 8  # 4-byte UID
UID_MAX_HEX: Final = 20  # 10-byte UID
