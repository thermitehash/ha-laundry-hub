"""Removing an in-use wash bin / dry type through the generic UI is undone."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.laundry_hub.const import DOMAIN
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import bin_id, dry_id, make_entry


async def test_native_delete_of_used_zone_is_restored(
    hass: HomeAssistant, entry
) -> None:
    hub: LaundryHub = entry.runtime_data
    sid = bin_id(hub, "Reds")
    hub.enroll(uid="11223344", name="Red shirt", wash_bin=sid)
    sub = entry.subentries[sid]

    hass.config_entries.async_remove_subentry(entry, sid)
    await hass.async_block_till_done()

    assert sid in entry.subentries
    assert entry.subentries[sid].title == sub.title
    assert entry.subentries[sid].data == sub.data
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"zone_restored_{sid}")
    assert issue is not None
    assert issue.translation_placeholders == {"name": "Reds", "count": "1"}
    hub = entry.runtime_data
    assert hub.problems() == {}
    assert hass.states.get("sensor.laundry_hub_bin_reds") is not None

    # Reassign, then the issue clears and the zone can be removed.
    hub.reassign("wash_bin", "Darks", from_zone="Reds")
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"zone_restored_{sid}") is None
    hub.remove_zone(sid)
    await hass.async_block_till_done()
    assert sid not in entry.subentries
    assert hass.states.get("sensor.laundry_hub_bin_reds") is None


async def test_native_delete_of_unused_zone(hass: HomeAssistant, entry) -> None:
    hub: LaundryHub = entry.runtime_data
    sid = dry_id(hub, "Lay flat")
    hass.config_entries.async_remove_subentry(entry, sid)
    await hass.async_block_till_done()
    assert sid not in entry.subentries
    assert entry.runtime_data.zone_snapshot.keys() == entry.subentries.keys()


async def test_dangling_zone_repaired_on_startup(
    hass: HomeAssistant, hass_storage
) -> None:
    """A garment pointing at a zone that vanished while HA was down is repaired."""
    entry = make_entry()
    first_bin = next(s for s in entry.subentries.values() if s.title == "Darks")
    first_dry = next(s for s in entry.subentries.values() if s.title == "Dryer")
    hass_storage[DOMAIN] = {
        "version": 2,
        "minor_version": 1,
        "key": DOMAIN,
        "data": {
            "garments": {
                "g1": {
                    "id": "g1",
                    "uids": ["11223344"],
                    "name": "Orphan",
                    "wash_bin": "deleted-bin",
                    "dry_type": "deleted-dry",
                    "created": "2026-11-01T00:00:00+00:00",
                }
            }
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    garment = hub.catalog.garments["g1"]
    assert garment.wash_bin == first_bin.subentry_id
    assert garment.dry_type == first_dry.subentry_id
    assert hub.problems() == {}


async def test_dangling_zone_left_when_no_zones(
    hass: HomeAssistant, hass_storage
) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import OPTIONS

    entry = MockConfigEntry(domain=DOMAIN, options=OPTIONS, subentries_data=[])
    hass_storage[DOMAIN] = {
        "version": 2,
        "key": DOMAIN,
        "data": {
            "garments": {
                "g1": {
                    "id": "g1",
                    "uids": ["11223344"],
                    "name": "Orphan",
                    "wash_bin": "x",
                    "dry_type": "y",
                    "created": "2026-11-01T00:00:00+00:00",
                }
            }
        },
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    assert hub.catalog.garments["g1"].wash_bin == "x"
    assert hub.problems()["g1"]
