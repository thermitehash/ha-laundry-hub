"""Websocket API validation (the panel's only write path)."""

from __future__ import annotations

import base64
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.laundry_hub.const import PHOTO_MAX_BYTES
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import bin_id, dry_id

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


@pytest.fixture
async def ws(hass: HomeAssistant, entry, hass_ws_client):  # noqa: ANN201
    client = await hass_ws_client(hass)
    counter = {"id": 0}

    async def call(type_: str, **payload: Any) -> dict[str, Any]:
        await client.send_json_auto_id({"type": f"laundry_hub/{type_}", **payload})
        return await client.receive_json()

    call.client = client  # type: ignore[attr-defined]
    call.counter = counter  # type: ignore[attr-defined]
    return call


async def test_create_and_data(hass: HomeAssistant, ws, hub: LaundryHub) -> None:
    hass.states.async_set("person.alex", "home", {"friendly_name": "Alex"})
    msg = await ws(
        "garment/create",
        uid="04-a1-b2-c3-d4-e5-f6",
        name="Hoodie",
        wash_bin=bin_id(hub, "Darks"),
        dry_type=dry_id(hub, "Hang rod"),
        owner="person.alex",
        notes="wool",
        location="closet_2",
    )
    assert msg["success"], msg
    garment = msg["result"]
    assert garment["uids"] == ["04A1B2C3D4E5F6"]
    assert garment["state"] == "put_away"
    assert garment["location"] == "closet_2"
    assert garment["missing"] is False

    msg = await ws("data")
    assert msg["success"]
    data = msg["result"]
    assert len(data["garments"]) == 1
    assert [b["name"] for b in data["wash_bins"]] == [
        "Darks",
        "Lights & whites",
        "Colors",
        "Reds",
        "Delicates",
    ]
    assert data["wash_bins"][0]["usage"] == 1
    assert data["persons"] == [{"entity_id": "person.alex", "name": "Alex"}]
    assert data["stations"] == ["laundry", "closet_1", "closet_2"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"uid": "nothex!!"}, "invalid_uid"),
        ({"uid": "0102"}, "invalid_uid"),
        ({"wash_bin": "nope"}, "unknown_zone"),
        ({"dry_type": "nope"}, "unknown_zone"),
        ({"name": "   "}, "invalid_name"),
        ({"owner": "person.ghost"}, "invalid_owner"),
        ({"owner": "light.kitchen"}, "invalid_owner"),
    ],
)
async def test_create_validation(ws, hub: LaundryHub, payload, code) -> None:
    base = {
        "uid": "11223344",
        "name": "Shirt",
        "wash_bin": bin_id(hub, "Colors"),
        "dry_type": dry_id(hub, "Dryer"),
    }
    msg = await ws("garment/create", **{**base, **payload})
    assert not msg["success"]
    assert msg["error"]["code"] == code


async def test_create_wrong_zone_type(ws, hub: LaundryHub) -> None:
    msg = await ws(
        "garment/create",
        uid="11223344",
        name="Shirt",
        wash_bin=dry_id(hub, "Dryer"),  # a dry type passed as wash bin
        dry_type=dry_id(hub, "Dryer"),
    )
    assert msg["error"]["code"] == "unknown_zone"


async def test_create_schema_validation(ws) -> None:
    msg = await ws("garment/create", uid="11223344")
    assert msg["error"]["code"] == "invalid_format"
    msg = await ws(
        "garment/create",
        uid="11223344",
        name="x",
        wash_bin="a",
        dry_type="b",
        location="garage",
    )
    assert msg["error"]["code"] == "invalid_format"


async def test_duplicate_uid(ws, hub: LaundryHub) -> None:
    hub.enroll(uid="11223344", name="A")
    msg = await ws(
        "garment/create",
        uid="11:22:33:44",
        name="B",
        wash_bin=bin_id(hub, "Darks"),
        dry_type=dry_id(hub, "Dryer"),
    )
    assert msg["error"]["code"] == "duplicate_uid"


