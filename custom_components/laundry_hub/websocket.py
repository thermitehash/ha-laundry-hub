"""Websocket API used by the Laundry panel. Every write is validated here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import functools
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import probatio as vol

from .const import (
    CLOSET_STATIONS,
    DOMAIN,
    PHOTO_TYPES,
    SIGNAL_UPDATE,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from .services import LOCATION_CHOICES

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
    """Return the full catalog snapshot."""
    data = hub.snapshot()
    data["persons"] = [
        {"entity_id": s.entity_id, "name": s.name}
        for s in hass.states.async_all("person")
    ]
    return data


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

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, SIGNAL_UPDATE, _changed
    )
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
    ):
        websocket_api.async_register_command(hass, handler)
