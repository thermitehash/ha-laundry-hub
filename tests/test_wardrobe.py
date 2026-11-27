"""v0.2 wardrobe: migration, wardrobe-off regression, photos, owners, stations."""

from __future__ import annotations

import io
import json
import os
from typing import Any

from homeassistant.core import HomeAssistant
from PIL import Image
import pytest

from custom_components.laundry_hub.const import (
    DOMAIN,
    PHOTO_UPLOAD_MAX_BYTES,
    TAP_EVENT,
    VERSION,
)
from custom_components.laundry_hub.hub import LaundryHub
from custom_components.laundry_hub.models import Garment
from custom_components.laundry_hub.photos import (
    PhotoError,
    ensure_thumbnail,
    process_image,
    thumb_name,
)
from custom_components.laundry_hub.store import Catalog, migrate_v2

from .conftest import add_esphome_station, make_entry

WARDROBE = {"wardrobe_enabled": True, "ai_task_entity": "ai_task.test"}


def jpeg(
    size=(40, 30), color=(200, 30, 30), exif_orientation: int | None = None
) -> bytes:
    image = Image.new("RGB", size, color)
    out = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation  # Orientation
        exif[0x010F] = "SecretCam"  # Make
        image.save(out, "JPEG", exif=exif.tobytes())
    else:
        image.save(out, "JPEG")
    return out.getvalue()


