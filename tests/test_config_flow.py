"""Config, options and subentry flow tests."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.laundry_hub.const import (
    CONF_NOTIFY_TARGET,
    CONF_STATION_DEVICES,
    DOMAIN,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import add_esphome_station, bin_id, make_entry

BASE = {"feedback_timeout": 2, "flash_duration": 15, "missing_after_days": 14}


async def test_user_flow_seeds(hass: HomeAssistant) -> None:
    _, dev = add_esphome_station(hass, "laundry-room")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, "laundry_device": dev}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options[CONF_STATION_DEVICES] == {"laundry": dev}
    assert entry.options["flash_duration"] == 15
    assert entry.options[CONF_NOTIFY_TARGET] is None
    bins = [
        s.title
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_WASH_BIN
    ]
    dries = [
        s.title
        for s in entry.subentries.values()
        if s.subentry_type == SUBENTRY_DRY_TYPE
    ]
    assert bins == ["Darks", "Lights & whites", "Colors", "Reds", "Delicates"]
    assert dries == ["Dryer", "Hang rod", "Lay flat"]
    await hass.async_block_till_done()
    hub: LaundryHub = entry.runtime_data
    assert [z.name for z in hub.zones_of(SUBENTRY_WASH_BIN)] == bins


async def test_single_instance(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_user_flow_errors(hass: HomeAssistant) -> None:
    _, dev = add_esphome_station(hass, "laundry-room")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **BASE,
            "laundry_device": dev,
            "closet_1_device": dev,
            CONF_NOTIFY_TARGET: "nope",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        "base": "duplicate_device",
        CONF_NOTIFY_TARGET: "notify_not_found",
    }
    # Fix it: notify service exists now.
    async_mock_service(hass, "notify", "mobile_app_phone")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **BASE,
            "laundry_device": dev,
            CONF_NOTIFY_TARGET: " notify.mobile_app_phone ",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options[CONF_NOTIFY_TARGET] == "notify.mobile_app_phone"


async def test_notify_entity_accepted(hass: HomeAssistant) -> None:
    hass.states.async_set("notify.kitchen", "unknown")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE, CONF_NOTIFY_TARGET: "notify.kitchen"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow(hass: HomeAssistant, entry) -> None:
    _, dev = add_esphome_station(hass, "closet-2")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**BASE, "flash_duration": 30, "closet_2_device": dev}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["flash_duration"] == 30
    assert entry.runtime_data.flash_duration == 30
    assert entry.options[CONF_STATION_DEVICES] == {"closet_2": dev}
    # Re-open shows current values as defaults; errors are reported.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(k): k for k in result["data_schema"].schema}
    assert schema_keys["closet_2_device"].description == {"suggested_value": dev}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**BASE, CONF_NOTIFY_TARGET: "ghost"}
    )
    assert result["errors"] == {CONF_NOTIFY_TARGET: "notify_not_found"}


async def test_options_shows_notify_default(hass: HomeAssistant) -> None:
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = make_entry({CONF_NOTIFY_TARGET: "mobile_app_phone"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_NOTIFY_TARGET].description == {
        "suggested_value": "mobile_app_phone"
    }


async def _start_sub(
    hass: HomeAssistant, entry, zone_type: str, source=SOURCE_USER, sid=None
):
    context = {"source": source}
    if sid:
        context["subentry_id"] = sid
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, zone_type), context=context
    )


async def test_add_wash_bin(hass: HomeAssistant, entry) -> None:
    hass.states.async_set("light.wled_seg6", "off")
    result = await _start_sub(hass, entry, SUBENTRY_WASH_BIN)
    assert result["type"] is FlowResultType.FORM
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys["order"].default() == 5  # appended after the 5 seeded bins
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": " Towels ",
            "color": [1, 2, 3],
            "light_entity": "light.wled_seg6",
            "order": 9,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    hub: LaundryHub = entry.runtime_data
    towels = [z for z in hub.zones_of(SUBENTRY_WASH_BIN) if z.name == "Towels"][0]
    assert towels.color == (1, 2, 3)
    assert towels.light_entity == "light.wled_seg6"
    assert hass.states.get("sensor.laundry_hub_bin_towels") is not None


async def test_add_zone_errors(hass: HomeAssistant, entry) -> None:
    result = await _start_sub(hass, entry, SUBENTRY_DRY_TYPE)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "dryer", "color": [1, 2, 3], "order": 1}
    )
    assert result["errors"] == {"name": "name_exists"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "   ", "color": [1, 2, 3], "order": 1}
    )
    assert result["errors"] == {"name": "invalid_name"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "x" * 101, "color": [1, 2, 3], "order": 1}
    )
    assert result["errors"] == {"name": "invalid_name"}
    # A wash bin may share a name with a dry type.
    result2 = await _start_sub(hass, entry, SUBENTRY_WASH_BIN)
    result2 = await hass.config_entries.subentries.async_configure(
        result2["flow_id"], {"name": "Dryer", "color": [1, 2, 3], "order": 1}
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY


async def test_edit_zone(hass: HomeAssistant, entry) -> None:
    hub: LaundryHub = entry.runtime_data
    sid = bin_id(hub, "Reds")
    result = await _start_sub(hass, entry, SUBENTRY_WASH_BIN, SOURCE_RECONFIGURE, sid)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    # Renaming to an existing name fails; same name (own) is fine.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Darks", "color": [9, 9, 9], "order": 0}
    )
    assert result["errors"] == {"name": "name_exists"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Reds & pinks", "color": [9, 9, 9], "order": 0}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    hub = entry.runtime_data
    zone = hub.zones[sid]
    assert (zone.name, zone.color, zone.order) == ("Reds & pinks", (9, 9, 9), 0)
    # Reordered first.
    assert hub.zones_of(SUBENTRY_WASH_BIN)[0].id in (sid, bin_id(hub, "Darks"))


async def test_delete_zone_blocked_when_in_use(hass: HomeAssistant, entry) -> None:
    hub: LaundryHub = entry.runtime_data
    sid = bin_id(hub, "Reds")
    hub.enroll(uid="11223344", name="Red shirt", wash_bin=sid)
    hub.enroll(uid="11223355", name="Red socks", wash_bin=sid)
    result = await _start_sub(hass, entry, SUBENTRY_WASH_BIN, SOURCE_RECONFIGURE, sid)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Reds", "color": [255, 0, 0], "order": 3, "delete": True},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "zone_in_use"}
    assert result["description_placeholders"] == {"name": "Reds", "count": "2"}
    assert sid in entry.subentries


async def test_delete_zone_when_unused(hass: HomeAssistant, entry) -> None:
    hub: LaundryHub = entry.runtime_data
    sid = bin_id(hub, "Reds")
    result = await _start_sub(hass, entry, SUBENTRY_WASH_BIN, SOURCE_RECONFIGURE, sid)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Reds", "color": [255, 0, 0], "order": 3, "delete": True},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "zone_removed"
    await hass.async_block_till_done()
    assert sid not in entry.subentries
    assert hass.states.get("sensor.laundry_hub_bin_reds") is None


async def test_delete_zone_entry_not_loaded(hass: HomeAssistant) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    sid = next(iter(entry.subentries))
    sub = entry.subentries[sid]
    result = await _start_sub(hass, entry, sub.subentry_type, SOURCE_RECONFIGURE, sid)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": sub.title, "color": [1, 2, 3], "order": 0, "delete": True},
    )
    assert result["errors"] == {"base": "not_loaded"}
