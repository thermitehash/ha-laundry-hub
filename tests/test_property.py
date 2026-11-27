"""Property tests: random tap / edit sequences keep the catalog consistent."""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import slugify
from hypothesis import HealthCheck, given, settings, strategies as st
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.laundry_hub.const import (
    CLOSET_STATIONS,
    DOMAIN,
    STATE_DRYING,
    STATE_IN_WASH,
    STATE_PUT_AWAY,
    STATIONS,
    STORED_STATES,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
    TAP_EVENT,
)
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import make_entry

UIDS = [f"0{i}A1B2C3" for i in range(6)] + ["DEADBEEF", "CAFEBABE"]

op = st.one_of(
    st.tuples(
        st.just("tap"),
        st.sampled_from(STATIONS),
        st.sampled_from(UIDS),
        st.sampled_from(["auto", "sort", "dry", "bogus"]),
    ),
    st.tuples(st.just("time"), st.integers(min_value=0, max_value=60 * 24 * 20)),
    st.tuples(
        st.just("enroll"), st.sampled_from(UIDS), st.integers(0, 10), st.integers(0, 10)
    ),
    st.tuples(st.just("remove_garment"), st.sampled_from(UIDS)),
    st.tuples(
        st.just("add_zone"),
        st.sampled_from([SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE]),
        st.integers(0, 99),
    ),
    st.tuples(st.just("remove_zone"), st.integers(0, 20)),
    st.tuples(st.just("native_remove_zone"), st.integers(0, 20)),
    st.tuples(st.just("edit_zone"), st.integers(0, 20), st.integers(0, 99)),
    st.tuples(
        st.just("reassign"),
        st.sampled_from([SUBENTRY_WASH_BIN, SUBENTRY_DRY_TYPE]),
        st.integers(0, 20),
        st.integers(0, 20),
    ),
    st.tuples(
        st.just("set_location"),
        st.sampled_from(UIDS),
        st.sampled_from([*CLOSET_STATIONS, STATE_IN_WASH, STATE_DRYING]),
    ),
)


def check_invariants(hass: HomeAssistant, hub: LaundryHub) -> None:
    assert hub.problems() == {}, hub.problems()
    zones = hub.zones
    for g in hub.catalog.garments.values():
        assert g.state in STORED_STATES
        assert zones[g.wash_bin].type == SUBENTRY_WASH_BIN
        assert zones[g.dry_type].type == SUBENTRY_DRY_TYPE
        for uid in g.uids:
            assert hub.catalog.uid_index[uid] == g.id
    assert len(hub.catalog.uid_index) == sum(
        len(g.uids) for g in hub.catalog.garments.values()
    )
    garments = list(hub.catalog.garments.values())
    for closet in CLOSET_STATIONS:
        expected = sum(
            1 for g in garments if g.state == STATE_PUT_AWAY and g.location == closet
        )
        assert hass.states.get(f"sensor.laundry_hub_{closet}_count").state == str(
            expected
        )
    in_wash = [g for g in garments if g.state == STATE_IN_WASH]
    drying = [g for g in garments if g.state == STATE_DRYING]
    assert hass.states.get("sensor.laundry_hub_in_wash").state == str(len(in_wash))
    assert hass.states.get("sensor.laundry_hub_drying").state == str(len(drying))
    assert hass.states.get("sensor.laundry_hub_missing").state == str(
        len(hub.missing())
    )
    bins = hass.states.get("sensor.laundry_hub_in_wash").attributes["bin_ids"]
    assert sum(bins.values()) == len(in_wash)
    for zone in hub.zones_of(SUBENTRY_WASH_BIN):
        expected = sum(1 for g in in_wash if g.wash_bin == zone.id)
        assert bins[zone.id] == expected
        sensor = hass.states.get(f"sensor.laundry_hub_bin_{slugify(zone.name)}")
        assert sensor is not None, zone.name
        assert sensor.state == str(expected)
    assert hass.states.get("sensor.laundry_hub_unknown_tags").state == str(
        len(hub.catalog.unknown_tags)
    )