async def test_update(ws, hub: LaundryHub) -> None:
    a = hub.enroll(uid="11223344", name="A")
    b = hub.enroll(uid="55667788", name="B")
    msg = await ws(
        "garment/update",
        garment_id=a.id,
        name="A2",
        wash_bin=bin_id(hub, "Reds"),
        dry_type="lay flat",  # by name, case-insensitive
        notes="",
        owner=None,
        add_uid="99AABBCC",
    )
    assert msg["success"], msg
    assert msg["result"]["name"] == "A2"
    assert msg["result"]["wash_bin"] == bin_id(hub, "Reds")
    assert msg["result"]["dry_type"] == dry_id(hub, "Lay flat")
    assert msg["result"]["uids"] == ["11223344", "99AABBCC"]

    # Replace a tag: add new + remove old in one call.
    msg = await ws(
        "garment/update", garment_id=a.id, add_uid="DDEEFF00", remove_uid="11223344"
    )
    assert msg["result"]["uids"] == ["99AABBCC", "DDEEFF00"]
    assert hub.catalog.by_uid("11223344") is None

    msg = await ws("garment/update", garment_id=a.id, add_uid="55667788")
    assert msg["error"]["code"] == "duplicate_uid"
    msg = await ws("garment/update", garment_id=b.id, remove_uid="55667788")
    assert msg["error"]["code"] == "last_tag"
    # Swap only tag in one call is allowed.
    msg = await ws(
        "garment/update", garment_id=b.id, remove_uid="55667788", add_uid="12121212"
    )
    assert msg["result"]["uids"] == ["12121212"]
    msg = await ws("garment/update", garment_id="nope", name="x")
    assert msg["error"]["code"] == "unknown_garment"
    msg = await ws("garment/update", garment_id=a.id, wash_bin="nope")
    assert msg["error"]["code"] == "unknown_zone"
    # Atomic: a failing field leaves the garment unchanged.
    msg = await ws(
        "garment/update", garment_id=a.id, name="Changed", owner="person.ghost"
    )
    assert msg["error"]["code"] == "invalid_owner"
    assert hub.catalog.garments[a.id].name == "A2"
    # By UID reference.
    msg = await ws("garment/update", garment_id="DDEEFF00", notes="x" * 10)
    assert msg["success"]


async def test_delete_and_set_location(ws, hub: LaundryHub) -> None:
    a = hub.enroll(uid="11223344", name="A")
    msg = await ws("garment/set_location", garment_id=a.id, location="in_wash")
    assert msg["result"]["state"] == "in_wash"
    assert msg["result"]["location"] == "laundry"
    assert msg["result"]["last_seen"]["station"] is None
    msg = await ws("garment/set_location", garment_id=a.id, location="closet_1")
    assert msg["result"]["state"] == "put_away"
    msg = await ws("garment/set_location", garment_id=a.id, location="moon")
    assert msg["error"]["code"] == "invalid_format"
    msg = await ws("garment/delete", garment_id=a.id)
    assert msg["result"] == {"garment_id": a.id}
    msg = await ws("garment/delete", garment_id=a.id)
    assert msg["error"]["code"] == "unknown_garment"


async def test_reassign(ws, hub: LaundryHub) -> None:
    a = hub.enroll(uid="11223344", name="A", wash_bin="Reds")
    b = hub.enroll(uid="55667788", name="B", wash_bin="Reds")
    c = hub.enroll(uid="99AABBCC", name="C", wash_bin="Colors")
    msg = await ws("reassign", field="wash_bin", to="Darks", garment_ids=[a.id])
    assert msg["result"] == {"moved": 1}
    msg = await ws("reassign", field="wash_bin", to="Darks", **{"from": "Reds"})
    assert msg["result"] == {"moved": 1}
    assert {g.wash_bin for g in (a, b)} == {bin_id(hub, "Darks")}
    assert c.wash_bin == bin_id(hub, "Colors")
    msg = await ws(
        "reassign", field="dry_type", to="Hang rod", garment_ids=[a.id, "99AABBCC"]
    )
    assert msg["result"] == {"moved": 2}
    msg = await ws("reassign", field="wash_bin", to="Darks")
    assert msg["error"]["code"] == "reassign_selection"
    msg = await ws("reassign", field="wash_bin", to="Dryer", garment_ids=[a.id])
    assert msg["error"]["code"] == "unknown_zone"
    msg = await ws("reassign", field="colour", to="Darks", garment_ids=[a.id])
    assert msg["error"]["code"] == "invalid_format"


