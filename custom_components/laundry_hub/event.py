"""Laundry Hub tap event entity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .entity import LaundryHubEntity
from .hub import SIGNAL_TAP

if TYPE_CHECKING:
    from . import LaundryHubConfigEntry
    from .hub import LaundryHub

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LaundryHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the tap event entity."""
    async_add_entities([TapEvent(entry.runtime_data)])


class TapEvent(LaundryHubEntity, EventEntity):
    """Fires for every processed tap."""

    _attr_translation_key = "tap"
    _attr_event_types = EVENT_TYPES
    _attr_icon = "mdi:nfc-tap"

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        super().__init__(hub, "tap")

    async def async_added_to_hass(self) -> None:
        """Listen for taps (not catalog updates)."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_TAP, self._handle_tap)
        )

    @callback
    def _handle_tap(self, event_type: str, attributes: dict[str, Any]) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