def _zone(hub: LaundryHub, index: int) -> Any:
    zones = sorted(hub.zones.values(), key=lambda z: z.id)
    return zones[index % len(zones)] if zones else None


async def run_ops(
    hass: HomeAssistant, freezer: Any, light_calls: Any, ops: list[tuple]
) -> None:
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    counter = 0
    try:
        for operation in ops:
            hub: LaundryHub = entry.runtime_data
            kind = operation[0]
            if kind == "tap":
                _, station, uid, mode = operation
                hass.bus.async_fire(
                    TAP_EVENT, {"station_id": station, "uid": uid, "mode": mode}
                )
            elif kind == "time":
                freezer.tick(timedelta(minutes=operation[1]))
                # Missing is time-derived; the integration refreshes hourly.
                freezer.tick(timedelta(hours=1))
                async_fire_time_changed(hass)
            elif kind == "enroll":
                _, uid, b, d = operation
                bins, dries = (
                    hub.zones_of(SUBENTRY_WASH_BIN),
                    hub.zones_of(SUBENTRY_DRY_TYPE),
                )
                try:
                    hub.enroll(
                        uid=uid,
                        name=f"g{uid}",
                        wash_bin=bins[b % len(bins)].id if bins else None,
                        dry_type=dries[d % len(dries)].id if dries else None,
                    )
                except ServiceValidationError:
                    pass  # duplicate UID / no zones
            elif kind == "remove_garment":
                try:
                    await hub.async_remove(operation[1])
                except ServiceValidationError:
                    pass
            elif kind == "add_zone":
                counter += 1
                _, zone_type, order = operation
                name = f"Z{counter}"
                hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(
                            {
                                "name": name,
                                "color": [1, 2, 3],
                                "light_entity": None,
                                "order": order,
                            }
                        ),
                        subentry_type=zone_type,
                        title=name,
                        unique_id=None,
                    ),
                )
            elif kind == "remove_zone":
                zone = _zone(hub, operation[1])
                if zone is not None:
                    try:
                        hub.remove_zone(zone.id)
                    except ServiceValidationError:
                        assert hub.catalog.usage(zone.id) > 0
            elif kind == "native_remove_zone":
                zone = _zone(hub, operation[1])
                if zone is not None:
                    hass.config_entries.async_remove_subentry(entry, zone.id)
            elif kind == "edit_zone":
                zone = _zone(hub, operation[1])
                if zone is not None:
                    sub = entry.subentries[zone.id]
                    hass.config_entries.async_update_subentry(
                        entry, sub, data={**sub.data, "order": operation[2]}
                    )
            elif kind == "reassign":
                _, field, src, dst = operation
                zones = hub.zones_of(field)
                if zones:
                    try:
                        hub.reassign(
                            field,
                            zones[dst % len(zones)].id,
                            from_zone=zones[src % len(zones)].id,
                        )
                    except ServiceValidationError:
                        raise AssertionError("valid reassign rejected") from None
            elif kind == "set_location":
                try:
                    hub.set_location(operation[1], operation[2])
                except ServiceValidationError:
                    assert hub.catalog.by_uid(operation[1]) is None
            await hass.async_block_till_done()
            check_invariants(hass, entry.runtime_data)
    finally:
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(ops=st.lists(op, min_size=1, max_size=40))
async def test_random_sequences_keep_invariants(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: Any,
    light_calls: Any,
    ops: list[tuple],
) -> None:
    hass_storage.pop(DOMAIN, None)
    await run_ops(hass, freezer, light_calls, ops)


async def test_regression_restore_then_reload(
    hass: HomeAssistant, freezer, light_calls
) -> None:
    """Native removal of every zone while garments exist."""
    ops = [("enroll", UIDS[0], 0, 0), ("tap", "laundry", UIDS[0], "auto")]
    ops += [("native_remove_zone", i) for i in range(10)]
    ops += [("tap", "laundry", UIDS[0], "auto")]
    await run_ops(hass, freezer, light_calls, ops)
