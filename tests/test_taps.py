"""End-to-end tap handling through the HA event bus."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.laundry_hub.const import (
    CONF_NOTIFY_TARGET,
    CONF_STATION_DEVICES,
    TAP_EVENT,
)
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import add_esphome_station, bin_id, dry_id, make_entry

UID = "04A1B2C3D4E5F6"


async def fire(
    hass: HomeAssistant, station: str, uid: str = UID, mode: str = "auto", **extra: Any
) -> None:
    hass.bus.async_fire(
        TAP_EVENT, {"station_id": station, "uid": uid, "mode": mode, **extra}
    )
    await hass.async_block_till_done()


def light_on(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(entity_id, "off")


@pytest.fixture
async def setup(
    hass: HomeAssistant, light_calls: dict[str, list[ServiceCall]]
) -> tuple[
    LaundryHub, dict[str, list[ServiceCall]], list[ServiceCall], list[ServiceCall]
]:
    """Entry with stations, lights and notify configured."""
    _, laundry_dev = add_esphome_station(hass, "laundry-room")
    _, closet_dev = add_esphome_station(hass, "closet-1")
    feedback = async_mock_service(hass, "esphome", "laundry_room_feedback")
    notify = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = make_entry(
        {
            CONF_STATION_DEVICES: {"laundry": laundry_dev, "closet_1": closet_dev},
            CONF_NOTIFY_TARGET: "mobile_app_phone",
        }
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub: LaundryHub = entry.runtime_data
    # Lights for Darks and Hang rod.
    for name, entity in (("Darks", "light.sort_darks"), ("Hang rod", "light.dry_hang")):
        light_on(hass, entity)
        zone = next(z for z in hub.zones.values() if z.name == name)
        sub = entry.subentries[zone.id]
        hass.config_entries.async_update_subentry(
            entry, sub, data={**sub.data, "light_entity": entity}
        )
    await hass.async_block_till_done()
    hub = entry.runtime_data  # reloaded
    hub.enroll(
        uid=UID,
        name="Blue hoodie",
        wash_bin=bin_id(hub, "Darks"),
        dry_type=dry_id(hub, "Hang rod"),
        location="closet_1",
    )
    return hub, light_calls, feedback, notify


def garment(hub: LaundryHub):  # noqa: ANN201
    g = hub.catalog.by_uid(UID)
    assert g is not None
    return g


async def test_full_cycle(
    hass: HomeAssistant, setup, freezer: FrozenDateTimeFactory
) -> None:
    hub, lights, feedback, _ = setup
    events = async_capture_events(hass, "state_changed")

    await fire(hass, "laundry")
    assert garment(hub).state == "in_wash"
    assert lights["turn_on"][-1].data == {
        "entity_id": "light.sort_darks",
        "rgb_color": [40, 40, 160],
    }
    assert feedback[-1].data == {"result": "known"}
    assert hass.states.get("sensor.laundry_hub_in_wash").state == "1"
    assert hass.states.get("sensor.laundry_hub_bin_darks").state == "1"
    assert hass.states.get("sensor.laundry_hub_closet_1_count").state == "0"
    assert (
        hass.states.get("event.laundry_hub_tap").attributes["event_type"] == "in_wash"
    )
    assert (
        hass.states.get("sensor.laundry_hub_in_wash").attributes["bins"]["Darks"] == 1
    )

    # Repeat within 5 min: same zone again, no state change.
    freezer.tick(timedelta(minutes=2))
    await fire(hass, "laundry")
    assert garment(hub).state == "in_wash"
    assert lights["turn_on"][-1].data["entity_id"] == "light.sort_darks"
    tap_state = hass.states.get("event.laundry_hub_tap")
    assert tap_state.attributes["repeat"] is True

    # ≥ 5 min after the last laundry tap → drying, dry light.
    freezer.tick(timedelta(minutes=5))
    await fire(hass, "laundry")
    assert garment(hub).state == "drying"
    assert lights["turn_on"][-1].data == {
        "entity_id": "light.dry_hang",
        "rgb_color": [0, 200, 255],
    }
    # The wash light was replaced (turned off) by the new flash.
    assert {"entity_id": "light.sort_darks"} in [c.data for c in lights["turn_off"]]
    assert hass.states.get("sensor.laundry_hub_drying").state == "1"
    assert (
        hass.states.get("sensor.laundry_hub_drying").attributes["dry_types"]["Hang rod"]
        == 1
    )

    # Closet → put away, moved.
    await fire(hass, "closet_1")
    g = garment(hub)
    assert (g.state, g.location, g.last_seen_station) == (
        "put_away",
        "closet_1",
        "closet_1",
    )
    assert hass.states.get("sensor.laundry_hub_closet_1_count").state == "1"
    assert len(events) > 0


async def test_closet_feedback(hass: HomeAssistant, setup) -> None:
    hub, _, _, _ = setup
    closet_feedback = async_mock_service(hass, "esphome", "closet_1_feedback")
    await fire(hass, "closet_1")  # already in closet_1
    assert closet_feedback[-1].data == {"result": "known"}
    await fire(hass, "closet_2")  # no feedback device configured; still moves
    assert garment(hub).location == "closet_2"
    await fire(hass, "closet_1")
    assert closet_feedback[-1].data == {"result": "moved"}


async def test_sort_and_dry_modes(hass: HomeAssistant, setup) -> None:
    hub, lights, _, _ = setup
    await fire(hass, "laundry", mode="dry")
    assert garment(hub).state == "drying"
    assert lights["turn_on"][-1].data["entity_id"] == "light.dry_hang"
    await fire(hass, "laundry", mode="sort")
    assert garment(hub).state == "in_wash"
    assert lights["turn_on"][-1].data["entity_id"] == "light.sort_darks"


async def test_bad_mode_is_auto(hass: HomeAssistant, setup, caplog) -> None:
    hub, _, _, _ = setup
    await fire(hass, "laundry", mode="turbo")
    assert garment(hub).state == "in_wash"
    assert "Unknown tap mode" in caplog.text


async def test_uid_normalized(hass: HomeAssistant, setup) -> None:
    hub, _, _, _ = setup
    await fire(hass, "laundry", uid="04:a1:b2:c3:d4:e5:f6")
    assert garment(hub).state == "in_wash"


@pytest.mark.parametrize(
    "data",
    [
        {"station_id": "garage", "uid": UID},
        {"station_id": "laundry", "uid": "zz"},
        {"station_id": "laundry", "uid": None},
        {"uid": UID},
    ],
)
async def test_invalid_events_ignored(hass: HomeAssistant, setup, data, caplog) -> None:
    hub, lights, feedback, _ = setup
    hass.bus.async_fire(TAP_EVENT, data)
    await hass.async_block_till_done()
    assert garment(hub).state == "put_away"
    assert feedback == []
    assert "Ignoring tap" in caplog.text


async def test_unknown_tag(hass: HomeAssistant, setup, freezer) -> None:
    hub, _, feedback, notify = setup
    await fire(hass, "laundry", uid="04AABBCCDDEEFF")
    assert feedback[-1].data == {"result": "unknown"}
    assert hub.catalog.last_unknown["uid"] == "04AABBCCDDEEFF"
    assert hass.states.get("sensor.laundry_hub_unknown_tags").state == "1"
    assert (
        hass.states.get("event.laundry_hub_tap").attributes["event_type"] == "unknown"
    )
    assert len(notify) == 1
    assert notify[0].data["title"] == "New laundry tag"
    assert notify[0].data["data"]["actions"][0]["uri"] == (
        "/laundry?page=enroll&uid=04AABBCCDDEEFF"
    )
    # Throttled: second tap within an hour does not notify again.
    await fire(hass, "laundry", uid="04AABBCCDDEEFF")
    assert len(notify) == 1
    assert hub.catalog.unknown_tags["04AABBCCDDEEFF"]["count"] == 2
    freezer.tick(timedelta(hours=1, seconds=1))
    await fire(hass, "laundry", uid="04AABBCCDDEEFF")
    assert len(notify) == 2
    # Enrolling it clears unknown state.
    hub.enroll(uid="04AABBCCDDEEFF", name="Sock")
    assert hub.catalog.last_unknown is None
    assert hub.catalog.unknown_tags == {}


async def test_notify_entity_target(hass: HomeAssistant) -> None:
    hass.states.async_set("notify.family", "unknown")
    calls = async_mock_service(hass, "notify", "send_message")
    entry = make_entry({CONF_NOTIFY_TARGET: "notify.family"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await fire(hass, "closet_2", uid="11223344")
    assert calls[0].data["entity_id"] == "notify.family"
    assert calls[0].data["title"] == "New laundry tag"


async def test_notify_missing_target(hass: HomeAssistant, caplog) -> None:
    entry = make_entry({CONF_NOTIFY_TARGET: "mobile_app_gone"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await fire(hass, "closet_2", uid="11223344")
    assert "Notify target mobile_app_gone not found" in caplog.text


async def test_notify_failure_logged(hass: HomeAssistant, caplog) -> None:
    async def boom(call: ServiceCall) -> None:
        raise HomeAssistantError("push down")

    hass.services.async_register("notify", "mobile_app_x", boom)
    entry = make_entry({CONF_NOTIFY_TARGET: "mobile_app_x"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await fire(hass, "closet_2", uid="11223344")
    assert "Enrollment notification failed" in caplog.text


async def test_feedback_service_missing(hass: HomeAssistant, caplog) -> None:
    _, dev = add_esphome_station(hass, "laundry-room")
    entry = make_entry({CONF_STATION_DEVICES: {"laundry": dev}})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await fire(hass, "laundry", uid="11223344")
    assert "esphome.laundry_room_feedback not available" in caplog.text


async def test_feedback_timeout(hass: HomeAssistant, caplog) -> None:
    import asyncio

    _, dev = add_esphome_station(hass, "laundry-room")

    async def slow(call: ServiceCall) -> None:
        await asyncio.sleep(10)

    hass.services.async_register("esphome", "laundry_room_feedback", slow)
    entry = make_entry(
        {CONF_STATION_DEVICES: {"laundry": dev}, "feedback_timeout": 0.01}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hass.bus.async_fire(TAP_EVENT, {"station_id": "laundry", "uid": "11223344"})
    await asyncio.sleep(0.2)
    assert "Feedback esphome.laundry_room_feedback failed" in caplog.text


async def test_feedback_device_resolution(hass: HomeAssistant) -> None:
    entry = make_entry({CONF_STATION_DEVICES: {"laundry": "nope", "closet_1": ""}})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    assert hub.feedback_service("laundry") is None
    assert hub.feedback_service("closet_1") is None
    # Device belonging to a non-esphome entry, and an esphome entry without name.
    from homeassistant.helpers import device_registry as dr

    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    nameless = MockConfigEntry(domain="esphome", data={})
    nameless.add_to_hass(hass)
    reg = dr.async_get(hass)
    dev_other = reg.async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other", "x")}
    )
    dev_nameless = reg.async_get_or_create(
        config_entry_id=nameless.entry_id, identifiers={("esphome", "y")}
    )
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_STATION_DEVICES: {
                "laundry": dev_other.id,
                "closet_1": dev_nameless.id,
            },
        },
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.feedback_service("laundry") is None
    assert entry.runtime_data.feedback_service("closet_1") is None


async def test_unexpected_error_does_not_escape(
    hass: HomeAssistant, setup, caplog
) -> None:
    hub, _, feedback, _ = setup
    with patch(
        "custom_components.laundry_hub.hub.decide", side_effect=RuntimeError("bug")
    ):
        await fire(hass, "laundry")
    assert "Unexpected error handling tap" in caplog.text
    assert feedback[-1].data == {"result": "error"}


async def test_corrupt_garment_state_gives_error_feedback(
    hass: HomeAssistant, setup
) -> None:
    hub, _, feedback, _ = setup
    garment(hub).state = "bogus"
    await fire(hass, "laundry")
    assert feedback[-1].data == {"result": "error"}


async def test_missing_zone_gives_error_feedback(hass: HomeAssistant, setup) -> None:
    hub, _, feedback, _ = setup
    garment(hub).wash_bin = "deleted"
    await fire(hass, "laundry")
    assert garment(hub).state == "in_wash"  # state still updates
    assert feedback[-1].data == {"result": "error"}


async def test_rapid_taps_serialized(hass: HomeAssistant, setup, freezer) -> None:
    """Many taps fired in the same loop iteration are processed in order."""
    hub, lights, _, _ = setup
    freezer.tick(timedelta(minutes=10))
    for _ in range(5):
        hass.bus.async_fire(
            TAP_EVENT, {"station_id": "laundry", "uid": UID, "mode": "auto"}
        )
    await hass.async_block_till_done()
    # First tap → in_wash; the other four are repeats within the window.
    assert garment(hub).state == "in_wash"
    assert hub.problems() == {}


async def test_flash_off_after_duration(hass: HomeAssistant, setup, freezer) -> None:
    hub, lights, _, _ = setup
    await fire(hass, "laundry")
    assert hub.flash.current == "light.sort_darks"
    freezer.tick(timedelta(seconds=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hub.flash.current is None
    assert lights["turn_off"][-1].data == {"entity_id": "light.sort_darks"}


async def test_tap_from_wrong_device_ignored(
    hass: HomeAssistant, setup, caplog
) -> None:
    """Regression: any ESPHome device could impersonate a configured station."""
    hub, _, feedback, _ = setup
    hass.bus.async_fire(
        TAP_EVENT,
        {"station_id": "laundry", "uid": UID, "mode": "auto", "device_id": "intruder"},
    )
    await hass.async_block_till_done()
    assert garment(hub).state == "put_away"
    assert "from another device" in caplog.text
    # The configured device is accepted.
    expected = hub.options["station_devices"]["laundry"]
    await fire(hass, "laundry", device_id=expected)
    assert garment(hub).state == "in_wash"


async def test_queued_taps_use_fire_time(hass: HomeAssistant, setup, freezer) -> None:
    """Regression: taps queued behind the lock were judged by processing time."""
    hub, _, _, _ = setup
    from homeassistant.util import dt as dt_util

    t0 = dt_util.utcnow()
    await hub.async_handle_tap("laundry", UID, "auto", t0)
    assert garment(hub).state == "in_wash"
    # Processed 10 minutes later, but fired 1 minute after the first tap: repeat.
    freezer.tick(timedelta(minutes=10))
    result = await hub.async_handle_tap(
        "laundry", UID, "auto", t0 + timedelta(minutes=1)
    )
    assert result.decision is not None and result.decision.repeat is True
    assert garment(hub).state == "in_wash"
    await hub.flash.async_stop()


async def test_notify_throttle_map_pruned(hass: HomeAssistant, setup, freezer) -> None:
    hub, _, _, notify = setup
    for i in range(5):
        await fire(hass, "laundry", uid=f"AA0000{i:02d}")
    assert len(hub._notified) == 5
    freezer.tick(timedelta(hours=2))
    await fire(hass, "laundry", uid="BB000000")
    assert list(hub._notified) == ["BB000000"]
