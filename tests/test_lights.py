"""Light flash controller."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.laundry_hub.lights import FlashController
from custom_components.laundry_hub.models import Zone


def zone(light: str | None, name: str = "Z", color=(1, 2, 3)) -> Zone:
    return Zone(
        id=name, type="wash_bin", name=name, color=color, light_entity=light, order=0
    )


async def test_flash_and_off(hass: HomeAssistant, light_calls, freezer) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    assert await flash.async_flash(zone("light.a"), 10)
    assert light_calls["turn_on"][0].data == {
        "entity_id": "light.a",
        "rgb_color": [1, 2, 3],
    }
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert light_calls["turn_off"] == []
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert light_calls["turn_off"][0].data == {"entity_id": "light.a"}
    assert flash.current is None


async def test_replacement_last_wins(hass: HomeAssistant, light_calls, freezer) -> None:
    hass.states.async_set("light.a", "off")
    hass.states.async_set("light.b", "off")
    flash = FlashController(hass)
    await flash.async_flash(zone("light.a"), 10)
    freezer.tick(timedelta(seconds=8))
    await flash.async_flash(zone("light.b", color=(9, 9, 9)), 10)
    assert light_calls["turn_off"][0].data == {"entity_id": "light.a"}
    assert flash.current == "light.b"
    # The old timer (would fire at t=10) must not turn off light.b.
    freezer.tick(timedelta(seconds=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(light_calls["turn_off"]) == 1
    freezer.tick(timedelta(seconds=8))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert light_calls["turn_off"][-1].data == {"entity_id": "light.b"}


async def test_same_light_reflash_extends(
    hass: HomeAssistant, light_calls, freezer
) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    await flash.async_flash(zone("light.a"), 10)
    freezer.tick(timedelta(seconds=8))
    await flash.async_flash(zone("light.a", color=(5, 5, 5)), 10)
    assert light_calls["turn_off"] == []  # same light: no off between
    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert light_calls["turn_off"] == []
    assert light_calls["turn_on"][-1].data["rgb_color"] == [5, 5, 5]
    await flash.async_stop()


async def test_unavailable_or_missing_light(
    hass: HomeAssistant, light_calls, caplog
) -> None:
    flash = FlashController(hass)
    assert not await flash.async_flash(zone("light.ghost"), 10)
    assert "does not exist" in caplog.text
    hass.states.async_set("light.down", "unavailable")
    assert not await flash.async_flash(zone("light.down"), 10)
    assert "is unavailable" in caplog.text
    assert not await flash.async_flash(zone(None), 10)
    assert light_calls["turn_on"] == []


async def test_new_zone_without_light_turns_old_off(
    hass: HomeAssistant, light_calls
) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    await flash.async_flash(zone("light.a"), 10)
    assert not await flash.async_flash(zone(None), 10)
    assert light_calls["turn_off"][0].data == {"entity_id": "light.a"}
    assert flash.current is None


async def test_service_failure(hass: HomeAssistant, caplog) -> None:
    hass.states.async_set("light.a", "off")

    async def boom(call: ServiceCall) -> None:
        raise HomeAssistantError("wled offline")

    hass.services.async_register("light", "turn_on", boom)
    flash = FlashController(hass)
    assert not await flash.async_flash(zone("light.a"), 10)
    assert "wled offline" in caplog.text
    assert flash.current is None


async def test_no_light_service_at_all(hass: HomeAssistant, caplog) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    assert not await flash.async_flash(zone("light.a"), 10)
    assert "light.turn_on for light.a failed" in caplog.text


async def test_concurrent_flashes_serialized(hass: HomeAssistant, light_calls) -> None:
    for e in ("light.a", "light.b", "light.c"):
        hass.states.async_set(e, "off")
    flash = FlashController(hass)
    await asyncio.gather(
        flash.async_flash(zone("light.a"), 10),
        flash.async_flash(zone("light.b"), 10),
        flash.async_flash(zone("light.c"), 10),
    )
    assert flash.current == "light.c"
    assert [c.data["entity_id"] for c in light_calls["turn_off"]] == [
        "light.a",
        "light.b",
    ]
    await flash.async_stop()


async def test_stop(hass: HomeAssistant, light_calls, freezer) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    await flash.async_stop()  # nothing on: no call
    assert light_calls["turn_off"] == []
    await flash.async_flash(zone("light.a"), 10)
    await flash.async_stop()
    assert light_calls["turn_off"][0].data == {"entity_id": "light.a"}
    freezer.tick(timedelta(seconds=20))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(light_calls["turn_off"]) == 1


async def test_off_after_light_turned_off_externally_is_safe(
    hass: HomeAssistant, light_calls, freezer
) -> None:
    hass.states.async_set("light.a", "off")
    flash = FlashController(hass)
    await flash.async_flash(zone("light.a"), 1)
    # Simulate the off coroutine running for a stale generation.
    await flash._async_off(0, "light.a")
    assert flash.current == "light.a"
    await flash.async_stop()
