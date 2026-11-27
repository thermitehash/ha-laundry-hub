"""Garment catalog persisted with Home Assistant's Store helper."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from .const import (
    CLOSET_STATIONS,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_NEW,
    STATE_PUT_AWAY,
    STATION_LAUNDRY,
    STATIONS,
    STORAGE_KEY,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
    STORED_STATES,
)
from .models import Garment, normalize_uid

_LOGGER = logging.getLogger(__name__)

MAX_UNKNOWN_TAGS = 50


class CatalogError(Exception):
    """Base error for catalog operations."""

    translation_key = "catalog_error"


class DuplicateUid(CatalogError):
    """UID already belongs to a garment."""

    translation_key = "duplicate_uid"


class UnknownGarment(CatalogError):
    """Garment id does not exist."""

    translation_key = "unknown_garment"


class LastTag(CatalogError):
    """Refused to remove the last tag of a garment."""

    translation_key = "last_tag"


def migrate_v1(old: dict[str, Any]) -> dict[str, Any]:
    """Migrate the v1 (pre-release) schema to v2.

    v1 stored a list of garments with a single ``uid`` and flat ``bin``/``dry``
    keys and a flat ``last_seen`` timestamp + ``last_station``.
    """
    garments: dict[str, Any] = {}
    quarantine: list[Any] = list(old.get("quarantine", []))
    for item in old.get("garments", []):
        try:
            gid = item.get("id") or ulid_now()
            last_seen = item.get("last_seen")
            garments[gid] = {
                "id": gid,
                "uids": [item["uid"]],
                "name": item["name"],
                "owner": item.get("owner"),
                "wash_bin": item["bin"],
                "dry_type": item["dry"],
                "notes": item.get("notes", ""),
                "photo": None,
                "state": item.get("state", STATE_NEW),
                "location": item.get("location"),
                "last_seen": (
                    {"time": last_seen, "station": item.get("last_station")}
                    if last_seen
                    else None
                ),
                "last_laundry_tap": None,
                "created": item["created"],
            }
        except (KeyError, TypeError, AttributeError):
            quarantine.append(item)
    return {
        "garments": garments,
        "last_unknown": old.get("last_unknown"),
        "unknown_tags": {},
        "quarantine": quarantine,
    }


def sanitize_garment(garment: Garment) -> list[str]:
    """Repair an inconsistent state/location in place; return what was fixed.

    A garment stuck in an invalid state would otherwise answer every tap with
    an error, so stored data is normalized to the nearest valid state.
    """
    fixes: list[str] = []
    if garment.state not in STORED_STATES:
        fixes.append(f"state {garment.state!r} -> new")
        garment.state, garment.location = STATE_NEW, None
    elif garment.state == STATE_PUT_AWAY and garment.location not in CLOSET_STATIONS:
        fixes.append(f"put_away at {garment.location!r} -> new")
        garment.state, garment.location = STATE_NEW, None
    elif (
        garment.state in (STATE_IN_WASH, STATE_DRYING)
        and garment.location != STATION_LAUNDRY
    ):
        fixes.append(f"{garment.state} location {garment.location!r} -> laundry")
        garment.location = STATION_LAUNDRY
    elif garment.state == STATE_NEW and garment.location is not None:
        fixes.append(f"new with location {garment.location!r} -> none")
        garment.location = None
    if garment.last_seen_station is not None and garment.last_seen_station not in (
        STATIONS
    ):
        fixes.append(f"last_seen station {garment.last_seen_station!r} -> none")
        garment.last_seen_station = None
    return fixes


class _CatalogStore(Store[dict[str, Any]]):
    async def _async_migrate_func(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        data = old_data
        if old_major_version < 2:
            data = migrate_v1(data)
        return data


class Catalog:
    """In-memory garment catalog backed by a debounced Store."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self._store = _CatalogStore(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self.garments: dict[str, Garment] = {}
        self.uid_index: dict[str, str] = {}
        self.last_unknown: dict[str, Any] | None = None
        self.unknown_tags: dict[str, dict[str, Any]] = {}
        self.quarantine: list[Any] = []

    async def async_load(self) -> None:
        """Load and validate stored data; malformed records are quarantined."""
        data = await self._store.async_load() or {}
        self.quarantine = list(data.get("quarantine", []))
        repaired = False
        for gid, raw in (data.get("garments") or {}).items():
            try:
                garment = Garment.from_dict(raw)
            except (KeyError, TypeError, ValueError, AssertionError) as err:
                _LOGGER.error("Quarantined malformed garment %s: %s", gid, err)
                self.quarantine.append(raw)
                continue
            clash = [u for u in garment.uids if u in self.uid_index]
            if clash:
                _LOGGER.error(
                    "Quarantined garment %s: tag(s) %s already used", gid, clash
                )
                self.quarantine.append(raw)
                continue
            if fixes := sanitize_garment(garment):
                _LOGGER.warning("Repaired stored garment %s: %s", gid, "; ".join(fixes))
                repaired = True
            self.garments[garment.id] = garment
            for uid in garment.uids:
                self.uid_index[uid] = garment.id
        if repaired:
            self.schedule_save()
        last_unknown = data.get("last_unknown")
        self.last_unknown = last_unknown if isinstance(last_unknown, dict) else None
        unknown = data.get("unknown_tags")
        self.unknown_tags = unknown if isinstance(unknown, dict) else {}
        # A tag enrolled since it was recorded as unknown is no longer unknown.
        for uid in list(self.unknown_tags):
            if uid in self.uid_index:
                del self.unknown_tags[uid]

    def _data(self) -> dict[str, Any]:
        return {
            "garments": {gid: g.to_dict() for gid, g in self.garments.items()},
            "last_unknown": self.last_unknown,
            "unknown_tags": self.unknown_tags,
            "quarantine": self.quarantine,
        }

    def schedule_save(self) -> None:
        """Schedule a debounced write."""
        self._store.async_delay_save(self._data, STORAGE_SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write immediately (used on unload)."""
        await self._store.async_save(self._data())

    # --- queries -------------------------------------------------------

    def get(self, garment_id: str) -> Garment:
        """Return a garment or raise UnknownGarment."""
        try:
            return self.garments[garment_id]
        except KeyError as err:
            raise UnknownGarment(garment_id) from err

    def by_uid(self, uid: str) -> Garment | None:
        """Return the garment owning a UID."""
        gid = self.uid_index.get(uid)
        return self.garments.get(gid) if gid else None

    def usage(self, zone_id: str) -> int:
        """Number of garments that use a zone (bin or dry type)."""
        return sum(
            1 for g in self.garments.values() if zone_id in (g.wash_bin, g.dry_type)
        )

    # --- mutations -----------------------------------------------------

    def _check_uids_free(self, uids: Iterable[str], garment_id: str | None) -> None:
        for uid in uids:
            owner = self.uid_index.get(uid)
            if owner is not None and owner != garment_id:
                raise DuplicateUid(uid)

    def add(
        self,
        *,
        uid: str,
        name: str,
        wash_bin: str,
        dry_type: str,
        owner: str | None = None,
        notes: str = "",
        location: str | None = None,
        state: str = STATE_NEW,
        now: datetime | None = None,
    ) -> Garment:
        """Add a garment."""
        uid = normalize_uid(uid)
        self._check_uids_free([uid], None)
        garment = Garment(
            id=ulid_now(),
            uids=[uid],
            name=name,
            owner=owner,
            wash_bin=wash_bin,
            dry_type=dry_type,
            notes=notes,
            state=state,
            location=location,
            created=now or dt_util.utcnow(),
        )
        self.garments[garment.id] = garment
        self.uid_index[uid] = garment.id
        self.unknown_tags.pop(uid, None)
        if self.last_unknown and self.last_unknown.get("uid") == uid:
            self.last_unknown = None
        self.schedule_save()
        return garment

    def add_uid(self, garment_id: str, uid: str) -> Garment:
        """Attach another tag to a garment."""
        garment = self.get(garment_id)
        uid = normalize_uid(uid)
        self._check_uids_free([uid], garment_id)
        if uid not in garment.uids:
            garment.uids.append(uid)
            self.uid_index[uid] = garment_id
        self.unknown_tags.pop(uid, None)
        if self.last_unknown and self.last_unknown.get("uid") == uid:
            self.last_unknown = None
        self.schedule_save()
        return garment

    def remove_uid(self, garment_id: str, uid: str) -> Garment:
        """Detach a tag from a garment (never the last one)."""
        garment = self.get(garment_id)
        uid = normalize_uid(uid)
        if uid in garment.uids:
            if len(garment.uids) == 1:
                raise LastTag(uid)
            garment.uids.remove(uid)
            self.uid_index.pop(uid, None)
            self.schedule_save()
        return garment

    def remove(self, garment_id: str) -> Garment:
        """Delete a garment."""
        garment = self.get(garment_id)
        del self.garments[garment_id]
        for uid in garment.uids:
            self.uid_index.pop(uid, None)
        self.schedule_save()
        return garment

    def record_unknown(self, uid: str, station: str, now: datetime) -> None:
        """Remember an unknown tag tap."""
        iso = now.isoformat()
        entry = self.unknown_tags.get(uid)
        if entry is None:
            entry = {"uid": uid, "first_seen": iso, "count": 0}
            self.unknown_tags[uid] = entry
        entry["last_seen"] = iso
        entry["station"] = station
        entry["count"] = int(entry.get("count", 0)) + 1
        if len(self.unknown_tags) > MAX_UNKNOWN_TAGS:
            oldest = min(
                self.unknown_tags, key=lambda k: self.unknown_tags[k]["last_seen"]
            )
            del self.unknown_tags[oldest]
        self.last_unknown = {"uid": uid, "station": station, "time": iso}
        self.schedule_save()

    def dismiss_unknown(self, uid: str) -> None:
        """Forget an unknown tag."""
        self.unknown_tags.pop(uid, None)
        if self.last_unknown and self.last_unknown.get("uid") == uid:
            self.last_unknown = None
        self.schedule_save()
