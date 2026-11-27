"""Data models for Laundry Hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any

from .const import (
    CLOSET_STATIONS,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_NEW,
    STATE_PUT_AWAY,
    STATION_LAUNDRY,
    STATIONS,
    STORED_STATES,
)

_SEPARATORS = re.compile(r"[\s:\-]")
_HEX = re.compile(r"^[0-9A-F]+$")
VALID_UID_BYTES = (4, 7, 10)  # ISO 14443A single/double/triple size


class InvalidUid(ValueError):
    """Raised for a malformed tag UID."""


def normalize_uid(raw: object) -> str:
    """Normalize a tag UID: uppercase hex, no separators, 4/7/10 bytes."""
    if not isinstance(raw, str):
        raise InvalidUid("UID must be a string")
    uid = _SEPARATORS.sub("", raw).upper()
    if not uid or not _HEX.match(uid):
        raise InvalidUid(f"UID {raw!r} is not hexadecimal")
    if len(uid) % 2 or len(uid) // 2 not in VALID_UID_BYTES:
        raise InvalidUid(f"UID {raw!r} must be 4, 7 or 10 bytes")
    return uid


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"naive timestamp {value!r}")
    return parsed


@dataclass(frozen=True, slots=True)
class Zone:
    """A wash bin or dry type (config subentry)."""

    id: str
    type: str
    name: str
    color: tuple[int, int, int]
    light_entity: str | None
    order: int


@dataclass(slots=True)
class Garment:
    """A garment in the catalog."""

    id: str
    uids: list[str]
    name: str
    wash_bin: str
    dry_type: str
    created: datetime
    owner: str | None = None
    notes: str = ""
    photo: str | None = None
    state: str = STATE_NEW
    location: str | None = None
    last_seen: datetime | None = None
    last_seen_station: str | None = None
    last_laundry_tap: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            "id": self.id,
            "uids": list(self.uids),
            "name": self.name,
            "owner": self.owner,
            "wash_bin": self.wash_bin,
            "dry_type": self.dry_type,
            "notes": self.notes,
            "photo": self.photo,
            "state": self.state,
            "location": self.location,
            "last_seen": (
                {"time": self.last_seen.isoformat(), "station": self.last_seen_station}
                if self.last_seen is not None
                else None
            ),
            "last_laundry_tap": (
                self.last_laundry_tap.isoformat() if self.last_laundry_tap else None
            ),
            "created": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Garment:
        """Deserialize from storage (raises on malformed data)."""
        last_seen = data.get("last_seen") or None
        created = _dt(data["created"])
        assert created is not None
        return cls(
            id=str(data["id"]),
            uids=[normalize_uid(u) for u in data["uids"]],
            name=str(data["name"]),
            owner=data.get("owner"),
            wash_bin=str(data["wash_bin"]),
            dry_type=str(data["dry_type"]),
            notes=str(data.get("notes") or ""),
            photo=data.get("photo"),
            state=str(data.get("state") or STATE_NEW),
            location=data.get("location"),
            last_seen=_dt(last_seen["time"]) if last_seen else None,
            last_seen_station=last_seen.get("station") if last_seen else None,
            last_laundry_tap=_dt(data.get("last_laundry_tap")),
            created=created,
        )


def garment_problems(garment: Garment, zone_ids: set[str]) -> list[str]:
    """Return invariant violations for a garment (empty list = valid)."""
    problems: list[str] = []
    if garment.state not in STORED_STATES:
        problems.append(f"invalid state {garment.state}")
    if garment.wash_bin not in zone_ids:
        problems.append(f"wash_bin {garment.wash_bin} does not exist")
    if garment.dry_type not in zone_ids:
        problems.append(f"dry_type {garment.dry_type} does not exist")
    if not garment.uids:
        problems.append("garment has no tag")
    if garment.state == STATE_PUT_AWAY and garment.location not in CLOSET_STATIONS:
        problems.append(f"put_away with location {garment.location}")
    if (
        garment.state in (STATE_IN_WASH, STATE_DRYING)
        and garment.location != STATION_LAUNDRY
    ):
        problems.append(f"{garment.state} with location {garment.location}")
    if garment.state == STATE_NEW and garment.location is not None:
        problems.append("new garment with a location")
    if garment.last_seen_station is not None and garment.last_seen_station not in (
        STATIONS
    ):
        problems.append(f"unknown last_seen station {garment.last_seen_station}")
    return problems
