"""Laundry Hub sensors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import (
    CLOSET_STATIONS,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_PUT_AWAY,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from .entity import LaundryHubEntity

if TYPE_CHECKING:
    from . import LaundryHubConfigEntry
    from .hub import LaundryHub

PARALLEL_UPDATES = 0
UNIT = "garments"
MAX_LIST = 50  # keep attributes small


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LaundryHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors (main + one per wash bin subentry)."""
    hub = entry.runtime_data
    entities: list[SensorEntity] = [ClosetCountSensor(hub, c) for c in CLOSET_STATIONS]
    entities += [
        InWashSensor(hub),
        DryingSensor(hub),
        MissingSensor(hub),
        UnknownTagsSensor(hub),
    ]
    bins = [
        BinSensor(hub, zone.id, zone.name) for zone in hub.zones_of(SUBENTRY_WASH_BIN)
    ]
    _prune_bin_sensors(hass, entry, {b.unique_id for b in bins})
    async_add_entities([*entities, *bins])


def _prune_bin_sensors(
    hass: HomeAssistant, entry: LaundryHubConfigEntry, keep: set[str | None]
) -> None:
    """Remove registry entries of bin sensors whose wash bin no longer exists.

    Bin sensors are deliberately not linked to their subentry: Home Assistant's
    subentry removal would delete the registry entry asynchronously, racing the
    restore of an in-use bin. Lifecycle is managed here instead.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_bin_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id.startswith(prefix) and reg_entry.unique_id not in keep:
            registry.async_remove(reg_entry.entity_id)


class _CountSensor(LaundryHubEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT


class ClosetCountSensor(_CountSensor):
    """Garments put away in a closet."""

    _attr_icon = "mdi:wardrobe"

    def __init__(self, hub: LaundryHub, closet: str) -> None:
        """Initialize."""
        super().__init__(hub, f"{closet}_count")
        self._closet = closet
        self._attr_translation_key = f"{closet}_count"

    @property
    def native_value(self) -> int:
        """Count."""
        return self.hub.count(STATE_PUT_AWAY, self._closet)


class InWashSensor(_CountSensor):
    """Garments in the wash, with per-bin counts."""

    _attr_translation_key = "in_wash"
    _attr_icon = "mdi:washing-machine"

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        super().__init__(hub, "in_wash")

    @property
    def native_value(self) -> int:
        """Count."""
        return self.hub.count(STATE_IN_WASH)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-bin counts and names."""
        zones = {z.id: z.name for z in self.hub.zones_of(SUBENTRY_WASH_BIN)}
        counts = self.hub.per_zone(STATE_IN_WASH, SUBENTRY_WASH_BIN)
        return {
            "bins": {zones[zid]: n for zid, n in counts.items()},
            "bin_ids": counts,
        }


class DryingSensor(_CountSensor):
    """Garments drying, with per-dry-type counts."""

    _attr_translation_key = "drying"
    _attr_icon = "mdi:tumble-dryer"

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        super().__init__(hub, "drying")

    @property
    def native_value(self) -> int:
        """Count."""
        return self.hub.count(STATE_DRYING)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-dry-type counts."""
        zones = {z.id: z.name for z in self.hub.zones_of(SUBENTRY_DRY_TYPE)}
        counts = self.hub.per_zone(STATE_DRYING, SUBENTRY_DRY_TYPE)
        return {"dry_types": {zones[zid]: n for zid, n in counts.items()}}


class MissingSensor(_CountSensor):
    """Garments not seen for the configured number of days."""

    _attr_translation_key = "missing"
    _attr_icon = "mdi:tshirt-crew-outline"

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        super().__init__(hub, "missing")

    @property
    def native_value(self) -> int:
        """Count."""
        return len(self.hub.missing())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Missing garments (capped)."""
        missing = sorted(self.hub.missing(), key=lambda g: g.name.lower())
        return {
            "garments": [
                {
                    "id": g.id,
                    "name": g.name,
                    "owner": g.owner,
                    "last_seen": g.last_seen.isoformat() if g.last_seen else None,
                }
                for g in missing[:MAX_LIST]
            ],
            "truncated": len(missing) > MAX_LIST,
        }


class UnknownTagsSensor(_CountSensor):
    """Unknown tags seen and not yet enrolled."""

    _attr_translation_key = "unknown_tags"
    _attr_icon = "mdi:tag-search"
    _attr_native_unit_of_measurement = "tags"

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        super().__init__(hub, "unknown_tags")

    @property
    def native_value(self) -> int:
        """Count."""
        return len(self.hub.catalog.unknown_tags)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Last unknown tag."""
        return {"last_unknown": self.hub.catalog.last_unknown}


class BinSensor(_CountSensor):
    """Garments currently in one wash bin (created/removed with the subentry)."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:basket"

    def __init__(self, hub: LaundryHub, zone_id: str, name: str) -> None:
        """Initialize."""
        super().__init__(hub, f"bin_{zone_id}")
        self._zone_id = zone_id
        self._attr_name = f"Laundry Hub bin {name}"
        self._attr_device_info = None
        self.entity_id = f"sensor.laundry_hub_bin_{slugify(name)}"

    @property
    def native_value(self) -> int:
        """Count."""
        return self.hub.per_zone(STATE_IN_WASH, SUBENTRY_WASH_BIN).get(self._zone_id, 0)