async def setup_entry(
    hass: HomeAssistant, options: dict[str, Any] | None = None
) -> LaundryHub:
    hass.states.async_set("ai_task.test", "unknown")
    entry = make_entry(options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


V2_DATA = {
    "garments": {
        "g1": {
            "id": "g1",
            "uids": ["11223344"],
            "name": "Hoodie",
            "owner": "person.alex",
            "wash_bin": "b",
            "dry_type": "d",
            "notes": "wool",
            "photo": "g1.png",
            "state": "put_away",
            "location": "closet_1",
            "last_seen": {"time": "2026-11-01T10:00:00+00:00", "station": "closet_1"},
            "last_laundry_tap": "2026-10-30T10:00:00+00:00",
            "created": "2026-10-01T10:00:00+00:00",
            "future_key": {"kept": True},
        },
        "g2": {
            "id": "g2",
            "uids": ["55667788"],
            "name": "Sock",
            "wash_bin": "b",
            "dry_type": "d",
            "photo": None,
            "created": "2026-10-01T10:00:00+00:00",
        },
    },
    "last_unknown": {"uid": "AABBCCDD", "station": "laundry", "time": "x"},
    "unknown_tags": {"AABBCCDD": {"uid": "AABBCCDD", "count": 1}},
    "quarantine": [],
}


# --- migration -------------------------------------------------------------------


def test_migrate_v2_is_lossless() -> None:
    data = migrate_v2(json.loads(json.dumps(V2_DATA)))
    g1 = data["garments"]["g1"]
    assert g1["photos"] == ["g1.png"]
    assert "photo" not in g1
    assert g1["future_key"] == {"kept": True}
    assert g1["wear_for"] == "any" and g1["uniform"] is False
    assert g1["lifecycle"] == "active"
    for reserved in ("wear_log", "fit_feedback"):
        assert g1[reserved] == []
    for reserved in ("out_since", "fit_score", "last_suggested"):
        assert g1[reserved] is None
    assert g1["skip_count"] == 0
    assert data["garments"]["g2"]["photos"] == []
    assert data["owners"] == {} and data["stations"] == {}
    # Every v2 key survives with the same value (except photo -> photos[0]).
    for gid, old in V2_DATA["garments"].items():
        for key, value in old.items():
            if key != "photo":
                assert data["garments"][gid][key] == value
    assert data["last_unknown"] == V2_DATA["last_unknown"]
    assert data["unknown_tags"] == V2_DATA["unknown_tags"]


async def test_v2_store_migrates_round_trips_and_backs_up(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[DOMAIN] = {
        "version": 2,
        "minor_version": 1,
        "key": DOMAIN,
        "data": json.loads(json.dumps(V2_DATA)),
    }
    catalog = Catalog(hass)
    await catalog.async_load()
    g1 = catalog.garments["g1"]
    assert g1.photos == ["g1.png"] and g1.photo == "g1.png"
    assert g1.extra == {"future_key": {"kept": True}}
    await catalog.async_flush()
    assert hass_storage[DOMAIN]["version"] == 3
    stored = hass_storage[DOMAIN]["data"]["garments"]["g1"]
    assert stored["future_key"] == {"kept": True}
    # Round trip: load the v3 data again, nothing changes.
    again = Garment.from_dict(stored).to_dict()
    assert again == stored
    backup = hass.config.path(".storage", f"{DOMAIN}.v2.backup")
    assert os.path.isfile(backup)
    with open(backup, encoding="utf-8") as file:  # noqa: ASYNC230
        saved = json.load(file)
    assert saved["version"] == 2
    assert saved["data"]["garments"]["g1"]["photo"] == "g1.png"


async def test_v1_store_migrates_through_v2(hass: HomeAssistant, hass_storage) -> None:
    hass_storage[DOMAIN] = {
        "version": 1,
        "key": DOMAIN,
        "data": {
            "garments": [
                {
                    "id": "a",
                    "uid": "11223344",
                    "name": "X",
                    "bin": "b",
                    "dry": "d",
                    "created": "2026-10-01T10:00:00+00:00",
                }
            ]
        },
    }
    catalog = Catalog(hass)
    await catalog.async_load()
    assert catalog.garments["a"].photos == []
    assert catalog.garments["a"].lifecycle == "active"


async def test_stored_description_remapped_on_load(
    hass: HomeAssistant, hass_storage
) -> None:
    raw = json.loads(json.dumps(V2_DATA))
    data = migrate_v2(raw)
    data["garments"]["g1"]["description"] = {
        "type": "Top",
        "subtype": "tee",
        "colors": ["gray", "neon"],
        "pattern": "zigzag",
    }
    hass_storage[DOMAIN] = {"version": 3, "key": DOMAIN, "data": data}
    catalog = Catalog(hass)
    await catalog.async_load()
    desc = catalog.garments["g1"].description
    assert desc["type"] == "top"
    assert desc["subtype"] == "t-shirt"
    assert [c["name"] for c in desc["colors"]] == ["grey"]
    assert desc["pattern"] is None


# --- wardrobe off: v0.1.2 behaviour ------------------------------------------------


async def test_wardrobe_off_regression(
    hass: HomeAssistant, hass_storage, hass_ws_client
) -> None:
    """A v0.1.2 store + config: taps, views and entities behave as before."""
    entry = make_entry()
    bins = [s for s in entry.subentries.values() if s.subentry_type == "wash_bin"]
    dries = [s for s in entry.subentries.values() if s.subentry_type == "dry_type"]
    data = json.loads(json.dumps(V2_DATA))
    for g in data["garments"].values():
        g["wash_bin"], g["dry_type"] = bins[0].subentry_id, dries[0].subentry_id
    hass_storage[DOMAIN] = {"version": 2, "key": DOMAIN, "data": data}
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub: LaundryHub = entry.runtime_data
    assert not hub.wardrobe.enabled
    assert hass.states.get("sensor.laundry_hub_closet_1_count").state == "1"

    hass.bus.async_fire(TAP_EVENT, {"station_id": "laundry", "uid": "11223344"})
    await hass.async_block_till_done()
    assert hub.catalog.garments["g1"].state == "in_wash"
    assert hass.states.get("sensor.laundry_hub_in_wash").state == "1"

    view = hub.garment_view(hub.catalog.garments["g1"])
    for key in (
        "description",
        "purchase",
        "brand",
        "size",
        "wear_for",
        "needs_review",
        "wear_log",
        "fit_score",
        "skip_count",
    ):
        assert key not in view
    assert view["photo"] == "g1.png" and view["photos"] == ["g1.png"]
    snap = hub.snapshot()
    assert snap["wardrobe"]["enabled"] is False and snap["taxonomy"] is None
    assert snap["wardrobe"]["photo_limit"] == 1

    client = await hass_ws_client(hass)
    for cmd in (
        {"type": "laundry_hub/garment/wardrobe", "garment_id": "g1", "brand": "X"},
        {"type": "laundry_hub/garment/describe", "garment_id": "g1"},
        {
            "type": "laundry_hub/garment/describe_fields",
            "garment_id": "g1",
            "fields": {"type": "top"},
        },
        {"type": "laundry_hub/import_product_url", "url": "https://example.com/p"},
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": ["g1"],
            "field": "type",
            "value": "top",
        },
    ):
        await client.send_json_auto_id(cmd)
        msg = await client.receive_json()
        assert msg["error"]["code"] == "wardrobe_disabled", cmd
    # Bulk owner/bin changes still work without the wardrobe.
    hass.states.async_set("person.alex", "home")
    await client.send_json_auto_id(
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": ["g1"],
            "field": "owner",
            "value": None,
        }
    )
    assert (await client.receive_json())["result"] == {"updated": 1}


