"""v0.2.8: garments without a tag, tag linking, manual states."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
import pytest

from custom_components.laundry_hub.const import (
    STATE_IN_WASH,
    STATE_PUT_AWAY,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
)
from custom_components.laundry_hub.hub import state_confidence

from .test_app_api import ALEX, SAM, Client
from .test_v026 import setup, stored_card

CLIENT_ID = "https://example.com/app"


@pytest.fixture(autouse=True)
def _now(freezer) -> None:
    freezer.move_to("2026-11-29 12:00:00+00:00")


@pytest.fixture
async def kid(hass: HomeAssistant) -> Any:
    """A non-admin household member linked to person.alex."""
    user = await hass.auth.async_create_user("Alex Kid", group_ids=["system-users"])
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return user, hass.auth.async_create_access_token(refresh)


def zones(hub: Any) -> tuple[str, str]:
    bin_id = next(z.id for z in hub.zones.values() if z.type == "wash_bin")
    dry_id = next(z.id for z in hub.zones.values() if z.type == "dry_type")
    return bin_id, dry_id


# --- migration ---------------------------------------------------------------------


async def test_existing_garments_keep_ids_tags_photos_and_history(
    hass: HomeAssistant, hass_ws_client
) -> None:
    hub = await setup(hass)
    garment = hub.enroll(uid="04A1B2C3", name="Old hoodie", owner=SAM)
    garment.photos = [f"{garment.id}-a.jpg"]
    garment.wear_log = [{"time": "2026-11-01T08:00:00+00:00"}]
    await hub.async_handle_tap("closet_1", "04A1B2C3")
    taps = [dict(t) for t in garment.recent_taps]
    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, hub.entry.entry_id)
    }
    entry = hub.entry
    assert await hass.config_entries.async_unload(entry.entry_id)  # flushes
    # Rewrite the stored garment as v0.2.7 wrote it (no v0.2.8 keys) and reload.
    store = Store(hass, 3, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION)
    raw = await store.async_load()
    for key in ("state_source", "state_updated"):
        raw["garments"][garment.id].pop(key)
    before = dict(raw["garments"][garment.id])
    await store.async_save(raw)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub = entry.runtime_data
    loaded = hub.catalog.garments[garment.id]
    assert (loaded.id, loaded.uids, loaded.photos) == (
        garment.id,
        ["04A1B2C3"],
        [f"{garment.id}-a.jpg"],
    )
    assert loaded.recent_taps == taps and loaded.wear_log == garment.wear_log
    assert loaded.tagged and loaded.state_source is None
    after = loaded.to_dict()
    assert {
        k: v for k, v in after.items() if k not in ("state_source", "state_updated")
    } == before
    assert {
        e.unique_id
        for e in er.async_entries_for_config_entry(registry, hub.entry.entry_id)
    } == unique_ids
    # Old uid-style refs still work, and answers carry the id as well.
    client = Client(await hass_ws_client(hass))
    res = (await client("garment/update", garment_id="04:A1:B2:C3", notes="kept"))[
        "result"
    ]
    assert (
        res["id"] == garment.id
        and res["uids"] == ["04A1B2C3"]
        and res["tagged"] is True
    )
    info = (await client("api_info"))["result"]
    assert "untagged_garments" in info["capabilities"] and info["api_version"] == 1


# --- creating untagged garments -----------------------------------------------------


async def test_create_untagged_from_the_panel_and_from_a_purchase_card(
    hass: HomeAssistant, hass_ws_client
) -> None:
    hub = await setup(hass)
    bin_id, dry_id = zones(hub)
    client = Client(await hass_ws_client(hass))
    res = (
        await client(
            "garment/create",
            name="Linen shirt",
            wash_bin=bin_id,
            dry_type=dry_id,
            owner=SAM,
        )
    )["result"]
    assert res["uids"] == [] and res["tagged"] is False and res["state"] == "new"
    assert res["missing"] is False and res["state_confidence"] == 0.5
    bad = await client(
        "garment/create",
        name="X",
        wash_bin=bin_id,
        dry_type=dry_id,
        location="closet_1",
    )
    assert bad["error"]["code"] == "untagged_location"
    # "New garment from this": untagged by default, filled from the card.
    hub.purchases.cards["c1"] = stored_card(
        "c1", "Wool Socks", "lands_end", quantity=3, brand="Darn Tough", size="M"
    )
    made = (
        await client(
            "garment/create", name="Wool Socks", wash_bin=bin_id, dry_type=dry_id
        )
    )["result"]
    matched = (
        await client("purchases/match", card_id="c1", garment_id=made["id"], units=1)
    )["result"]
    assert matched["tagged"] is False and matched["brand"] == "Darn Tough"
    assert hub.purchases.view(hub.purchases.cards["c1"])["remaining"] == 2
    # Same wardrobe fields as tagged garments.
    res = (await client("garment/wardrobe", garment_id=made["id"], wear_for="work"))[
        "result"
    ]
    assert res["wear_for"] == "work"


# --- manual states, counts and freshness ---------------------------------------------


async def test_manual_states_counts_and_freshness(
    hass: HomeAssistant, hass_ws_client, freezer
) -> None:
    hub = await setup(hass)
    bin_id, dry_id = zones(hub)
    client = Client(await hass_ws_client(hass))
    shirt = hub.enroll(uid=None, name="Linen shirt", wash_bin=bin_id, dry_type=dry_id)
    tagged = hub.enroll(
        uid="11223344", name="Tagged tee", wash_bin=bin_id, dry_type=dry_id
    )
    res = (
        await client("garment/manual_state", garment_id=shirt.id, action="to_laundry")
    )["result"]
    assert (res["state"], res["location"], res["state_source"]) == (
        STATE_IN_WASH,
        None,
        "manual",
    )
    assert res["recent_taps"][-1]["station"] == "manual"
    # Station counts and bin entities ignore untagged garments.
    assert hub.count(STATE_IN_WASH) == 0 and set(
        hub.per_zone(STATE_IN_WASH, "wash_bin").values()
    ) == {0}
    await hub.async_handle_tap("laundry", "11223344", "sort")
    await hass.async_block_till_done()
    assert hub.count(STATE_IN_WASH) == 1
    state = hass.states.get("sensor.laundry_hub_in_wash")
    assert state is not None and state.state == "1"
    res = (
        await client("garment/manual_state", garment_id=shirt.id, action="put_away")
    )["result"]
    assert (res["state"], res["location"]) == (
        STATE_PUT_AWAY,
        None,
    )  # never a made-up closet
    refused = await client(
        "garment/manual_state", garment_id=tagged.id, action="put_away"
    )
    assert refused["error"]["code"] == "garment_has_tag"
    # A hand-set state fades: fresh for 2 days, a quarter after 30, tags always trusted.
    now = dt_util.utcnow()
    assert state_confidence(shirt, now) == 1.0
    assert 0.25 < state_confidence(shirt, now + timedelta(days=16)) < 1.0
    assert state_confidence(shirt, now + timedelta(days=40)) == 0.25
    assert state_confidence(tagged, now + timedelta(days=400)) == 1.0
    # Never missing, whatever the time.
    freezer.tick(timedelta(days=60))
    assert hub.is_missing(shirt) is False and hub.is_missing(tagged) is True
    assert shirt not in hub.missing()


# --- attach, move, replace, remove ---------------------------------------------------


async def test_attach_move_replace_remove_and_the_two_minute_window(
    hass: HomeAssistant, hass_ws_client, freezer
) -> None:
    hub = await setup(hass)
    bin_id, dry_id = zones(hub)
    client = Client(await hass_ws_client(hass))
    shirt = hub.enroll(uid=None, name="Linen shirt", wash_bin=bin_id, dry_type=dry_id)
    other = hub.enroll(
        uid="55667788", name="Red sweater", wash_bin=bin_id, dry_type=dry_id
    )
    # Attach: the next unknown tap at any station links within 2 minutes.
    res = (await client("garment/attach_tag", garment_id=shirt.id))["result"]
    assert res["linked"] is False and res["waiting_until"]
    assert (await client("data"))["result"]["link"]["garment_id"] == shirt.id
    freezer.tick(timedelta(seconds=90))
    await hub.async_handle_tap("closet_2", "AABBCCDD")
    assert shirt.uids == ["AABBCCDD"] and "AABBCCDD" not in hub.catalog.unknown_tags
    assert hub.link_status() is None
    # The window closes after 2 minutes: the tap is just an unknown tag.
    await client("garment/attach_tag", garment_id=other.id)
    freezer.tick(timedelta(seconds=121))
    await hub.async_handle_tap("closet_2", "99887766")
    assert "99887766" in hub.catalog.unknown_tags and other.uids == ["55667788"]
    # A tag on another garment is refused, with Move tag here as the way out.
    taken = await client("garment/attach_tag", garment_id=shirt.id, uid="55667788")
    assert (
        taken["error"]["code"] == "tag_in_use"
        and "Red sweater" in taken["error"]["message"]
    )
    moved = (
        await client(
            "garment/attach_tag", garment_id=shirt.id, uid="55667788", move=True
        )
    )["result"]
    assert moved["garment"]["uids"] == ["AABBCCDD", "55667788"]
    assert other.uids == [] and not other.tagged and other.location is None
    # Replace (a lost tag): the old tags go, the new one stays.
    await client("garment/attach_tag", garment_id=shirt.id, replace=True)
    await hub.async_handle_tap("laundry", "0A0B0C0D")
    assert shirt.uids == ["0A0B0C0D"]
    await client("garment/attach_cancel", garment_id=shirt.id)
    # Remove: back to untagged; the history is kept.
    history = list(shirt.recent_taps)
    res = (await client("garment/remove_tag", garment_id=shirt.id))["result"]
    assert res["tagged"] is False and res["uids"] == [] and shirt.recent_taps == history
    assert hub.catalog.by_uid("0A0B0C0D") is None


async def test_unknown_tag_links_to_an_untagged_garment(
    hass: HomeAssistant, hass_ws_client, kid
) -> None:
    hub = await setup(hass, app_access="household")
    hass.states.async_set(ALEX, "home", {"user_id": kid[0].id})
    bin_id, dry_id = zones(hub)
    sams = hub.enroll(
        uid=None, name="Sam scarf", wash_bin=bin_id, dry_type=dry_id, owner=SAM
    )
    alexs = hub.enroll(
        uid=None, name="Alex hat", wash_bin=bin_id, dry_type=dry_id, owner=ALEX
    )
    hub.enroll(
        uid="12121212", name="Tagged", wash_bin=bin_id, dry_type=dry_id, owner=ALEX
    )
    member = Client(await hass_ws_client(hass, kid[1]))
    admin = Client(await hass_ws_client(hass))
    # Alex taps a new tag with the phone: Alex's untagged garments come first.
    tapped = (await member("tap", uid="ABCD1234", source="phone", device_name="Alex"))[
        "result"
    ]
    assert tapped["known"] is False
    ranked = (await admin("unknown/link_candidates", uid="ABCD1234"))["result"][
        "garments"
    ]
    assert [g["id"] for g in ranked] == [alexs.id, sams.id]  # no tagged garments
    assert [
        g["id"]
        for g in (await member("unknown/link_candidates", uid="ABCD1234"))["result"][
            "garments"
        ]
    ] == [alexs.id]
    res = (await admin("garment/attach_tag", garment_id=sams.id, uid="ABCD1234"))[
        "result"
    ]
    assert res["linked"] and sams.uids == ["ABCD1234"]
    assert "ABCD1234" not in hub.catalog.unknown_tags


# --- members ---------------------------------------------------------------------------


async def test_member_untagged_and_tag_permissions(
    hass: HomeAssistant, hass_ws_client, kid
) -> None:
    hub = await setup(hass, app_access="household")
    hass.states.async_set(ALEX, "home", {"user_id": kid[0].id})
    bin_id, dry_id = zones(hub)
    sams = hub.enroll(
        uid="55667788", name="Sam shirt", wash_bin=bin_id, dry_type=dry_id, owner=SAM
    )
    sams_untagged = hub.enroll(
        uid=None, name="Sam scarf", wash_bin=bin_id, dry_type=dry_id, owner=SAM
    )
    member = Client(await hass_ws_client(hass, kid[1]))
    # Create untagged for themselves: owner forced to Alex.
    made = await member(
        "garment/create", name="Alex hat", wash_bin=bin_id, dry_type=dry_id, owner=SAM
    )
    assert made["error"]["code"] == "owner_not_allowed"
    made = (
        await member(
            "garment/create", name="Alex hat", wash_bin=bin_id, dry_type=dry_id
        )
    )["result"]
    assert made["owner"] == ALEX and made["tagged"] is False
    hat = made["id"]
    assert (await member("garment/manual_state", garment_id=hat, action="to_laundry"))[
        "success"
    ]
    assert (
        await member(
            "garment/manual_state", garment_id=sams_untagged.id, action="put_away"
        )
    )["error"]["code"] == "unauthorized"
    # Attach: a typed tag must be the one they just tapped.
    assert (await member("garment/attach_tag", garment_id=hat, uid="ABCD1234"))[
        "error"
    ]["code"] == "tag_not_yours"
    await member("tap", uid="ABCD1234", source="phone", device_name="Alex")
    assert (await member("garment/attach_tag", garment_id=hat, uid="ABCD1234"))[
        "success"
    ]
    # Never someone else's garment or tag.
    assert (await member("garment/attach_tag", garment_id=sams_untagged.id))["error"][
        "code"
    ] == "unauthorized"
    stolen = await member(
        "garment/attach_tag", garment_id=hat, uid="55667788", move=True
    )
    assert stolen["error"]["code"] == "unauthorized" and sams.uids == ["55667788"]
    # Their own session: completed by their phone tap; only they see it.
    await member("garment/remove_tag", garment_id=hat)
    res = (await member("garment/attach_tag", garment_id=hat))["result"]
    assert res["linked"] is False
    assert (await member("data"))["result"]["link"]["garment_id"] == hat
    linked = (await member("tap", uid="FEED0001", source="phone", device_name="Alex"))[
        "result"
    ]
    assert (
        linked["known"]
        and linked["linked"]
        and linked["garment"]["uids"] == ["FEED0001"]
    )


# --- Not clothing keeps photos; purge removes them -------------------------------------


async def test_not_clothing_restore_keeps_photo_files_and_purge_removes_them(
    hass: HomeAssistant, freezer
) -> None:
    hub = await setup(hass)
    inbox = hub.purchases
    photos = Path(hass.config.path("laundry_hub/photos"))
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "purchase-lamp-a.jpg").write_bytes(b"x")
    inbox.cards["lamp"] = stored_card(
        "lamp", "Desk Lamp", "amazon", photos=["purchase-lamp-a.jpg"]
    )
    await inbox.async_dismiss("lamp", not_clothing=True)
    assert inbox.cards["lamp"]["photos"] == ["purchase-lamp-a.jpg"]
    assert (photos / "purchase-lamp-a.jpg").is_file()
    restored = await inbox.async_restore("lamp")
    assert restored["photos"] == ["purchase-lamp-a.jpg"]  # the same file, no download
    await inbox.async_dismiss("lamp", not_clothing=True)
    # A year after it was dismissed, the archived card and its photos are purged.
    freezer.tick(timedelta(days=364))
    assert await inbox._async_purge_archived() == 0  # noqa: SLF001
    freezer.tick(timedelta(days=2))
    assert await inbox._async_purge_archived() == 1  # noqa: SLF001
    assert "lamp" not in inbox.cards and not (photos / "purchase-lamp-a.jpg").exists()
    assert "desk lamp" in inbox.meta["not_clothing"]["amazon"]  # the rule stays