async def test_zone_remove_blocked(
    hass: HomeAssistant, ws, hub: LaundryHub, entry
) -> None:
    hub.enroll(uid="11223344", name="A", wash_bin="Reds")
    sid = bin_id(hub, "Reds")
    msg = await ws("zone/remove", zone_id=sid)
    assert msg["error"]["code"] == "zone_in_use"
    assert "used by 1 garment" in msg["error"]["message"]
    assert "Bulk reassign" in msg["error"]["message"]
    assert sid in entry.subentries
    msg = await ws("zone/remove", zone_id=dry_id(hub, "Lay flat"))
    assert msg["success"]
    await hass.async_block_till_done()
    msg = await ws("zone/remove", zone_id="nope")
    assert msg["error"]["code"] == "unknown_zone"


async def test_zone_flash(
    hass: HomeAssistant, ws, hub: LaundryHub, entry, light_calls
) -> None:
    msg = await ws("zone/flash", zone_id=bin_id(hub, "Darks"))
    assert msg["error"]["code"] == "no_light"
    sub = entry.subentries[bin_id(hub, "Darks")]
    hass.states.async_set("light.seg1", "off")
    hass.config_entries.async_update_subentry(
        entry, sub, data={**sub.data, "light_entity": "light.seg1"}
    )
    await hass.async_block_till_done()
    msg = await ws("zone/flash", zone_id="darks")
    assert msg["result"] == {"flashed": True}
    await entry.runtime_data.flash.async_stop()


async def test_unknown_dismiss(ws, hub: LaundryHub) -> None:
    from homeassistant.util import dt as dt_util

    hub.catalog.record_unknown("AABBCCDD", "laundry", dt_util.utcnow())
    msg = await ws("unknown/dismiss", uid="aa:bb:cc:dd")
    assert msg["success"]
    assert hub.catalog.unknown_tags == {}
    msg = await ws("unknown/dismiss", uid="bad")
    assert msg["error"]["code"] == "invalid_uid"


async def test_photo(hass: HomeAssistant, ws, hub: LaundryHub, hass_client) -> None:
    a = hub.enroll(uid="11223344", name="A")
    msg = await ws("photo/upload", garment_id=a.id, content_type="image/png", data=PNG)
    assert msg["success"], msg
    assert msg["result"]["photo"] == f"{a.id}.png"
    path = hass.config.path("laundry_hub/photos", f"{a.id}.png")
    import os

    assert os.path.isfile(path)

    client = await hass_client()
    resp = await client.get(f"/api/laundry_hub/photo/{a.id}")
    assert resp.status == 200
    assert (await resp.read()).startswith(b"\x89PNG")
    resp = await client.get("/api/laundry_hub/photo/nope")
    assert resp.status == 404

    # Replace with jpeg removes the png.
    jpg = base64.b64encode(b"\xff\xd8\xff" + b"1" * 10).decode()
    msg = await ws("photo/upload", garment_id=a.id, content_type="image/jpeg", data=jpg)
    assert msg["result"]["photo"] == f"{a.id}.jpg"
    assert not os.path.exists(path)

    msg = await ws("photo/upload", garment_id=a.id, content_type="image/gif", data=PNG)
    assert msg["error"]["code"] == "invalid_format"
    msg = await ws(
        "photo/upload", garment_id=a.id, content_type="image/png", data="!!notbase64"
    )
    assert msg["error"]["code"] == "photo_invalid"
    msg = await ws("photo/upload", garment_id=a.id, content_type="image/png", data="")
    assert msg["error"]["code"] == "photo_invalid"
    big = base64.b64encode(b"0" * (PHOTO_MAX_BYTES + 1)).decode()
    msg = await ws("photo/upload", garment_id=a.id, content_type="image/png", data=big)
    assert msg["error"]["code"] == "photo_too_large"

    msg = await ws("photo/delete", garment_id=a.id)
    assert msg["result"]["photo"] is None
    msg = await ws("photo/delete", garment_id=a.id)  # idempotent
    assert msg["success"]
    resp = await client.get(f"/api/laundry_hub/photo/{a.id}")
    assert resp.status == 404

    # Deleting a garment deletes its photo.
    await ws("photo/upload", garment_id=a.id, content_type="image/png", data=PNG)
    await ws("garment/delete", garment_id=a.id)
    assert not os.path.exists(path)


