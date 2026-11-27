"""Setup/unload tests."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.laundry_hub.const import DOMAIN


async def test_setup_unload(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.laundry_hub_in_wash").state == "0"
    assert hass.states.get("sensor.laundry_hub_closet_1_count").state == "0"
    assert hass.states.get("sensor.laundry_hub_bin_darks") is not None
    assert hass.states.get("event.laundry_hub_tap") is not None
    assert hass.services.has_service(DOMAIN, "enroll_garment")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
