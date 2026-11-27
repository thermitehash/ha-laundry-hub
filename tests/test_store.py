"""Storage: migration, corruption handling, persistence."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.laundry_hub.const import DOMAIN, STORAGE_VERSION
from custom_components.laundry_hub.hub import LaundryHub
from custom_components.laundry_hub.models import Garment, InvalidUid, normalize_uid
from custom_components.laundry_hub.store import (
    MAX_UNKNOWN_TAGS,
    Catalog,
    DuplicateUid,
    LastTag,
    UnknownGarment,
    migrate_v1,
)

from .conftest import bin_id, make_entry

V1 = {
    "garments": [
        {
            "id": "abc",
            "uid": "04A1B2C3D4E5F6",
            "name": "Hoodie",
            "owner": "person.alex",
            "bin": "bin1",
            "dry": "dry1",
            "notes": "wool",
            "state": "put_away",
            "location": "closet_1",
            "last_seen": "2026-11-01T10:00:00+00:00",
            "last_station": "closet_1",
            "created": "2026-10-01T10:00:00+00:00",
        },
        {
            "uid": "11223344",
            "name": "Sock",
            "bin": "bin1",
            "dry": "dry1",
            "created": "2026-10-01T10:00:00+00:00",
        },
        {"uid": "broken"},
    ],
    "last_unknown": {"uid": "AABBCCDD", "station": "laundry", "time": "x"},
}


def test_migrate_v1() -> None:
    data = migrate_v1(V1)
    hoodie = data["garments"]["abc"]
    assert hoodie["uids"] == ["04A1B2C3D4E5F6"]
    assert hoodie["wash_bin"] == "bin1"
    assert hoodie["dry_type"] == "dry1"
    assert hoodie["last_seen"] == {
        "time": "2026-11-01T10:00:00+00:00",
        "station": "closet_1",
    }
    assert Garment.from_dict(hoodie).owner == "person.alex"
    sock = next(g for g in data["garments"].values() if g["name"] == "Sock")
    assert sock["state"] == "new"
    assert sock["last_seen"] is None
    assert data["quarantine"] == [{"uid": "broken"}]
    assert data["last_unknown"]["uid"] == "AABBCCDD"
    assert data["unknown_tags"] == {}


async def test_store_migration_on_load(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[DOMAIN] = {"version": 1, "minor_version": 1, "key": DOMAIN, "data": V1}
    catalog = Catalog(hass)
    await catalog.async_load()
    assert set(catalog.uid_index) == {"04A1B2C3D4E5F6", "11223344"}
    assert len(catalog.quarantine) == 1
    await catalog.async_flush()
    assert hass_storage[DOMAIN]["version"] == STORAGE_VERSION
    assert "abc" in hass_storage[DOMAIN]["data"]["garments"]


async def test_corrupt_records_quarantined(hass: HomeAssistant, hass_storage) -> None:
    good = {
        "id": "g1",
        "uids": ["11223344"],
        "name": "A",
        "wash_bin": "b",
        "dry_type": "d",
        "created": "2026-11-01T00:00:00+00:00",
    }
    hass_storage[DOMAIN] = {
        "version": 2,
        "key": DOMAIN,
        "data": {
            "garments": {
                "g1": good,
                "g2": {**good, "id": "g2"},  # duplicate UID
                "g3": {**good, "id": "g3", "uids": ["zz"]},  # bad uid
                "g4": {
                    **good,
                    "id": "g4",
                    "uids": ["55667788"],
                    "created": "2026-11-01T00:00:00",
                },  # naive
                "g5": {"id": "g5"},  # missing keys
                "g6": {
                    **good,
                    "id": "g6",
                    "uids": ["99887766"],
                    "last_seen": {"time": "garbage"},
                },
            },
            "last_unknown": "not a dict",
            "unknown_tags": ["not", "a", "dict"],
        },
    }
    catalog = Catalog(hass)
    await catalog.async_load()
    assert list(catalog.garments) == ["g1"]
    assert len(catalog.quarantine) == 5
    assert catalog.last_unknown is None
    assert catalog.unknown_tags == {}


async def test_unknown_cleared_if_enrolled(hass: HomeAssistant, hass_storage) -> None:
    hass_storage[DOMAIN] = {
        "version": 2,
        "key": DOMAIN,
        "data": {
            "garments": {
                "g1": {
                    "id": "g1",
                    "uids": ["11223344"],
                    "name": "A",
                    "wash_bin": "b",
                    "dry_type": "d",
                    "created": "2026-11-01T00:00:00+00:00",
                }
            },
            "unknown_tags": {
                "11223344": {"uid": "11223344"},
                "AABBCCDD": {"uid": "AABBCCDD"},
            },
        },
    }
    catalog = Catalog(hass)
    await catalog.async_load()
    assert list(catalog.unknown_tags) == ["AABBCCDD"]


async def test_empty_storage(hass: HomeAssistant) -> None:
    catalog = Catalog(hass)
    await catalog.async_load()
    assert catalog.garments == {}


async def test_debounced_save_and_restart(
    hass: HomeAssistant, hass_storage, freezer: FrozenDateTimeFactory
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    hub: LaundryHub = entry.runtime_data
    garment = hub.enroll(uid="11223344", name="Shirt", wash_bin=bin_id(hub, "Colors"))
    assert DOMAIN not in hass_storage  # debounced, not yet written
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    stored = hass_storage[DOMAIN]["data"]["garments"][garment.id]
    assert stored["name"] == "Shirt"

    # Change then unload immediately: flushed on unload.
    hub.update(garment.id, {"name": "Shirt 2"})
    await hass.config_entries.async_unload(entry.entry_id)
    assert hass_storage[DOMAIN]["data"]["garments"][garment.id]["name"] == "Shirt 2"

    # Restart: data round-trips including timestamps.
    await hass.config_entries.async_setup(entry.entry_id)
    hub = entry.runtime_data
    loaded = hub.catalog.garments[garment.id]
    assert loaded.name == "Shirt 2"
    assert loaded.created == garment.created
    assert loaded.created.tzinfo is not None


async def test_catalog_errors(hass: HomeAssistant) -> None:
    catalog = Catalog(hass)
    garment = catalog.add(uid="11223344", name="A", wash_bin="b", dry_type="d")
    with pytest.raises(DuplicateUid):
        catalog.add(uid="11:22:33:44", name="B", wash_bin="b", dry_type="d")
    with pytest.raises(UnknownGarment):
        catalog.get("nope")
    with pytest.raises(LastTag):
        catalog.remove_uid(garment.id, "11223344")
    catalog.add_uid(garment.id, "55667788")
    catalog.add_uid(garment.id, "55667788")  # idempotent
    assert garment.uids == ["11223344", "55667788"]
    catalog.remove_uid(garment.id, "11223344")
    catalog.remove_uid(garment.id, "99999999")  # not attached: no-op
    assert catalog.by_uid("11223344") is None
    assert catalog.by_uid("55667788") is garment
    other = catalog.add(uid="11223344", name="C", wash_bin="b", dry_type="d")
    with pytest.raises(DuplicateUid):
        catalog.add_uid(other.id, "55667788")
    catalog.remove(other.id)
    assert catalog.by_uid("11223344") is None


async def test_unknown_tags_capped(hass: HomeAssistant, freezer) -> None:
    catalog = Catalog(hass)
    for i in range(MAX_UNKNOWN_TAGS + 5):
        freezer.tick(timedelta(seconds=1))
        catalog.record_unknown(f"{i:08X}", "laundry", dt_util.utcnow())
    assert len(catalog.unknown_tags) == MAX_UNKNOWN_TAGS
    assert "00000000" not in catalog.unknown_tags  # oldest evicted
    catalog.dismiss_unknown(f"{MAX_UNKNOWN_TAGS + 4:08X}")
    assert catalog.last_unknown is None
    catalog.dismiss_unknown("FFFFFFFF")  # unknown uid: fine


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("04a1b2c3", "04A1B2C3"),
        ("04-A1-B2-C3-D4-E5-F6", "04A1B2C3D4E5F6"),
        ("04:a1:b2:c3:d4:e5:f6:00:11:22", "04A1B2C3D4E5F6001122"),
        (" 04 A1 B2 C3 ", "04A1B2C3"),
    ],
)
def test_normalize_uid(raw: str, expected: str) -> None:
    assert normalize_uid(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "XYZ", "04A1B2", "04A1B2C3D4", "04A1B2C", 1234, None]
)
def test_normalize_uid_invalid(raw: object) -> None:
    with pytest.raises(InvalidUid):
        normalize_uid(raw)


async def test_inconsistent_states_repaired_on_load(
    hass: HomeAssistant, hass_storage
) -> None:
    """Regression: an invalid stored state made every tap answer error."""
    base = {
        "name": "A",
        "wash_bin": "b",
        "dry_type": "d",
        "created": "2026-11-01T00:00:00+00:00",
    }
    garments = {
        "g1": {**base, "id": "g1", "uids": ["11111111"], "state": "missing"},
        "g2": {
            **base,
            "id": "g2",
            "uids": ["22222222"],
            "state": "put_away",
            "location": None,
        },
        "g3": {
            **base,
            "id": "g3",
            "uids": ["33333333"],
            "state": "drying",
            "location": "closet_1",
        },
        "g4": {
            **base,
            "id": "g4",
            "uids": ["44444444"],
            "state": "new",
            "location": "closet_2",
        },
        "g5": {
            **base,
            "id": "g5",
            "uids": ["55555555"],
            "state": "put_away",
            "location": "closet_1",
            "last_seen": {"time": "2026-11-02T00:00:00+00:00", "station": "garage"},
        },
    }
    hass_storage[DOMAIN] = {"version": 2, "key": DOMAIN, "data": {"garments": garments}}
    catalog = Catalog(hass)
    await catalog.async_load()
    g = catalog.garments
    assert (g["g1"].state, g["g1"].location) == ("new", None)
    assert (g["g2"].state, g["g2"].location) == ("new", None)
    assert (g["g3"].state, g["g3"].location) == ("drying", "laundry")
    assert (g["g4"].state, g["g4"].location) == ("new", None)
    assert (g["g5"].state, g["g5"].last_seen_station) == ("put_away", None)
    from custom_components.laundry_hub.models import garment_problems

    for garment in g.values():
        assert garment_problems(garment, {"b", "d"}) == []
