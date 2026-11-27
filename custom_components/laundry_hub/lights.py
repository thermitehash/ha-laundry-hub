"""Sorting-light flashing (WLED segments exposed as HA lights)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .models import Zone

_LOGGER = logging.getLogger(__name__)

LIGHT_CALL_TIMEOUT = 5.0


class FlashController:
    """Flash one zone at a time; a new flash replaces the current one."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self.current: str | None = None
        self._cancel_off: CALLBACK_TYPE | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    def _cancel_timer(self) -> None:
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None

    async def _call(self, service: str, data: dict[str, object]) -> bool:
        """Call a light service; never raises."""
        try:
            async with asyncio.timeout(LIGHT_CALL_TIMEOUT):
                await self.hass.services.async_call(
                    "light", service, data, blocking=True
                )
        except (HomeAssistantError, TimeoutError, ValueError) as err:
            _LOGGER.warning(
                "light.%s for %s failed: %s", service, data.get("entity_id"), err
            )
            return False
        return True

    def _usable(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning("Light %s does not exist; skipping flash", entity_id)
            return False
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            _LOGGER.warning("Light %s is %s; skipping flash", entity_id, state.state)
            return False
        return True

    async def async_flash(self, zone: Zone, duration: float) -> bool:
        """Turn on the zone's light in its color for `duration` seconds."""
        async with self._lock:
            self._generation += 1
            generation = self._generation
            self._cancel_timer()
            previous = self.current
            self.current = None
            target = zone.light_entity
            if previous is not None and previous != target:
                await self._call("turn_off", {"entity_id": previous})
            if target is None:
                _LOGGER.debug("Zone %s has no light configured", zone.name)
                return False
            if not self._usable(target):
                return False
            ok = await self._call(
                "turn_on",
                {"entity_id": target, "rgb_color": list(zone.color)},
            )
            if not ok:
                return False
            self.current = target
            self._cancel_off = async_call_later(
                self.hass, duration, self._make_off_callback(generation, target)
            )
            return True

    def _make_off_callback(
        self, generation: int, entity_id: str
    ) -> Callable[[datetime], None]:
        @callback
        def _off(_now: datetime) -> None:
            self._cancel_off = None
            if generation != self._generation:
                return
            self.hass.async_create_background_task(
                self._async_off(generation, entity_id),
                f"laundry_hub flash off {entity_id}",
            )

        return _off

    async def _async_off(self, generation: int, entity_id: str) -> None:
        async with self._lock:
            if generation != self._generation or self.current != entity_id:
                return
            self.current = None
            await self._call("turn_off", {"entity_id": entity_id})

    async def async_stop(self) -> None:
        """Cancel any flash and turn the light off (unload)."""
        async with self._lock:
            self._generation += 1
            self._cancel_timer()
            current, self.current = self.current, None
            if current is not None:
                await self._call("turn_off", {"entity_id": current})
