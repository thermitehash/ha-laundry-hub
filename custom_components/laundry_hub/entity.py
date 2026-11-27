"""Base entity for Laundry Hub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, SIGNAL_UPDATE, VERSION

if TYPE_CHECKING:
    from .hub import LaundryHub


class LaundryHubEntity(Entity):
    """Entity refreshed whenever the catalog changes."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub: LaundryHub, key: str) -> None:
        """Initialize."""
        self.hub = hub
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.entry.entry_id)},
            name=NAME,
            manufacturer="Laundry Hub",
            model="Garment tracker",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to catalog updates."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
