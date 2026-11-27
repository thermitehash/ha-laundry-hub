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


# Keys written by to_dict(); anything else found in storage is kept in `extra`
# so a round trip never loses data written by a newer or older version.
_KNOWN_KEYS = frozenset(
    {
        "id", "uids", "name", "owner", "wash_bin", "dry_type", "notes", "photo",
        "photos", "state", "location", "last_seen", "last_laundry_tap", "created",
        "brand", "size", "purchase", "description", "description_provenance",
        "description_confidence", "description_ai", "description_model",
        "description_updated", "wear_for", "uniform", "recent_taps", "lifecycle",
        "lifecycle_changed", "sale_price", "wear_log", "out_since", "fit_feedback",
        "fit_score", "skip_count", "last_suggested",
    }
)  # fmt: skip
MAX_RECENT_TAPS = 10


@dataclass(slots=True)
class Garment:
    """A garment in the catalog (storage schema v3)."""

    id: str
    uids: list[str]
    name: str
    wash_bin: str
    dry_type: str
    created: datetime
    owner: str | None = None
    notes: str = ""
    photos: list[str] = field(default_factory=list)  # first = cover
    state: str = STATE_NEW
    location: str | None = None
    last_seen: datetime | None = None
    last_seen_station: str | None = None
    last_laundry_tap: datetime | None = None
    # Wardrobe (v0.2, optional)
    brand: str | None = None
    size: dict[str, Any] | None = None  # {label, system, cut, brand_size_note}
    purchase: dict[str, Any] | None = None  # see wardrobe.PURCHASE_KEYS
    description: dict[str, Any] | None = None  # taxonomy.DESCRIPTION_FIELDS
    description_provenance: dict[str, str] = field(default_factory=dict)
    description_confidence: dict[str, float] = field(default_factory=dict)
    description_ai: dict[str, Any] = field(default_factory=dict)  # last AI values
    description_model: str | None = None
    description_updated: datetime | None = None
    wear_for: str = "any"
    uniform: bool = False
    recent_taps: list[dict[str, Any]] = field(default_factory=list)
    lifecycle: str = "active"
    lifecycle_changed: datetime | None = None
    sale_price: float | None = None
    # Reserved for v0.3 (stored, never used or shown in v0.2)
    wear_log: list[Any] = field(default_factory=list)
    out_since: datetime | None = None
    fit_feedback: list[Any] = field(default_factory=list)
    fit_score: float | None = None
    skip_count: int = 0
    last_suggested: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def photo(self) -> str | None:
        """Cover photo filename (v0.1 compatibility)."""
        return self.photos[0] if self.photos else None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {
            **self.extra,
            "id": self.id,
            "uids": list(self.uids),
            "name": self.name,
            "owner": self.owner,
            "wash_bin": self.wash_bin,
            "dry_type": self.dry_type,
            "notes": self.notes,
            "photos": list(self.photos),
            "state": self.state,
            "location": self.location,
            "last_seen": (
                {"time": self.last_seen.isoformat(), "station": self.last_seen_station}
                if self.last_seen is not None
                else None
            ),
            "last_laundry_tap": _iso(self.last_laundry_tap),
            "created": self.created.isoformat(),
            "brand": self.brand,
            "size": dict(self.size) if self.size else None,
            "purchase": dict(self.purchase) if self.purchase else None,
            "description": dict(self.description) if self.description else None,
            "description_provenance": dict(self.description_provenance),
            "description_confidence": dict(self.description_confidence),
            "description_ai": dict(self.description_ai),
            "description_model": self.description_model,
            "description_updated": _iso(self.description_updated),
            "wear_for": self.wear_for,
            "uniform": self.uniform,
            "recent_taps": [dict(t) for t in self.recent_taps],
            "lifecycle": self.lifecycle,
            "lifecycle_changed": _iso(self.lifecycle_changed),
            "sale_price": self.sale_price,
            "wear_log": list(self.wear_log),
            "out_since": _iso(self.out_since),
            "fit_feedback": list(self.fit_feedback),
            "fit_score": self.fit_score,
            "skip_count": self.skip_count,
            "last_suggested": _iso(self.last_suggested),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Garment:
        """Deserialize from storage (raises on malformed data)."""
        last_seen = data.get("last_seen") or None
        created = _dt(data["created"])
        assert created is not None
        photos = data.get("photos")
        if photos is None:  # v2 data that skipped migration
            photos = [data["photo"]] if data.get("photo") else []
        return cls(
            id=str(data["id"]),
            uids=[normalize_uid(u) for u in data["uids"]],
            name=str(data["name"]),
            owner=data.get("owner"),
            wash_bin=str(data["wash_bin"]),
            dry_type=str(data["dry_type"]),
            notes=str(data.get("notes") or ""),
            photos=[str(p) for p in photos],
            state=str(data.get("state") or STATE_NEW),
            location=data.get("location"),
            last_seen=_dt(last_seen["time"]) if last_seen else None,
            last_seen_station=last_seen.get("station") if last_seen else None,
            last_laundry_tap=_dt(data.get("last_laundry_tap")),
            created=created,
            brand=data.get("brand"),
            size=data.get("size") or None,
            purchase=data.get("purchase") or None,
            description=data.get("description") or None,
            description_provenance=dict(data.get("description_provenance") or {}),
            description_confidence=dict(data.get("description_confidence") or {}),
            description_ai=dict(data.get("description_ai") or {}),
            description_model=data.get("description_model"),
            description_updated=_dt(data.get("description_updated")),
            wear_for=str(data.get("wear_for") or "any"),
            uniform=bool(data.get("uniform", False)),
            recent_taps=list(data.get("recent_taps") or [])[-MAX_RECENT_TAPS:],
            lifecycle=str(data.get("lifecycle") or "active"),
            lifecycle_changed=_dt(data.get("lifecycle_changed")),
            sale_price=data.get("sale_price"),
            wear_log=list(data.get("wear_log") or []),
            out_since=_dt(data.get("out_since")),
            fit_feedback=list(data.get("fit_feedback") or []),
            fit_score=data.get("fit_score"),
            skip_count=int(data.get("skip_count") or 0),
            last_suggested=_dt(data.get("last_suggested")),
            extra={k: v for k, v in data.items() if k not in _KNOWN_KEYS},
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


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