# --- photos ----------------------------------------------------------------------


def test_process_image_orients_strips_and_resizes() -> None:
    raw = jpeg(size=(3200, 1600), exif_orientation=6)  # rotate 90 degrees
    out = process_image(raw)
    full = Image.open(io.BytesIO(out.full))
    thumb = Image.open(io.BytesIO(out.thumb))
    assert full.size == (800, 1600)  # rotated, long edge 1600
    assert max(thumb.size) == 400
    for image in (full, thumb):
        assert image.format == "JPEG"
        assert not image.getexif()
        assert "exif" not in image.info and "icc_profile" not in image.info
    assert b"SecretCam" not in out.full


def test_process_image_small_and_alpha() -> None:
    image = Image.new("RGBA", (50, 20), (0, 0, 0, 0))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    out = process_image(buf.getvalue())
    full = Image.open(io.BytesIO(out.full))
    assert full.size == (50, 20)  # never upscaled
    assert full.mode == "RGB"
    assert full.getpixel((0, 0)) == (255, 255, 255)  # transparent -> white


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        (b"", "photo_invalid"),
        (b"not an image", "photo_invalid"),
        (b"0" * (PHOTO_UPLOAD_MAX_BYTES + 1), "photo_too_large"),
    ],
)
def test_process_image_rejects(raw: bytes, key: str) -> None:
    with pytest.raises(PhotoError) as err:
        process_image(raw)
    assert err.value.key == key


def test_ensure_thumbnail(tmp_path) -> None:
    (tmp_path / "legacy.png").write_bytes(jpeg())
    assert ensure_thumbnail(tmp_path, "legacy.png")
    assert (tmp_path / "legacy.thumb.jpg").is_file()
    assert not ensure_thumbnail(tmp_path, "legacy.png")  # already there
    assert not ensure_thumbnail(tmp_path, "../x.png")
    assert not ensure_thumbnail(tmp_path, "missing.jpg")
    (tmp_path / "bad.jpg").write_bytes(b"junk")
    assert not ensure_thumbnail(tmp_path, "bad.jpg")
    assert thumb_name("a.b.jpg") == "a.b.thumb.jpg"
    assert thumb_name("noext") == "noext.thumb.jpg"


async def test_http_upload_limits_and_delete(
    hass: HomeAssistant, hass_client, hass_ws_client
) -> None:
    hub = await setup_entry(hass, {"wardrobe_enabled": True})  # no AI: no auto-describe
    garment = hub.enroll(uid="11223344", name="Tee")
    client = await hass_client()

    async def upload(raw: bytes, gid: str = garment.id):
        form = {"garment_id": gid, "file": io.BytesIO(raw)}
        return await client.post("/api/laundry_hub/photos", data=form)

    for _ in range(8):
        resp = await upload(jpeg())
        assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert len(body["photos"]) == 8
    resp = await upload(jpeg())
    assert resp.status == 400
    assert (await resp.json())["code"] == "photo_limit"
    resp = await upload(b"junk", gid=garment.id)
    assert resp.status == 400
    resp = await upload(jpeg(), gid="nope")
    assert (await resp.json())["code"] == "unknown_garment"
    resp = await client.post("/api/laundry_hub/photos", data={"garment_id": garment.id})
    assert resp.status == 400

    names = list(garment.photos)
    resp = await client.get(f"/api/laundry_hub/photos/{names[0]}")
    assert resp.status == 200
    assert (
        await client.get(f"/api/laundry_hub/photos/{thumb_name(names[0])}")
    ).status == 200
    assert (await client.get("/api/laundry_hub/photos/other.jpg")).status == 404
    assert (await client.get("/api/laundry_hub/photos/..%2Fx.jpg")).status in (400, 404)

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/photo/reorder",
            "garment_id": garment.id,
            "photos": list(reversed(names)),
        }
    )
    assert (await ws.receive_json())["result"]["photos"][0] == names[-1]
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/photo/reorder",
            "garment_id": garment.id,
            "photos": names[:2],
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "invalid_field_value"
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/photo/remove",
            "garment_id": garment.id,
            "photo": names[0],
        }
    )
    assert names[0] not in (await ws.receive_json())["result"]["photos"]
    assert not os.path.exists(hass.config.path("laundry_hub/photos", names[0]))
    assert not os.path.exists(
        hass.config.path("laundry_hub/photos", thumb_name(names[0]))
    )
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/photo/remove",
            "garment_id": garment.id,
            "photo": names[0],
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "unknown_photo"

    # Data snapshot signs a full and a thumbnail URL per photo.
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    data = (await ws.receive_json())["result"]
    urls = next(g for g in data["garments"] if g["id"] == garment.id)["photo_urls"]
    assert len(urls) == 7 and "authSig=" in urls[0]["thumb"]

    left = list(garment.photos)
    await hub.async_remove(garment.id)
    for name in left:
        assert not os.path.exists(hass.config.path("laundry_hub/photos", name))


