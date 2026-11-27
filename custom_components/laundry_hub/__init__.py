"""Laundry Hub: NFC garment tracking and laundry sorting lights."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .hub import LaundryHub
from .media import async_register_hidden_source
from .panel import async_register_panel, async_unregister_panel
from .services import async_setup_services
from .websocket import async_setup_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.EVENT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type LaundryHubConfigEntry = ConfigEntry[LaundryHub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services and websocket commands (once)."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LaundryHubConfigEntry) -> bool:
    """Set up Laundry Hub from a config entry."""
    hub = LaundryHub(hass, entry)
    await hub.async_setup()
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass)
    if "media_source" in hass.config.components:
        async_register_hidden_source(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LaundryHubConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
        async_unregister_panel(hass)
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: LaundryHubConfigEntry
) -> None:
    """Options or subentries changed: guard removals, then reload."""
    hub = entry.runtime_data
    if hub.restore_removed_zones():
        # Re-adding fires this listener again; that call does the reload.
        return
    await hass.config_entries.async_reload(entry.entry_id)
