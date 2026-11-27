"""Websocket API used by the Laundry panel. Every write is validated here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import functools
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import probatio as vol

from .const import (
    CLOSET_STATIONS,
    DOMAIN,
    PHOTO_TYPES,
    PHOTO_URL_TTL_HOURS,
    PHOTOS_URL,
    SIGNAL_TAP,
    SIGNAL_UPDATE,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from .photos import thumb_name
from .services import LOCATION_CHOICES
from .taxonomy import DESCRIPTION_FIELDS, WEAR_FOR

if TYPE_CHECKING:
    from .hub import LaundryHub

type _Msg = dict[str, Any]
type _Handler = Callable[
    [HomeAssistant, websocket_api.ActiveConnection, _Msg, "LaundryHub"], Awaitable[Any]
]

_str = vol.All(str, vol.Length(max=200))
_opt_str = vol.Any(None, _str)


def _with_hub(func: _Handler) -> websocket_api.AsyncWebSocketCommandHandler:
    """Resolve the hub and turn validation errors into websocket errors."""

    @functools.wraps(func)
    async def wrapper(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: _Msg
    ) -> None:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            connection.send_error(msg["id"], "not_loaded", "Laundry Hub is not set up")
            return
        hub: LaundryHub = entries[0].runtime_data
        try:
            result = await func(hass, connection, msg, hub)
        except ServiceValidationError as err:
            connection.send_error(msg["id"], err.translation_key or "invalid", str(err))
            return
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        connection.send_result(msg["id"], result)

    return wrapper


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/data"})
@websocket_api.async_response
@_with_hub
async def ws_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Return the full catalog snapshot (photo URLs signed for this session)."""
    data = hub.snapshot()
    data["persons"] = [
        {"entity_id": s.entity_id, "name": s.name}
        for s in hass.states.async_all("person")
    ]
    ttl = timedelta(hours=PHOTO_URL_TTL_HOURS)

    def sign(name: str) -> str:
        return async_sign_path(
            hass,
            f"{PHOTOS_URL}/{name}",
            ttl,
            refresh_token_id=connection.refresh_token_id,
        )

    for garment in data["garments"]:
        garment["photo_urls"] = [
            {"name": name, "full": sign(name), "thumb": sign(thumb_name(name))}
            for name in garment.get("photos", [])
        ]
    for card in (data.get("purchases") or {}).get("cards", []):
        _sign_card(card, sign)
    return data


def _sign_card(card: dict[str, Any], sign: Callable[[str], str]) -> dict[str, Any]:
    card["photo_urls"] = [
        {"name": name, "full": sign(name), "thumb": sign(thumb_name(name))}
        for name in card.get("photos") or []
    ]
    return card


