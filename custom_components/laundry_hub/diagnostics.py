"""Diagnostics for Laundry Hub (personal data redacted)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY_TARGET, CONF_STATION_DEVICES

if TYPE_CHECKING:
    from . import LaundryHubConfigEntry

TO_REDACT_OPTIONS = {CONF_NOTIFY_TARGET, CONF_STATION_DEVICES}
TO_REDACT_GARMENT = {"name", "owner", "notes", "photo", "uids"}


def _mask_uid(uid: str) -> str:
    return f"{uid[:2]}…{uid[-2:]}" if len(uid) > 4 else "**REDACTED**"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LaundryHubConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    hub = entry.runtime_data
    garments = [
        {
            **async_redact_data(g.to_dict(), TO_REDACT_GARMENT),
            "tag_count": len(g.uids),
            "missing": hub.is_missing(g),
        }
        for g in hub.catalog.garments.values()
    ]
    return {
        "options": async_redact_data(dict(entry.options), TO_REDACT_OPTIONS),
        "stations_configured": sorted(
            (entry.options.get(CONF_STATION_DEVICES) or {}).keys()
        ),
        "feedback_targets": {
            station: hub.feedback_service(station) is not None
            for station in ("laundry", "closet_1", "closet_2")
        },
        "zones": [hub.zone_view(z) for z in hub.zones.values()],
        "garment_count": len(garments),
        "garments": garments,
        "unknown_tags": [
            {**t, "uid": _mask_uid(str(t.get("uid", "")))}
            for t in hub.catalog.unknown_tags.values()
        ],
        "quarantined_records": len(hub.catalog.quarantine),
        "invariant_problems": hub.problems(),
        "current_flash": hub.flash.current,
    }
