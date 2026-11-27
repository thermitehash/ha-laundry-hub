"""Missing detection (frozen clock) and diagnostics redaction."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.laundry_hub.const import (
    CONF_NOTIFY_TARGET,
    CONF_STATION_DEVICES,
    TAP_EVENT,
)
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import add_esphome_station, make_entry


async def test_missing_after_days(
    hass: HomeAssistant, hub: LaundryHub, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-11-01 08:00:00+00:00")
    seen = hub.enroll(uid="11223344", name="Seen")
    never = hub.enroll(uid="55667788", name="Never tapped")
    freezer.tick(timedelta(days=10))
    hass.bus.async_fire(TAP_EVENT, {"station_id": "closet_1", "uid": "11223344"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.laundry_hub_missing").state == "0"

    # 14 days after creation: the never-tapped garment is missing. The hourly
    # refresh updates the sensor without any tap.
    freezer.tick(timedelta(days=4, hours=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.laundry_hub_missing")
    assert state.state == "1"
    assert state.attributes["garments"][0]["name"] == "Never tapped"
    assert state.attributes["truncated"] is False
    assert hub.is_missing(never)
    assert not hub.is_missing(seen)
    snapshot = hub.snapshot()
    assert {g["name"]: g["missing"] for g in snapshot["garments"]} == {
        "Seen": False,
        "Never tapped": True,
    }

    # Never auto-deleted, even much later.
    freezer.tick(timedelta(days=365))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(hub.catalog.garments) == 2
    assert hass.states.get("sensor.laundry_hub_missing").state == "2"

    # A tap brings it back.
    hass.bus.async_fire(TAP_EVENT, {"station_id": "laundry", "uid": "55667788"})
    await hass.async_block_till_done()
    assert not hub.is_missing(never)


async def test_missing_after_option(hass: HomeAssistant, freezer) -> None:
    entry = make_entry({"missing_after_days": 1})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    hub.enroll(uid="11223344", name="A")
    freezer.tick(timedelta(days=1))
    assert len(hub.missing()) == 1


async def test_diagnostics_redacted(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "diagnostics", {})
    _, dev = add_esphome_station(hass, "laundry-room")
    from pytest_homeassistant_custom_component.common import async_mock_service

    async_mock_service(hass, "notify", "mobile_app_secret_phone")
    entry = make_entry(
        {
            CONF_STATION_DEVICES: {"laundry": dev},
            CONF_NOTIFY_TARGET: "mobile_app_secret_phone",
        }
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    hass.states.async_set("person.alex", "home")
    g = hub.enroll(
        uid="04A1B2C3D4E5F6", name="Secret jacket", owner="person.alex", notes="private"
    )
    g.purchase = {"store": "SecretStore", "order_ref": "ORDER-9"}
    g.photos = ["secretphoto.jpg"]
    from homeassistant.util import dt as dt_util

    hub.catalog.record_unknown("AABBCCDD", "laundry", dt_util.utcnow())
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = str(diag)
    for secret in (
        "Secret jacket",
        "person.alex",
        "private",
        "04A1B2C3D4E5F6",
        "AABBCCDD",
        "mobile_app_secret_phone",
        "SecretStore",
        "ORDER-9",
        "secretphoto",
        dev,
    ):
        assert secret not in text, secret
    assert diag["garment_count"] == 1
    assert diag["garments"][0]["tag_count"] == 1
    assert diag["unknown_tags"][0]["uid"] == "AA…DD"
    assert diag["stations_configured"] == ["laundry"]
    assert diag["feedback_targets"]["laundry"] is True
    assert diag["invariant_problems"] == {}
    assert len(diag["zones"]) == 8
