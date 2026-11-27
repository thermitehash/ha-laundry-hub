"""Actions (services) validation and behaviour."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
import probatio as vol
import pytest

from custom_components.laundry_hub.const import DOMAIN
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import bin_id, dry_id


async def call(hass: HomeAssistant, service: str, data: dict, response: bool = False):  # noqa: ANN201
    return await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=response
    )


async def test_enroll_defaults_and_response(
    hass: HomeAssistant, hub: LaundryHub
) -> None:
    resp = await call(hass, "enroll_garment", {"uid": "11223344", "name": "Tee"}, True)
    garment = hub.catalog.garments[resp["garment_id"]]
    assert resp["uids"] == ["11223344"]
    # Defaults to the first wash bin / dry type by order.
    assert garment.wash_bin == bin_id(hub, "Darks")
    assert garment.dry_type == dry_id(hub, "Dryer")
    assert garment.state == "new"
    await call(
        hass,
        "enroll_garment",
        {
            "uid": "55667788",
            "name": "Jeans",
            "wash_bin": "darks",
            "dry_type": "Hang rod",
            "location": "closet_1",
            "owner": "",
        },
    )
    jeans = hub.catalog.by_uid("55667788")
    assert jeans.state == "put_away"
    assert jeans.owner is None


@pytest.mark.parametrize(
    ("data", "exc"),
    [
        ({"uid": "11223344"}, vol.Invalid),
        ({"uid": "11223344", "name": ""}, vol.Invalid),
        ({"uid": "11223344", "name": "x", "owner": "light.x"}, vol.Invalid),
        ({"uid": "11223344", "name": "x", "location": "in_wash"}, vol.Invalid),
        ({"uid": "1122", "name": "x"}, ServiceValidationError),
        (
            {"uid": "11223344", "name": "x", "wash_bin": "Hang rod"},
            ServiceValidationError,
        ),
        (
            {"uid": "11223344", "name": "x", "owner": "person.nobody"},
            ServiceValidationError,
        ),
    ],
)
async def test_enroll_validation(
    hass: HomeAssistant, hub: LaundryHub, data, exc
) -> None:
    with pytest.raises(exc):
        await call(hass, "enroll_garment", data)


async def test_enroll_duplicate_uid_error_message(
    hass: HomeAssistant, hub: LaundryHub
) -> None:
    await call(hass, "enroll_garment", {"uid": "11223344", "name": "A"})
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "enroll_garment", {"uid": "11-22-33-44", "name": "B"})
    assert err.value.translation_key == "duplicate_uid"
    assert err.value.translation_placeholders["uid"] == "11223344"


async def test_update_remove_set_location(hass: HomeAssistant, hub: LaundryHub) -> None:
    hass.states.async_set("person.sam", "home")
    g = hub.enroll(uid="11223344", name="A")
    await call(
        hass,
        "update_garment",
        {
            "garment": "11223344",
            "name": "B",
            "owner": "person.sam",
            "add_uid": "55667788",
        },
    )
    assert (g.name, g.owner, g.uids) == ("B", "person.sam", ["11223344", "55667788"])
    await call(hass, "update_garment", {"garment": g.id, "remove_uid": "11223344"})
    assert g.uids == ["55667788"]
    await call(hass, "set_location", {"garment": g.id, "location": "drying"})
    assert (g.state, g.location) == ("drying", "laundry")
    with pytest.raises(vol.Invalid):
        await call(hass, "set_location", {"garment": g.id, "location": "attic"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "update_garment", {"garment": "nope", "name": "x"})
    await call(hass, "remove_garment", {"garment": "55667788"})
    assert hub.catalog.garments == {}
    with pytest.raises(ServiceValidationError):
        await call(hass, "remove_garment", {"garment": g.id})


async def test_reassign_service(hass: HomeAssistant, hub: LaundryHub) -> None:
    hub.enroll(uid="11223344", name="A", wash_bin="Reds")
    resp = await call(
        hass, "reassign", {"field": "wash_bin", "to": "Colors", "from": "Reds"}, True
    )
    assert resp == {"moved": 1}
    with pytest.raises(vol.Invalid):
        await call(hass, "reassign", {"field": "wash_bin", "to": "Colors"})
    resp = await call(
        hass,
        "reassign",
        {"field": "dry_type", "to": "Lay flat", "garments": "11223344"},
        True,
    )
    assert resp == {"moved": 1}


async def test_flash_zone_service(
    hass: HomeAssistant, hub: LaundryHub, entry, light_calls
) -> None:
    with pytest.raises(ServiceValidationError, match="no light configured"):
        await call(hass, "flash_zone", {"zone": "Dryer"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "flash_zone", {"zone": "Nope"})
    sub = entry.subentries[dry_id(hub, "Dryer")]
    hass.states.async_set("light.dry", "on")
    hass.config_entries.async_update_subentry(
        entry, sub, data={**sub.data, "light_entity": "light.dry"}
    )
    await hass.async_block_till_done()
    await call(hass, "flash_zone", {"zone": "Dryer"})
    assert light_calls["turn_on"][-1].data == {
        "entity_id": "light.dry",
        "rgb_color": [255, 120, 0],
    }
    await entry.runtime_data.flash.async_stop()


async def test_services_when_not_loaded(hass: HomeAssistant, entry) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "enroll_garment", {"uid": "11223344", "name": "A"})
    assert err.value.translation_key == "not_loaded"


async def test_no_zones(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import OPTIONS

    entry = MockConfigEntry(domain=DOMAIN, options=OPTIONS, subentries_data=[])
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "enroll_garment", {"uid": "11223344", "name": "A"})
    assert err.value.translation_key == "no_zones"


async def test_ambiguous_zone_name(hass: HomeAssistant, hub: LaundryHub, entry) -> None:
    # A wash bin named like a dry type: untyped lookup by name is ambiguous.
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigSubentry

    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(
                {"name": "Dryer", "color": [1, 1, 1], "light_entity": None, "order": 9}
            ),
            subentry_type="wash_bin",
            title="Dryer",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()
    hub = entry.runtime_data
    with pytest.raises(ServiceValidationError):
        hub.resolve_zone("Dryer")
    assert hub.resolve_zone("Dryer", "dry_type").type == "dry_type"
    assert hub.resolve_zone("Dryer", "wash_bin").type == "wash_bin"