def _signer(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection
) -> Callable[[str], str]:
    ttl = timedelta(hours=PHOTO_URL_TTL_HOURS)
    return lambda name: async_sign_path(
        hass, f"{PHOTOS_URL}/{name}", ttl, refresh_token_id=connection.refresh_token_id
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/suggest",
        vol.Optional("owner"): _opt_str,
        vol.Optional("wash_bin"): _opt_str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_purchases_suggest(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Unmatched purchase cards, ranked for the Add flow."""
    hub.wardrobe.require()
    sign = _signer(hass, connection)
    cards = hub.purchases.suggest(msg.get("owner"), msg.get("wash_bin"))
    return {"cards": [_sign_card(hub.purchases.view(c), sign) for c in cards]}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/match",
        vol.Required("card_id"): _str,
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_purchases_match(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Fill a garment from a purchase card (copies photos)."""
    garment = hub.resolve_garment(msg["garment_id"])
    await hub.purchases.async_match(msg["card_id"], garment)
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/dismiss",
        vol.Required("card_id"): _str,
        vol.Optional("not_clothing", default=False): bool,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_purchases_dismiss(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Dismiss a card ("Not clothing" also remembers the product for that store)."""
    hub.wardrobe.require()
    card = await hub.purchases.async_dismiss(
        msg["card_id"], not_clothing=msg["not_clothing"]
    )
    return hub.purchases.view(card)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/purchases/check"})
@websocket_api.async_response
@_with_hub
async def ws_purchases_check(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Check purchase emails now."""
    hub.wardrobe.require()
    return await hub.purchases.async_check(manual=True)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: _Msg
) -> None:
    """Push a message whenever the catalog changes."""

    @callback
    def _changed() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"changed": True})
        )

    @callback
    def _tap(event_type: str, attributes: dict[str, Any]) -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"tap": {"event_type": event_type, **attributes}}
            )
        )

    unsubs = [
        async_dispatcher_connect(hass, SIGNAL_UPDATE, _changed),
        async_dispatcher_connect(hass, SIGNAL_TAP, _tap),
    ]

    @callback
    def _unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/create",
        vol.Required("uid"): _str,
        vol.Required("name"): _str,
        vol.Required("wash_bin"): _str,
        vol.Required("dry_type"): _str,
        vol.Optional("owner"): _opt_str,
        vol.Optional("notes"): vol.Any(None, vol.All(str, vol.Length(max=1000))),
        vol.Optional("location"): vol.Any(None, "", vol.In(CLOSET_STATIONS)),
    }
)
@websocket_api.async_response
@_with_hub
async def ws_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Create a garment."""
    garment = hub.enroll(
        uid=msg["uid"],
        name=msg["name"],
        wash_bin=msg["wash_bin"],
        dry_type=msg["dry_type"],
        owner=msg.get("owner"),
        notes=msg.get("notes"),
        location=msg.get("location") or None,
    )
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/update",
        vol.Required("garment_id"): _str,
        vol.Optional("name"): _str,
        vol.Optional("owner"): _opt_str,
        vol.Optional("wash_bin"): _str,
        vol.Optional("dry_type"): _str,
        vol.Optional("notes"): vol.Any(None, vol.All(str, vol.Length(max=1000))),
        vol.Optional("add_uid"): _str,
        vol.Optional("remove_uid"): _str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Update a garment."""
    changes = {k: v for k, v in msg.items() if k not in ("id", "type", "garment_id")}
    return hub.garment_view(hub.update(msg["garment_id"], changes))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/garment/delete", vol.Required("garment_id"): _str}
)
@websocket_api.async_response
@_with_hub
async def ws_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Delete a garment."""
    garment = await hub.async_remove(msg["garment_id"])
    return {"garment_id": garment.id}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/set_location",
        vol.Required("garment_id"): _str,
        vol.Required("location"): vol.In(LOCATION_CHOICES),
    }
)
@websocket_api.async_response
@_with_hub
async def ws_set_location(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Manually set a garment's location."""
    return hub.garment_view(hub.set_location(msg["garment_id"], msg["location"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reassign",
        vol.Required("field"): vol.In([SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE]),
        vol.Required("to"): _str,
        vol.Optional("garment_ids"): [_str],
        vol.Optional("from"): _str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_reassign(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Bulk reassign wash bin / dry type."""
    moved = hub.reassign(
        msg["field"], msg["to"], msg.get("garment_ids"), msg.get("from")
    )
    return {"moved": moved}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/zone/remove", vol.Required("zone_id"): _str}
)
@websocket_api.async_response
@_with_hub
async def ws_zone_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Remove an unused wash bin / dry type."""
    hub.remove_zone(msg["zone_id"])
    return None


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/zone/flash", vol.Required("zone_id"): _str}
)
@websocket_api.async_response
@_with_hub
async def ws_zone_flash(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Flash a zone's light."""
    return {"flashed": await hub.async_flash_zone(msg["zone_id"])}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/unknown/dismiss", vol.Required("uid"): _str}
)
@websocket_api.async_response
@_with_hub
async def ws_dismiss_unknown(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Forget an unknown tag."""
    hub.dismiss_unknown(msg["uid"])
    return None


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/upload",
        vol.Required("garment_id"): _str,
        vol.Required("content_type"): vol.In(list(PHOTO_TYPES)),
        vol.Required("data"): str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_photo_upload(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Store a garment photo (base64)."""
    garment = await hub.async_set_photo(
        msg["garment_id"], msg["content_type"], msg["data"]
    )
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/photo/delete", vol.Required("garment_id"): _str}
)
@websocket_api.async_response
@_with_hub
async def ws_photo_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Delete a garment photo."""
    return hub.garment_view(await hub.async_delete_photo(msg["garment_id"]))


_size = vol.Any(
    None,
    {
        vol.Optional("label"): vol.Any(None, _str),
        vol.Optional("system"): vol.Any(None, _str),
        vol.Optional("cut"): vol.Any(None, _str),
        vol.Optional("brand_size_note"): vol.Any(None, _str),
    },
)
_purchase = vol.Any(
    None,
    {
        vol.Optional("store"): vol.Any(None, _str),
        vol.Optional("purchased_on"): vol.Any(None, _str),
        vol.Optional("price"): vol.Any(None, vol.Coerce(float), ""),
        vol.Optional("currency"): vol.Any(None, _str),
        vol.Optional("product_url"): vol.Any(None, vol.All(str, vol.Length(max=2000))),
        vol.Optional("order_ref"): vol.Any(None, _str),
        vol.Optional("source"): vol.Any(None, _str),
    },
)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/wardrobe",
        vol.Required("garment_id"): _str,
        vol.Optional("brand"): _opt_str,
        vol.Optional("size"): _size,
        vol.Optional("purchase"): _purchase,
        vol.Optional("wear_for"): vol.In(list(WEAR_FOR)),
        vol.Optional("uniform"): bool,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_wardrobe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Update brand, size, purchase details and wear context."""
    garment = hub.resolve_garment(msg["garment_id"])
    changes = {k: v for k, v in msg.items() if k not in ("id", "type", "garment_id")}
    hub.wardrobe.update_details(garment, changes)
    hub.catalog.schedule_save()
    hub.notify_changed()
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/describe_fields",
        vol.Required("garment_id"): _str,
        vol.Required("fields"): {vol.In(list(DESCRIPTION_FIELDS)): object},
    }
)
@websocket_api.async_response
@_with_hub
async def ws_describe_fields(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Set description fields by hand (locks them)."""
    garment = hub.resolve_garment(msg["garment_id"])
    hub.wardrobe.set_description(garment, msg["fields"])
    hub.catalog.schedule_save()
    hub.notify_changed()
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/reset_field",
        vol.Required("garment_id"): _str,
        vol.Required("field"): vol.In(list(DESCRIPTION_FIELDS)),
    }
)
@websocket_api.async_response
@_with_hub
async def ws_reset_field(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Unlock a field and restore the AI value ("Reset to AI")."""
    garment = hub.resolve_garment(msg["garment_id"])
    hub.wardrobe.reset_field(garment, msg["field"])
    hub.catalog.schedule_save()
    hub.notify_changed()
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/describe",
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_describe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Describe a garment with AI Task (re-describe keeps locked fields)."""
    garment = hub.resolve_garment(msg["garment_id"])
    await hub.wardrobe.async_describe(garment)
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/set_field",
        vol.Required("garment_ids"): vol.All([_str], vol.Length(min=1, max=1000)),
        vol.Required("field"): _str,
        vol.Required("value"): object,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_set_field(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Bulk "Set field…" across selected garments."""
    garments = [hub.resolve_garment(ref) for ref in msg["garment_ids"]]
    count = hub.wardrobe.bulk_set(garments, msg["field"], msg["value"])
    hub.catalog.schedule_save()
    hub.notify_changed()
    return {"updated": count}


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/remove",
        vol.Required("garment_id"): _str,
        vol.Required("photo"): _str,
    }
)
@websocket_api.async_response
@_with_hub
async def ws_photo_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Delete one photo of a garment."""
    garment = hub.resolve_garment(msg["garment_id"])
    await hub.wardrobe.async_delete_photo(garment, msg["photo"])
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/reorder",
        vol.Required("garment_id"): _str,
        vol.Required("photos"): [_str],
    }
)
@websocket_api.async_response
@_with_hub
async def ws_photo_reorder(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Reorder photos; the first is the cover."""
    garment = hub.resolve_garment(msg["garment_id"])
    hub.wardrobe.reorder_photos(garment, msg["photos"])
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_product_url",
        vol.Required("url"): vol.All(str, vol.Length(min=1, max=2000)),
    }
)
@websocket_api.async_response
@_with_hub
async def ws_import_url(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Preview what a product page says; nothing is saved until import/apply."""
    preview = await hub.wardrobe.async_import_url(msg["url"])
    ttl = timedelta(minutes=30)
    preview["images"] = [
        async_sign_path(
            hass,
            f"{PHOTOS_URL}/pending/{preview['token']}/{index}",
            ttl,
            refresh_token_id=connection.refresh_token_id,
        )
        for index in range(preview.pop("image_count"))
    ]
    return preview


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import/apply",
        vol.Required("garment_id"): _str,
        vol.Required("token"): _str,
        vol.Optional("fields"): [_str],
        vol.Optional("images"): [vol.Coerce(int)],
    }
)
@websocket_api.async_response
@_with_hub
async def ws_import_apply(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Save the chosen parts of an import preview."""
    garment = hub.resolve_garment(msg["garment_id"])
    await hub.wardrobe.async_apply_import(
        garment, msg["token"], fields=msg.get("fields"), images=msg.get("images")
    )
    return hub.garment_view(garment)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/owner/update",
        vol.Required("owner"): _str,
        vol.Required("work_label"): vol.All(str, vol.Length(min=1, max=30)),
    }
)
@websocket_api.async_response
@_with_hub
async def ws_owner_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Per-owner settings (the label used for "work", e.g. "School")."""
    owner = hub.validate_owner(msg["owner"])
    if owner is None:
        raise hub_validation("invalid_owner", "Choose a person")
    settings = hub.catalog.set_owner_settings(
        owner, work_label=msg["work_label"].strip()
    )
    hub.notify_changed()
    return settings


def hub_validation(key: str, message: str) -> ServiceValidationError:
    """Shortcut for a translatable validation error."""
    from .hub import validation_error  # noqa: PLC0415

    return validation_error(key, message)


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register websocket commands."""
    for handler in (
        ws_data,
        ws_subscribe,
        ws_create,
        ws_update,
        ws_delete,
        ws_set_location,
        ws_reassign,
        ws_zone_remove,
        ws_zone_flash,
        ws_dismiss_unknown,
        ws_photo_upload,
        ws_photo_delete,
        ws_wardrobe,
        ws_describe_fields,
        ws_reset_field,
        ws_describe,
        ws_set_field,
        ws_photo_remove,
        ws_photo_reorder,
        ws_import_url,
        ws_import_apply,
        ws_owner_update,
        ws_purchases_suggest,
        ws_purchases_match,
        ws_purchases_dismiss,
        ws_purchases_check,
    ):
        websocket_api.async_register_command(hass, handler)