async def test_photo_file_missing_on_disk(
    hass: HomeAssistant, ws, hub: LaundryHub, hass_client
) -> None:
    a = hub.enroll(uid="11223344", name="A")
    a.photo = "../../etc/passwd"
    client = await hass_client()
    assert (await client.get(f"/api/laundry_hub/photo/{a.id}")).status == 404
    a.photo = f"{a.id}.png"  # not written
    assert (await client.get(f"/api/laundry_hub/photo/{a.id}")).status == 404


async def test_photo_write_error(hass: HomeAssistant, ws, hub: LaundryHub) -> None:
    from unittest.mock import patch

    a = hub.enroll(uid="11223344", name="A")
    with patch(
        "custom_components.laundry_hub.hub.write_photo",
        side_effect=OSError("disk full"),
    ):
        msg = await ws(
            "photo/upload", garment_id=a.id, content_type="image/png", data=PNG
        )
    assert msg["error"]["code"] == "failed"
    assert "disk full" in msg["error"]["message"]
    a.photo = f"{a.id}.png"
    with patch(
        "custom_components.laundry_hub.hub.delete_photo", side_effect=OSError("ro fs")
    ):
        msg = await ws("photo/delete", garment_id=a.id)
    assert msg["error"]["code"] == "failed"


async def test_subscribe(hass: HomeAssistant, ws, hub: LaundryHub) -> None:
    msg = await ws("subscribe")
    assert msg["success"]
    hub.enroll(uid="11223344", name="A")
    event = await ws.client.receive_json()
    assert event["event"] == {"changed": True}


async def test_not_loaded(hass: HomeAssistant, entry, hass_ws_client) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "laundry_hub/data"})
    msg = await client.receive_json()
    assert msg["error"]["code"] == "not_loaded"


async def test_photo_view_not_loaded(hass: HomeAssistant, entry, hass_client) -> None:
    client = await hass_client()
    await hass.config_entries.async_unload(entry.entry_id)
    assert (await client.get("/api/laundry_hub/photo/x")).status == 404


async def test_requires_admin(
    hass: HomeAssistant, entry, hass_ws_client, hass_read_only_access_token
) -> None:
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "laundry_hub/data"})
    msg = await client.receive_json()
    assert msg["error"]["code"] == "unauthorized"


async def test_panel_registered(hass: HomeAssistant, entry) -> None:
    from homeassistant.components import frontend

    panels = hass.data[frontend.DATA_PANELS]
    assert "laundry" in panels
    panel = panels["laundry"]
    assert panel.require_admin is True
    assert panel.config["_panel_custom"]["module_url"].startswith(
        "/laundry_hub_static/laundry-panel.js"
    )
    await hass.config_entries.async_unload(entry.entry_id)
    assert "laundry" not in hass.data[frontend.DATA_PANELS]
    await hass.config_entries.async_setup(entry.entry_id)
    assert "laundry" in hass.data[frontend.DATA_PANELS]


async def test_static_js_served(hass: HomeAssistant, entry, hass_client) -> None:
    client = await hass_client()
    resp = await client.get("/laundry_hub_static/laundry-panel.js")
    assert resp.status == 200
    assert "laundry-hub-panel" in await resp.text()
