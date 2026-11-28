"""Diagnostics for Laundry Hub (personal data redacted)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_NOTIFY_TARGET,
    CONF_PURCHASE_CUSTOM,
    CONF_PURCHASE_EMAIL,
    CONF_PURCHASE_SHOPIFY,
    CONF_STATION_DEVICES,
)

if TYPE_CHECKING:
    from . import LaundryHubConfigEntry

TO_REDACT_OPTIONS = {
    CONF_NOTIFY_TARGET,
    CONF_STATION_DEVICES,
    CONF_PURCHASE_CUSTOM,
    CONF_PURCHASE_EMAIL,
    CONF_PURCHASE_SHOPIFY,
}
TO_REDACT_GARMENT = {
    "name", "owner", "notes", "photo", "photos", "uids", "purchase", "brand_size_note",
}  # fmt: skip


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
            "photo_count": len(g.photos),
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
            {
                **{k: v for k, v in t.items() if k != "tapped_by"},
                "uid": _mask_uid(str(t.get("uid", ""))),
            }
            for t in hub.catalog.unknown_tags.values()
        ],
        "quarantined_records": len(hub.catalog.quarantine),
        "invariant_problems": hub.problems(),
        "current_flash": hub.flash.current,
        "wardrobe_enabled": hub.wardrobe.enabled,
        "ai_entity_configured": bool(hub.wardrobe.ai_entity),
        "station_health": hub.station_health(),
        "purchases": _purchases(hub),
    }


def _purchases(hub: Any) -> dict[str, Any]:
    """Store list, counts and the last error class: no addresses, subjects or bodies."""
    inbox = hub.purchases
    account = inbox.account
    counts: dict[str, int] = {}
    for card in inbox.cards.values():
        status = str(card.get("status"))
        counts[status] = counts.get(status, 0) + 1
    meta = inbox.meta
    return {
        "connected": account is not None,
        "provider": account.provider if account else None,
        "active": inbox.active,
        "stores": sorted(inbox.opted.store_keys),
        "custom_domain_count": len(inbox.opted.custom_domains),
        "shopify_name_count": len(inbox.opted.shopify_names),
        "cards": counts,
        "folders_tracked": len(meta.get("folders") or {}),
        "seen_messages": len(meta.get("seen_ids") or []),
        "not_clothing_rules": sum(
            len(v) for v in (meta.get("not_clothing") or {}).values()
        ),
        "unreadable_emails": len(meta.get("unreadable") or {}),
        "unreadable_senders": sorted(
            {
                str(e.get("sender_domain"))
                for e in (meta.get("unreadable") or {}).values()
            }
        ),
        "pending_failures": len(meta.get("failures") or {}),
        # v0.2.6: counts only (no names, owners or order numbers).
        "cleanup": {
            k: (meta.get("cleanup") or {}).get(k)
            for k in ("version", "removed", "grouped", "split", "classified")
        },
        "grouped_cards": sum(
            1 for c in inbox.cards.values() if len(c.get("history") or []) > 1
        ),
        "assigned_cards": sum(1 for c in inbox.cards.values() if c.get("owner")),
        "learned_shop_ids": len(meta.get("shop_ids") or {}),
        "last_check": meta.get("last_check"),
        "last_error": meta.get("last_error"),
    }