async def test_upload_requires_admin(
    hass: HomeAssistant, hass_client, hass_read_only_access_token
) -> None:
    hub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="Tee")
    client = await hass_client(hass_read_only_access_token)
    resp = await client.post(
        "/api/laundry_hub/photos",
        data={"garment_id": garment.id, "file": io.BytesIO(jpeg())},
    )
    assert resp.status == 401


async def test_legacy_thumbnail_fallback(hass: HomeAssistant, hass_client) -> None:
    hub = await setup_entry(hass)
    garment = hub.enroll(uid="11223344", name="Tee")
    directory = hass.config.path("laundry_hub/photos")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{garment.id}.png"), "wb") as file:  # noqa: ASYNC230
        file.write(jpeg())
    garment.photos = [f"{garment.id}.png"]
    client = await hass_client()
    resp = await client.get(f"/api/laundry_hub/photos/{garment.id}.thumb.jpg")
    assert resp.status == 200  # served from the original until the thumb exists
    await hub.wardrobe.async_ensure_thumbnails()
    assert os.path.exists(os.path.join(directory, f"{garment.id}.thumb.jpg"))


# --- owners, stations, recent taps ---------------------------------------------------


async def test_station_health_owner_settings_recent_taps(
    hass: HomeAssistant, hass_ws_client, freezer
) -> None:
    _, dev = add_esphome_station(hass, "laundry-room")
    from homeassistant.helpers import entity_registry as er

    reg = er.async_get(hass)
    ent = reg.async_get_or_create("sensor", "esphome", "wifi", device_id=dev)
    hass.states.async_set(ent.entity_id, "unavailable")
    hub = await setup_entry(hass, {"station_devices": {"laundry": dev}})
    hass.states.async_set("person.alex", "home")
    garment = hub.enroll(uid="11223344", name="Tee", owner="person.alex")
    for _ in range(12):
        hass.bus.async_fire(TAP_EVENT, {"station_id": "laundry", "uid": "11223344"})
        await hass.async_block_till_done()
    assert len(garment.recent_taps) == 10
    assert garment.recent_taps[-1]["station"] == "laundry"
    assert garment.recent_taps[0]["event"] in ("in_wash", "drying")

    health = {h["station"]: h for h in hub.station_health()}
    assert health["laundry"]["configured"] is True
    assert health["laundry"]["online"] is False
    assert health["laundry"]["last_tap"] is not None
    assert health["closet_1"] == {
        "station": "closet_1",
        "configured": False,
        "online": None,
        "last_tap": None,
    }
    hass.states.async_set(ent.entity_id, "-60")
    assert {h["station"]: h for h in hub.station_health()}["laundry"]["online"] is True

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/owner/update",
            "owner": "person.alex",
            "work_label": " School ",
        }
    )
    assert (await ws.receive_json())["result"]["work_label"] == "School"
    await ws.send_json_auto_id(
        {"type": "laundry_hub/owner/update", "owner": "", "work_label": "X"}
    )
    assert (await ws.receive_json())["error"]["code"] == "invalid_owner"
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    data = (await ws.receive_json())["result"]
    assert data["owners"]["person.alex"] == {
        "work_label": "School",
        "body_profile": None,
        "schedule": None,
    }
    assert data["version"] == VERSION
    assert data["station_health"][0]["station"] == "laundry"


async def test_subscribe_pushes_taps(hass: HomeAssistant, hass_ws_client) -> None:
    hub = await setup_entry(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "laundry_hub/subscribe"})
    assert (await ws.receive_json())["success"]
    hass.bus.async_fire(TAP_EVENT, {"station_id": "closet_1", "uid": "99887766"})
    await hass.async_block_till_done()
    seen = [await ws.receive_json() for _ in range(2)]
    taps = [m["event"]["tap"] for m in seen if "tap" in m["event"]]
    assert taps and taps[0]["event_type"] == "unknown" and taps[0]["uid"] == "99887766"
    assert hub.catalog.stations["closet_1"]["last_tap"]
