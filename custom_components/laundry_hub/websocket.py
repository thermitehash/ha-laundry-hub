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

from .access import (
    ACCESS_HOUSEHOLD,
    Caller,
    Forbidden,
    Policy,
    app_access,
    caller_for,
    can_see_garment,
    check,
    member_unknown_tags,
)
from .const import (
    API_VERSION,
    CLOSET_STATIONS,
    DOMAIN,
    PHOTO_TYPES,
    PHOTO_URL_TTL_HOURS,
    PHOTOS_URL,
    SIGNAL_TAP,
    SIGNAL_UPDATE,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
    VERSION,
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


_rev = vol.All(vol.Coerce(int), vol.Range(min=0))
_revs = {_str: _rev}
_CALLER = "_laundry_caller"
# Which record an `if_rev` refers to, by the message key that names it.
_TARGETS = (
    ("garment_id", "garment"),
    ("card_id", "purchase"),
    ("zone_id", "zone"),
    ("uid", "unknown_tag"),
)


def caller_of(msg: _Msg) -> Caller:
    """The Caller the wrapper attached to this message."""
    caller: Caller = msg[_CALLER]
    return caller


class Conflict(HomeAssistantError):
    """An ``if_rev`` didn't match: nothing was written."""

    def __init__(self, kind: str, record_id: str, current: dict[str, Any] | None):
        """Initialize."""
        super().__init__(f"The {kind} changed since revision you sent")
        self.kind = kind
        self.record_id = record_id
        self.current = current


def _current(hub: LaundryHub, kind: str, record_id: str) -> dict[str, Any] | None:
    if kind == "garment":
        garment = hub.catalog.garments.get(record_id)
        return hub.garment_view(garment, synced=True) if garment else None
    if kind == "purchase":
        card = hub.catalog.purchases.get(record_id)
        return hub.purchases.view(card) if card else None
    if kind == "zone":
        zone = hub.zones.get(record_id)
        return hub.zone_view(zone) if zone else None
    tag = hub.catalog.unknown_tags.get(record_id)
    return hub.revs.stamp("unknown_tag", record_id, dict(tag)) if tag else None


def _check_revs(hub: LaundryHub, msg: _Msg) -> None:
    """Raise Conflict if any `if_rev` / `if_revs` is out of date."""
    wanted: list[tuple[str, str, int]] = []
    if "if_rev" in msg and str(msg.get("type", "")).endswith("/tap"):
        garment = hub.catalog.by_uid(str(msg.get("uid", "")).replace(":", "").upper())
        if garment is not None:
            wanted.append(("garment", garment.id, int(msg["if_rev"])))
    elif "if_rev" in msg:
        for key, kind in _TARGETS:
            if key in msg:
                wanted.append((kind, str(msg[key]), int(msg["if_rev"])))
                break
    for record_id, rev in (msg.get("if_revs") or {}).items():
        wanted.append(("garment", str(record_id), int(rev)))
    if not wanted:
        return
    hub.revs.sync()
    for kind, record_id, rev in wanted:
        meta = hub.revs.meta(kind, record_id)
        if meta is None or meta["rev"] != rev:
            raise Conflict(kind, record_id, _current(hub, kind, record_id))


def _with_hub(
    policy: Policy,
) -> Callable[[_Handler], websocket_api.AsyncWebSocketCommandHandler]:
    """Resolve the hub, enforce the command's policy and `if_rev`, map errors."""

    def decorator(func: _Handler) -> websocket_api.AsyncWebSocketCommandHandler:
        @functools.wraps(func)
        async def wrapper(
            hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: _Msg
        ) -> None:
            entries = hass.config_entries.async_loaded_entries(DOMAIN)
            if not entries:
                connection.send_error(
                    msg["id"], "not_loaded", "Laundry Hub is not set up"
                )
                return
            hub: LaundryHub = entries[0].runtime_data
            caller = caller_for(hub, connection.user)
            try:
                check(caller, policy, hub, msg)
                _check_revs(hub, msg)
                result = await func(hass, connection, {**msg, _CALLER: caller}, hub)
            except Forbidden as err:
                connection.send_error(msg["id"], "unauthorized", str(err))
                return
            except Conflict as err:
                connection.send_message(
                    {
                        "id": msg["id"],
                        "type": "result",
                        "success": False,
                        "error": {
                            "code": "conflict",
                            "message": str(err),
                            "kind": err.kind,
                            "record_id": err.record_id,
                            "current": err.current,
                        },
                    }
                )
                return
            except ServiceValidationError as err:
                connection.send_error(
                    msg["id"], err.translation_key or "invalid", str(err)
                )
                return
            except HomeAssistantError as err:
                connection.send_error(msg["id"], "failed", str(err))
                return
            connection.send_result(msg["id"], result)

        wrapper.laundry_policy = policy  # type: ignore[attr-defined]
        return wrapper

    return decorator


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/data"})
@websocket_api.async_response
@_with_hub(Policy.READ)
async def ws_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Return the full catalog snapshot (photo URLs signed for this session)."""
    data = filter_for(caller_of(msg), hub, hub.snapshot())
    data["persons"] = [
        {"entity_id": s.entity_id, "name": s.name}
        for s in hass.states.async_all("person")
    ]
    sign = _signer(hass, connection)
    for garment in data["garments"]:
        _sign_garment(garment, sign)
    purchases = data.get("purchases") or {}
    for card in [
        *purchases.get("cards", []),
        *purchases.get("archived", []),
        *purchases.get("mine", []),
    ]:
        _sign_card(card, sign)
    return data


def _sign_garment(garment: dict[str, Any], sign: Callable[[str], str]) -> None:
    garment["photo_urls"] = [
        {"name": name, "full": sign(name), "thumb": sign(thumb_name(name))}
        for name in garment.get("photos", [])
    ]


def _member_tag(tag: dict[str, Any]) -> dict[str, Any]:
    """An unknown tag as a member sees it (no other users' ids)."""
    return {k: v for k, v in tag.items() if k != "tapped_by"}


def filter_for(caller: Caller, hub: LaundryHub, data: dict[str, Any]) -> dict[str, Any]:
    """A member sees only their own owner's garments (and matched cards)."""
    data["role"] = caller.role
    if caller.is_admin:
        return data
    own = {g["id"] for g in data["garments"] if g.get("owner") == caller.owner}
    data["garments"] = [g for g in data["garments"] if g["id"] in own]
    data["unknown_tags"] = [
        hub.revs.stamp("unknown_tag", str(t["uid"]), _member_tag(t))
        for t in member_unknown_tags(hub, caller)
    ]
    data["last_unknown"] = None
    data["owners"] = {
        k: v for k, v in (data.get("owners") or {}).items() if k == caller.owner
    }
    if data.get("purchases") is not None:
        data["purchases"] = {
            "connected": data["purchases"].get("connected"),
            "cards": [],
            "archived": [],
            "uniform_offers": [],
            "mine": [
                hub.purchases.view(c)
                for c in hub.catalog.purchases.values()
                if c.get("status") == "matched" and c.get("garment_id") in own
            ],
        }
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/suggest",
        vol.Optional("owner"): _opt_str,
        vol.Optional("wash_bin"): _opt_str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/match",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("card_id"): _str,
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/dismiss",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("card_id"): _str,
        vol.Optional("not_clothing", default=False): bool,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/restore",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("card_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_purchases_restore(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Move a dismissed or archived card back to the inbox."""
    card = await hub.purchases.async_restore(msg["card_id"])
    return _sign_card(hub.purchases.view(card), _signer(hass, connection))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/purchases/uniform",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("store_key"): _str,
        vol.Optional("accept", default=True): bool,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_purchases_uniform(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Mark a uniform supplier's matched garments as uniform (or decline)."""
    count = hub.purchases.mark_store_uniform(msg["store_key"], accept=msg["accept"])
    return {"updated": count}


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/purchases/retry_unreadable"}
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_purchases_retry(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Clear the 'couldn't be read' marks and read those emails again."""
    hub.wardrobe.require()
    return await hub.purchases.async_retry_unreadable()


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/purchases/check"})
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_purchases_check(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Check purchase emails now."""
    hub.wardrobe.require()
    return await hub.purchases.async_check(manual=True)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: _Msg
) -> None:
    """Push a message whenever the catalog changes (each carries `rev`).

    A member gets taps of their own owner's garments only.
    """
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "Laundry Hub is not set up")
        return
    hub: LaundryHub = entries[0].runtime_data
    caller = caller_for(hub, connection.user)
    if not caller.allowed:
        connection.send_error(
            msg["id"], "unauthorized", "Laundry Hub is limited to administrators"
        )
        return

    def _hub_now() -> LaundryHub | None:
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        return loaded[0].runtime_data if loaded else None

    @callback
    def _changed() -> None:
        current = _hub_now()
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {"changed": True, "rev": current.revs.rev if current else None},
            )
        )

    @callback
    def _tap(event_type: str, attributes: dict[str, Any]) -> None:
        if not caller.is_admin and (
            caller.owner is None or attributes.get("owner") != caller.owner
        ):
            return
        current = _hub_now()
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "tap": {"event_type": event_type, **attributes},
                    "rev": current.revs.rev if current else None,
                },
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/create",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
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
@_with_hub(Policy.ENROL)
async def ws_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Create a garment (a member: for their own owner, from their own tap)."""
    caller = caller_of(msg)
    garment = hub.enroll(
        uid=msg["uid"],
        name=msg["name"],
        wash_bin=msg["wash_bin"],
        dry_type=msg["dry_type"],
        owner=msg.get("owner") if caller.is_admin else caller.owner,
        notes=msg.get("notes"),
        location=msg.get("location") or None,
    )
    return hub.garment_view(garment)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/update",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
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
@_with_hub(Policy.OWN_GARMENT)
async def ws_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Update a garment."""
    changes = {k: v for k, v in msg.items() if k not in ("id", "type", "garment_id")}
    return hub.garment_view(hub.update(msg["garment_id"], changes))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/delete",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Delete a garment."""
    garment = await hub.async_remove(msg["garment_id"])
    return {"garment_id": garment.id}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/set_location",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("location"): vol.In(LOCATION_CHOICES),
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_set_location(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Manually set a garment's location."""
    return hub.garment_view(hub.set_location(msg["garment_id"], msg["location"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/reassign",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("field"): vol.In([SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE]),
        vol.Required("to"): _str,
        vol.Optional("garment_ids"): [_str],
        vol.Optional("from"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/zone/remove",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("zone_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_zone_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Remove an unused wash bin / dry type."""
    hub.remove_zone(msg["zone_id"])
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/zone/flash",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("zone_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_zone_flash(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Flash a zone's light."""
    return {"flashed": await hub.async_flash_zone(msg["zone_id"])}


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/unknown/dismiss",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("uid"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
async def ws_dismiss_unknown(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Forget an unknown tag."""
    hub.dismiss_unknown(msg["uid"])
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/upload",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("content_type"): vol.In(list(PHOTO_TYPES)),
        vol.Required("data"): str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.OWN_GARMENT)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/delete",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.OWN_GARMENT)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/wardrobe",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Optional("brand"): _opt_str,
        vol.Optional("size"): _size,
        vol.Optional("purchase"): _purchase,
        vol.Optional("wear_for"): vol.In(list(WEAR_FOR)),
        vol.Optional("uniform"): bool,
    }
)
@websocket_api.async_response
@_with_hub(Policy.OWN_GARMENT)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/describe_fields",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("fields"): {vol.In(list(DESCRIPTION_FIELDS)): object},
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/reset_field",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("field"): vol.In(list(DESCRIPTION_FIELDS)),
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/describe",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/garment/set_field",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_ids"): vol.All([_str], vol.Length(min=1, max=1000)),
        vol.Required("field"): _str,
        vol.Required("value"): object,
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/remove",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("photo"): _str,
    }
)
@websocket_api.async_response
@_with_hub(Policy.OWN_GARMENT)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/reorder",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("photos"): [_str],
    }
)
@websocket_api.async_response
@_with_hub(Policy.OWN_GARMENT)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import_product_url",
        vol.Required("url"): vol.All(str, vol.Length(min=1, max=2000)),
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/import/apply",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("garment_id"): _str,
        vol.Required("token"): _str,
        vol.Optional("fields"): [_str],
        vol.Optional("images"): [vol.Coerce(int)],
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/owner/update",
        vol.Optional("if_rev"): _rev,
        vol.Optional("if_revs"): _revs,
        vol.Required("owner"): _str,
        vol.Required("work_label"): vol.All(str, vol.Length(min=1, max=30)),
    }
)
@websocket_api.async_response
@_with_hub(Policy.ADMIN)
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


# --- app API (v0.2.3) ---------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/api_info"})
@websocket_api.async_response
@_with_hub(Policy.READ)
async def ws_api_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """What this server supports and who the caller is."""
    caller = caller_of(msg)
    return {
        "api_version": API_VERSION,
        "integration_version": VERSION,
        "wardrobe_enabled": hub.wardrobe.enabled,
        "capabilities": capabilities(hub),
        "role": caller.role,
        "owner": caller.owner,
        "app_access": "household" if caller.household else "admins",
        "rev": hub.revs.rev,
    }


def capabilities(hub: LaundryHub) -> list[str]:
    """Features the app may show (more arrive in later versions)."""
    caps = ["phone_tap", "changes", "if_rev"]
    if app_access(hub) == ACCESS_HOUSEHOLD:
        caps.append("member_enroll")
    if hub.wardrobe.enabled:
        caps.append("url_import")
        if hub.wardrobe.ai_entity:
            caps.append("ai_describe")
        if hub.purchases.connected:
            caps.append("purchases")
    return caps


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/changes", vol.Required("since_rev"): _rev}
)
@websocket_api.async_response
@_with_hub(Policy.READ)
async def ws_changes(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Everything with rev > since_rev, plus tombstones (or reset)."""
    caller = caller_of(msg)
    since = int(msg["since_rev"])
    revs = hub.revs
    revs.sync()
    if revs.needs_reset(since):
        return {"reset": True, "rev": revs.rev}
    ids = revs.changed_ids(since)
    sign = _signer(hass, connection)
    garments: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    for gid in ids["garment"]:
        garment = hub.catalog.garments.get(gid)
        if garment is None:
            continue
        meta = revs.meta("garment", gid) or {"rev": 0}
        if not can_see_garment(caller, garment):
            # Not (or no longer) the member's: tell the app to drop it.
            hidden.append(
                {"id": gid, "kind": "garment", "rev": meta["rev"], "deleted": True}
            )
            continue
        view = hub.garment_view(garment, synced=True)
        _sign_garment(view, sign)
        garments.append(view)
    zones = [hub.zone_view(hub.zones[z]) for z in ids["zone"] if z in hub.zones]
    unknown: list[dict[str, Any]] = []
    purchases: list[dict[str, Any]] = []
    if caller.is_admin:
        unknown = [
            revs.stamp("unknown_tag", uid, dict(hub.catalog.unknown_tags[uid]))
            for uid in ids["unknown_tag"]
            if uid in hub.catalog.unknown_tags
        ]
    else:
        mine = {str(t["uid"]) for t in member_unknown_tags(hub, caller)}
        unknown = [
            revs.stamp("unknown_tag", uid, _member_tag(hub.catalog.unknown_tags[uid]))
            for uid in ids["unknown_tag"]
            if uid in mine
        ]
    for cid in ids["purchase"]:
        card = hub.catalog.purchases.get(cid)
        if card is None:
            continue
        garment = hub.catalog.garments.get(str(card.get("garment_id")))
        visible = caller.is_admin or (
            card.get("status") == "matched"
            and garment is not None
            and can_see_garment(caller, garment)
        )
        if visible:
            purchases.append(_sign_card(hub.purchases.view(card), sign))
    tombstones = revs.tombstones_since(since)
    if not caller.is_admin:
        tombstones = [
            t for t in tombstones if t["kind"] in ("garment", "zone", "purchase")
        ]
    return {
        "reset": False,
        "rev": revs.rev,
        "garments": garments,
        "zones": zones,
        "unknown_tags": unknown,
        "purchases": purchases,
        "deleted": sorted([*tombstones, *hidden], key=lambda t: t["rev"]),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/photo/url",
        vol.Required("garment_id"): _str,
        vol.Required("filename"): _str,
        vol.Optional("size", default="thumb"): vol.In(["thumb", "full"]),
    }
)
@websocket_api.async_response
@_with_hub(Policy.READ)
async def ws_photo_url(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """Sign one photo URL on demand (24 h)."""
    garment = hub.resolve_garment(msg["garment_id"])
    if not can_see_garment(caller_of(msg), garment):
        raise Forbidden("You can only see your own garments")
    if msg["filename"] not in garment.photos:
        raise hub_validation("unknown_photo", "That photo isn't on this garment")
    name = msg["filename"] if msg["size"] == "full" else thumb_name(msg["filename"])
    return {
        "url": _signer(hass, connection)(name),
        "expires_in": PHOTO_URL_TTL_HOURS * 3600,
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/tap",
        vol.Required("uid"): _str,
        vol.Required("source"): "phone",
        vol.Optional("device_name"): vol.All(str, vol.Length(max=60)),
        vol.Optional("action"): _str,
        vol.Optional("if_rev"): _rev,
    }
)
@websocket_api.async_response
@_with_hub(Policy.TAP)
async def ws_tap(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: _Msg,
    hub: LaundryHub,
) -> Any:
    """A phone acting as a tap station."""
    result = await hub.async_phone_tap(
        msg["uid"],
        device_name=msg.get("device_name"),
        action=msg.get("action"),
        caller=caller_of(msg),
    )
    if result.get("garment") and result["garment"].get("photos") is not None:
        _sign_garment(result["garment"], _signer(hass, connection))
    return result


def hub_validation(key: str, message: str) -> ServiceValidationError:
    """Shortcut for a translatable validation error."""
    from .hub import validation_error  # noqa: PLC0415

    return validation_error(key, message)


ws_subscribe.laundry_policy = Policy.READ  # type: ignore[attr-defined]


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
        ws_purchases_retry,
        ws_purchases_restore,
        ws_purchases_uniform,
        ws_api_info,
        ws_changes,
        ws_photo_url,
        ws_tap,
    ):
        websocket_api.async_register_command(hass, handler)
