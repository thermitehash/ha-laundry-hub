"""Change tracking for offline clients (v0.2.3).

Every garment, zone, unknown tag and purchase card carries ``rev`` (from one
store-wide, monotonically increasing counter) and ``updated_at``. Instead of
instrumenting every mutation, the tracker fingerprints each record after any
change (``sync()`` runs before every "changed" signal): a record whose
fingerprint differs gets the next rev; a record that disappeared leaves a
tombstone ``{id, kind, rev, deleted: true}`` for ``TOMBSTONE_DAYS``.

``changes(since_rev)`` returns every record and tombstone with ``rev >
since_rev``. A ``since_rev`` older than the oldest tombstone ever purged (or
newer than the counter, e.g. after a restore) answers ``reset``: the client
re-reads everything. Persisted in the catalog store (schema 3.3).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
import json
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .hub import LaundryHub

TOMBSTONE_DAYS = 30
KINDS = ("garment", "zone", "unknown_tag", "purchase")


def _fingerprint(record: Any) -> str:
    raw = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


class RevTracker:
    """Assigns revs and keeps tombstones for one hub."""

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        self.hub = hub

    # --- persisted state (lives in catalog.revs) ----------------------------

    @property
    def state(self) -> dict[str, Any]:
        """{'rev': int, 'floor': int, 'records': {...}, 'tombstones': {...}}."""
        state = self.hub.catalog.revs
        state.setdefault("rev", 0)
        state.setdefault("floor", 0)
        state.setdefault("records", {})
        state.setdefault("tombstones", {})
        return state

    @property
    def rev(self) -> int:
        """Current store-wide rev."""
        return int(self.state["rev"])

    def meta(self, kind: str, record_id: str) -> dict[str, Any] | None:
        """{'rev', 'updated_at'} of a live record, or None."""
        entry = self.state["records"].get(f"{kind}:{record_id}")
        if entry is None:
            return None
        return {"rev": entry["rev"], "updated_at": entry["updated_at"]}

    def stamp(self, kind: str, record_id: str, view: dict[str, Any]) -> dict[str, Any]:
        """Add rev and updated_at to a serialized record (in place)."""
        meta = self.meta(kind, record_id) or {"rev": 0, "updated_at": None}
        view["rev"] = meta["rev"]
        view["updated_at"] = meta["updated_at"]
        return view

    # --- records ---------------------------------------------------------------

    def _sources(self) -> dict[str, tuple[Callable[[], dict[str, Any]], Any]]:
        hub = self.hub
        return {
            "garment": (
                lambda: {g.id: g for g in hub.catalog.garments.values()},
                lambda g: {**g.to_dict(), "missing": hub.is_missing(g)},
            ),
            "zone": (
                lambda: dict(hub.zones),
                lambda z: {
                    k: v
                    for k, v in hub.zone_view(z).items()
                    if k not in ("usage", "rev", "updated_at")
                },
            ),
            "unknown_tag": (
                lambda: dict(hub.catalog.unknown_tags),
                lambda t: t,
            ),
            "purchase": (
                lambda: dict(hub.catalog.purchases),
                lambda c: c,
            ),
        }

    def sync(self, now: datetime | None = None) -> bool:
        """Bump revs for changed records, tombstone removed ones. True if any."""
        now = now or dt_util.utcnow()
        iso = now.isoformat()
        state = self.state
        records: dict[str, Any] = state["records"]
        tombstones: dict[str, Any] = state["tombstones"]
        changed = False
        seen: set[str] = set()
        for kind, (items, serialize) in self._sources().items():
            for record_id, item in items().items():
                key = f"{kind}:{record_id}"
                seen.add(key)
                fingerprint = _fingerprint(serialize(item))
                entry = records.get(key)
                if entry is not None and entry["hash"] == fingerprint:
                    continue
                state["rev"] += 1
                records[key] = {
                    "rev": state["rev"],
                    "updated_at": iso,
                    "hash": fingerprint,
                }
                tombstones.pop(key, None)  # re-created (e.g. a tag enrolled again)
                changed = True
        for key in [k for k in records if k not in seen]:
            kind, _, record_id = key.partition(":")
            state["rev"] += 1
            tombstones[key] = {
                "id": record_id,
                "kind": kind,
                "rev": state["rev"],
                "deleted": True,
                "deleted_at": iso,
            }
            del records[key]
            changed = True
        changed |= self._purge(now)
        if changed:
            self.hub.catalog.schedule_save()
        return changed

    def _purge(self, now: datetime) -> bool:
        """Drop tombstones older than the window; raise the reset floor."""
        cutoff = now - timedelta(days=TOMBSTONE_DAYS)
        tombstones: dict[str, Any] = self.state["tombstones"]
        purged = False
        for key, stone in list(tombstones.items()):
            deleted = dt_util.parse_datetime(str(stone.get("deleted_at") or ""))
            if deleted is None or deleted < cutoff:
                self.state["floor"] = max(int(self.state["floor"]), int(stone["rev"]))
                del tombstones[key]
                purged = True
        return purged

    # --- queries ---------------------------------------------------------------

    def needs_reset(self, since_rev: int) -> bool:
        """True when the client must re-read everything."""
        return since_rev < int(self.state["floor"]) or since_rev > self.rev

    def changed_ids(self, since_rev: int) -> dict[str, list[str]]:
        """Live record ids per kind with rev > since_rev."""
        out: dict[str, list[str]] = {kind: [] for kind in KINDS}
        for key, entry in self.state["records"].items():
            if entry["rev"] > since_rev:
                kind, _, record_id = key.partition(":")
                out.setdefault(kind, []).append(record_id)
        return out

    def tombstones_since(self, since_rev: int) -> list[dict[str, Any]]:
        """Tombstones with rev > since_rev, oldest first (no internal fields)."""
        stones = [
            {k: v for k, v in stone.items() if k != "deleted_at"}
            for stone in self.state["tombstones"].values()
            if stone["rev"] > since_rev
        ]
        return sorted(stones, key=lambda s: s["rev"])
