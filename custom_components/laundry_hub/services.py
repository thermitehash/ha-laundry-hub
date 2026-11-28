"""Laundry Hub actions (services)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv
import probatio as vol

from .const import (
    CLOSET_STATIONS,
    DOMAIN,
    STATE_DRYING,
    STATE_IN_WASH,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from .hub import validation_error

if TYPE_CHECKING:
    from .hub import LaundryHub

ATTR_GARMENT = "garment"
LOCATION_CHOICES = [*CLOSET_STATIONS, STATE_IN_WASH, STATE_DRYING]

_name = vol.All(cv.string, vol.Length(min=1, max=100))
_notes = vol.All(cv.string, vol.Length(max=1000))
_person = vol.Any(None, "", cv.entity_domain("person"))

ENROLL_SCHEMA = vol.Schema(
    {
        vol.Required("uid"): cv.string,
        vol.Required("name"): _name,
        vol.Optional("wash_bin"): cv.string,
        vol.Optional("dry_type"): cv.string,
        vol.Optional("owner"): _person,
        vol.Optional("notes"): _notes,
        vol.Optional("location"): vol.In(CLOSET_STATIONS),
    }
)
UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_GARMENT): cv.string,
        vol.Optional("name"): _name,
        vol.Optional("owner"): _person,
        vol.Optional("wash_bin"): cv.string,
        vol.Optional("dry_type"): cv.string,
        vol.Optional("notes"): _notes,
        vol.Optional("add_uid"): cv.string,
        vol.Optional("remove_uid"): cv.string,
    }
)
REMOVE_SCHEMA = vol.Schema({vol.Required(ATTR_GARMENT): cv.string})
SET_LOCATION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_GARMENT): cv.string,
        vol.Required("location"): vol.In(LOCATION_CHOICES),
    }
)
REASSIGN_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("field"): vol.In([SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE]),
            vol.Required("to"): cv.string,
            vol.Optional("garments"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("from"): cv.string,
        }
    ),
    cv.has_at_least_one_key("garments", "from"),
)
FLASH_SCHEMA = vol.Schema({vol.Required("zone"): cv.string})
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("url"): vol.All(cv.string, vol.Length(max=2000)),
        vol.Optional(ATTR_GARMENT): cv.string,
        vol.Optional("apply", default=False): cv.boolean,
    }
)
DESCRIBE_SCHEMA = vol.Schema({vol.Required(ATTR_GARMENT): cv.string})


def _hub(hass: HomeAssistant) -> LaundryHub:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise validation_error("not_loaded", "Laundry Hub is not set up")
    hub: LaundryHub = entries[0].runtime_data
    return hub


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Laundry Hub actions."""

    async def enroll(call: ServiceCall) -> ServiceResponse:
        hub = _hub(hass)
        garment = hub.enroll(**call.data)
        return {"garment_id": garment.id, "uids": list(garment.uids)}

    async def update(call: ServiceCall) -> None:
        data = dict(call.data)
        ref = data.pop(ATTR_GARMENT)
        _hub(hass).update(ref, data)

    async def remove(call: ServiceCall) -> None:
        await _hub(hass).async_remove(call.data[ATTR_GARMENT])

    async def set_location(call: ServiceCall) -> None:
        _hub(hass).set_location(call.data[ATTR_GARMENT], call.data["location"])

    async def reassign(call: ServiceCall) -> ServiceResponse:
        moved = _hub(hass).reassign(
            call.data["field"],
            call.data["to"],
            call.data.get("garments"),
            call.data.get("from"),
        )
        return {"moved": moved}

    async def flash_zone(call: ServiceCall) -> None:
        await _hub(hass).async_flash_zone(call.data["zone"])

    hass.services.async_register(
        DOMAIN,
        "enroll_garment",
        enroll,
        schema=ENROLL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "update_garment", update, schema=UPDATE_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_garment", remove, schema=REMOVE_SCHEMA)
    hass.services.async_register(
        DOMAIN, "set_location", set_location, schema=SET_LOCATION_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "reassign",
        reassign,
        schema=REASSIGN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "flash_zone", flash_zone, schema=FLASH_SCHEMA)

    async def import_product_url(call: ServiceCall) -> ServiceResponse:
        hub = _hub(hass)
        preview = await hub.wardrobe.async_import_url(call.data["url"])
        preview.pop("image_count", None)
        if call.data["apply"]:
            if ATTR_GARMENT not in call.data:
                raise validation_error("garment_required", "apply needs a garment")
            garment = hub.resolve_garment(call.data[ATTR_GARMENT])
            await hub.wardrobe.async_apply_import(garment, preview["token"])
            preview["applied_to"] = garment.id
        return preview

    async def describe_garment(call: ServiceCall) -> ServiceResponse:
        hub = _hub(hass)
        garment = hub.resolve_garment(call.data[ATTR_GARMENT])
        return {"description": await hub.wardrobe.async_describe(garment)}

    hass.services.async_register(
        DOMAIN,
        "import_product_url",
        import_product_url,
        schema=IMPORT_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def check_purchase_emails(call: ServiceCall) -> ServiceResponse:
        hub = _hub(hass)
        hub.wardrobe.require()
        return await hub.purchases.async_check(manual=True)

    hass.services.async_register(
        DOMAIN,
        "check_purchase_emails",
        check_purchase_emails,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "describe_garment",
        describe_garment,
        schema=DESCRIBE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
