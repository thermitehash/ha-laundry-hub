"""v0.2.3 app API: api_info, changes, if_rev, permissions, phone taps, photo access."""

from __future__ import annotations

from datetime import timedelta
import io
from pathlib import Path
import re
from typing import Any

from aiohttp import FormData
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry

from custom_components.laundry_hub.access import Policy
from custom_components.laundry_hub.config_flow import seed_subentries
from custom_components.laundry_hub.const import DOMAIN, STORAGE_KEY, TAP_EVENT, VERSION
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import OPTIONS
from .test_wardrobe import jpeg

FRONTEND = Path(__file__).parent.parent / "custom_components/laundry_hub/frontend"
ALEX = "person.alex"
SAM = "person.sam"


class Client:
    """A websocket client for one HA user."""

    def __init__(self, raw: Any) -> None:
        self.raw = raw

    async def __call__(self, type_: str, **payload: Any) -> dict[str, Any]:
        await self.raw.send_json_auto_id({"type": f"{DOMAIN}/{type_}", **payload})
        return await self.raw.receive_json()


async def setup(hass: HomeAssistant, **options: Any) -> LaundryHub:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Laundry Hub",
        data={},
        options={**OPTIONS, "wardrobe_enabled": True, **options},
        subentries_data=seed_subentries(),
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


@pytest.fixture
async def member(hass: HomeAssistant) -> Any:
    """A non-admin HA user linked to person.alex, plus a token."""
    user = await hass.auth.async_create_user("Alex Kid", group_ids=["system-users"])
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    hass.states.async_set(ALEX, "home", {"user_id": user.id})
    hass.states.async_set(SAM, "home", {"user_id": None})
    return user, hass.auth.async_create_access_token(refresh)


async def clients(
    hass: HomeAssistant, hass_ws_client: Any, member: Any
) -> tuple[Client, Client]:
    admin = Client(await hass_ws_client(hass))
    kid = Client(await hass_ws_client(hass, member[1]))
    return admin, kid


def garments(hub: LaundryHub) -> tuple[Any, Any]:
    mine = hub.enroll(uid="11223344", name="Alex hoodie", owner=ALEX)
    theirs = hub.enroll(uid="55667788", name="Sam shirt", owner=SAM)
    return mine, theirs


# --- api_info ------------------------------------------------------------------------


async def test_api_info(hass: HomeAssistant, hass_ws_client, member) -> None:
    hub = await setup(hass, app_access="household", ai_task_entity="ai_task.x")
    admin, kid = await clients(hass, hass_ws_client, member)
    info = (await admin("api_info"))["result"]
    assert info["api_version"] == 1 and info["integration_version"] == VERSION
    assert info["wardrobe_enabled"] is True and info["role"] == "admin"
    assert {"phone_tap", "url_import", "ai_describe", "changes", "if_rev"} <= set(
        info["capabilities"]
    )
    assert "purchases" not in info["capabilities"]  # mailbox not connected
    assert info["rev"] == hub.revs.rev
    info = (await kid("api_info"))["result"]
    assert info["role"] == "member" and info["owner"] == ALEX
    assert info["app_access"] == "household"


async def test_household_off_members_locked_out(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    await setup(hass)
    _admin, kid = await clients(hass, hass_ws_client, member)
    for type_ in ("api_info", "data", "changes", "subscribe"):
        payload = {"since_rev": 0} if type_ == "changes" else {}
        msg = await kid(type_, **payload)
        assert msg["error"]["code"] == "unauthorized", type_


# --- changes / revs ------------------------------------------------------------------


async def test_changes_revs_and_tombstones(
    hass: HomeAssistant, hass_ws_client, member, freezer
) -> None:
    hub = await setup(hass)
    admin, _kid = await clients(hass, hass_ws_client, member)
    data = (await admin("data"))["result"]
    start = data["rev"]
    assert all(isinstance(z["rev"], int) and z["updated_at"] for z in data["wash_bins"])
    assert start >= 8  # 8 seeded zones got revs

    a = hub.enroll(uid="11223344", name="A")
    hub.catalog.record_unknown(
        "AABBCCDD", "closet_1", hub.catalog.garments[a.id].created
    )
    hub.notify_changed()
    changes = (await admin("changes", since_rev=start))["result"]
    assert changes["reset"] is False
    assert [g["name"] for g in changes["garments"]] == ["A"]
    assert changes["garments"][0]["rev"] > start
    assert [t["uid"] for t in changes["unknown_tags"]] == ["AABBCCDD"]
    rev_a = changes["garments"][0]["rev"]

    # Nothing changed: nothing returned, rev unchanged.
    again = (await admin("changes", since_rev=changes["rev"]))["result"]
    assert again["garments"] == [] and again["rev"] == changes["rev"]

    msg = await admin("garment/update", garment_id=a.id, name="A2")
    assert msg["result"]["rev"] > rev_a and msg["result"]["name"] == "A2"
    msg = await admin("garment/delete", garment_id=a.id)
    assert msg["success"]
    changes = (await admin("changes", since_rev=again["rev"]))["result"]
    assert changes["garments"] == []
    stone = next(t for t in changes["deleted"] if t["kind"] == "garment")
    assert stone == {
        "id": a.id,
        "kind": "garment",
        "rev": stone["rev"],
        "deleted": True,
    }

    # After 30 days the tombstone is purged: an old since_rev must reset.
    old = again["rev"]
    freezer.tick(timedelta(days=31))
    hub.notify_changed()
    assert (await admin("changes", since_rev=old))["result"] == {
        "reset": True,
        "rev": hub.revs.rev,
    }
    # A since_rev from the future (store restored) resets too.
    assert (await admin("changes", since_rev=hub.revs.rev + 5))["result"]["reset"]
    # Zones get revs and tombstones like everything else.
    zone = next(iter(hub.zones))
    assert hub.revs.meta("zone", zone) is not None


async def test_subscribe_events_carry_rev(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    hub = await setup(hass)
    admin, _ = await clients(hass, hass_ws_client, member)
    assert (await admin("subscribe"))["success"]
    hub.enroll(uid="11223344", name="A")
    event = await admin.raw.receive_json()
    assert event["event"] == {"changed": True, "rev": hub.revs.rev}


async def test_migration_3_2_assigns_revs(hass: HomeAssistant) -> None:
    raw = Store(hass, 3, STORAGE_KEY, minor_version=2)
    await raw.async_save(
        {
            "garments": {
                "g1": {
                    "id": "g1",
                    "uids": ["11223344"],
                    "name": "Old",
                    "owner": None,
                    "wash_bin": "x",
                    "dry_type": "y",
                    "notes": "",
                    "photos": [],
                    "state": "new",
                    "location": None,
                    "last_seen": None,
                    "last_laundry_tap": None,
                    "created": "2026-01-01T00:00:00+00:00",
                }
            },
            "unknown_tags": {
                "AABBCCDD": {"uid": "AABBCCDD", "last_seen": "2026-01-01"}
            },
            "purchases": {"c1": {"id": "c1", "status": "new", "photos": []}},
            "purchase_meta": {},
        }
    )
    hub = await setup(hass)
    for kind, rid in (
        ("garment", "g1"),
        ("unknown_tag", "AABBCCDD"),
        ("purchase", "c1"),
    ):
        assert hub.revs.meta(kind, rid) is not None, kind
    revs = sorted(e["rev"] for e in hub.catalog.revs["records"].values())
    assert revs == list(range(1, len(revs) + 1))  # one counter, no gaps
    await hub.catalog.async_flush()
    saved = await Store(hass, 3, STORAGE_KEY, minor_version=3).async_load()
    assert saved["revs"]["rev"] == hub.revs.rev
    assert saved["garments"]["g1"]["name"] == "Old"  # lossless


# --- if_rev ----------------------------------------------------------------------------


async def test_if_rev_conflicts(hass: HomeAssistant, hass_ws_client, member) -> None:
    hub = await setup(hass)
    admin, _ = await clients(hass, hass_ws_client, member)
    g = hub.enroll(uid="11223344", name="A")
    rev = hub.garment_view(g)["rev"]
    hub.update(g.id, {"name": "Changed elsewhere"})
    for type_, payload in (
        ("garment/update", {"name": "Mine"}),
        ("garment/set_location", {"location": "closet_1"}),
        ("garment/wardrobe", {"wear_for": "work"}),
        ("garment/describe_fields", {"fields": {"pattern": "solid"}}),
    ):
        msg = await admin(type_, garment_id=g.id, if_rev=rev, **payload)
        err = msg["error"]
        assert err["code"] == "conflict", type_
        assert err["kind"] == "garment" and err["record_id"] == g.id
        assert err["current"]["name"] == "Changed elsewhere"
    assert g.name == "Changed elsewhere" and g.state == "new"  # nothing written
    current = hub.garment_view(g)["rev"]
    msg = await admin("garment/update", garment_id=g.id, if_rev=current, name="Mine")
    assert msg["success"] and g.name == "Mine"
    # Bulk commands take if_revs.
    bin_id = next(z.id for z in hub.zones.values() if z.type == "wash_bin")
    msg = await admin(
        "reassign",
        field="wash_bin",
        to=bin_id,
        garment_ids=[g.id],
        if_revs={g.id: current},
    )
    assert msg["error"]["code"] == "conflict"
    # The panel never sends if_rev: unchanged behaviour.
    assert (await admin("garment/update", garment_id=g.id, name="Panel"))["success"]


# --- permission matrix -------------------------------------------------------------------


def payloads(hub: LaundryHub, gid: str) -> dict[str, dict[str, Any]]:
    bin_id = next(z.id for z in hub.zones.values() if z.type == "wash_bin")
    dry_id = next(z.id for z in hub.zones.values() if z.type == "dry_type")
    return {
        "data": {},
        "api_info": {},
        "changes": {"since_rev": 0},
        "photo/url": {"garment_id": gid, "filename": "x.jpg"},
        "tap": {"uid": "99AABBCC", "source": "phone"},
        "garment/create": {
            "uid": "01020304",
            "name": "N",
            "wash_bin": bin_id,
            "dry_type": dry_id,
        },
        "garment/update": {"garment_id": gid, "name": "N"},
        "garment/delete": {"garment_id": gid},
        "garment/set_location": {"garment_id": gid, "location": "closet_1"},
        "garment/wardrobe": {"garment_id": gid, "wear_for": "work"},
        "garment/describe_fields": {"garment_id": gid, "fields": {"pattern": "solid"}},
        "garment/reset_field": {"garment_id": gid, "field": "pattern"},
        "garment/describe": {"garment_id": gid},
        "garment/set_field": {"garment_ids": [gid], "field": "uniform", "value": True},
        "photo/upload": {
            "garment_id": gid,
            "content_type": "image/jpeg",
            "data": "eA==",
        },
        "photo/delete": {"garment_id": gid},
        "photo/remove": {"garment_id": gid, "photo": "x.jpg"},
        "photo/reorder": {"garment_id": gid, "photos": []},
        "reassign": {"field": "wash_bin", "to": bin_id, "garment_ids": [gid]},
        "zone/remove": {"zone_id": bin_id},
        "zone/flash": {"zone_id": bin_id},
        "unknown/dismiss": {"uid": "AABBCCDD"},
        "import_product_url": {"url": "https://shop.example.com/p"},
        "import/apply": {"garment_id": gid, "token": "t"},
        "owner/update": {"owner": ALEX, "work_label": "School"},
        "purchases/suggest": {},
        "purchases/match": {"card_id": "c", "garment_id": gid},
        "purchases/dismiss": {"card_id": "c"},
        "purchases/restore": {"card_id": "c"},
        "purchases/uniform": {"store_key": "blauer"},
        "purchases/check": {},
        "purchases/retry_unreadable": {},
        "purchases/set_quantity": {"card_id": "c", "quantity": 2},
        "purchases/split": {"card_id": "c", "purchase_id": "p"},
        "purchases/merge": {"card_id": "c", "other_id": "d"},
        "purchases/assign": {"card_id": "c", "owner": None},
        "purchases/cleanup_seen": {},
        "purchases/reclassify": {},
        "hello": {"client_api_versions": [1], "client_build": 1},
    }


def registered(hass: HomeAssistant) -> dict[str, Any]:
    handlers = hass.data[websocket_api.DOMAIN]
    return {
        name.split("/", 1)[1]: handler
        for name, (handler, _schema) in handlers.items()
        if name.startswith(f"{DOMAIN}/")
    }


async def test_every_command_declares_a_policy(hass: HomeAssistant) -> None:
    hub = await setup(hass)
    commands = registered(hass)
    assert set(commands) - {"subscribe"} == set(payloads(hub, "g"))
    for name, handler in commands.items():
        assert isinstance(getattr(handler, "laundry_policy", None), Policy), name
    member_ok = {
        name for name, h in commands.items() if h.laundry_policy is not Policy.ADMIN
    }
    # v0.2.4: members may enrol (garment/create); describe_fields and
    # reset_field became admin-only (Heather's answer). v0.2.6: members may
    # match and dismiss purchase cards assigned to them.
    assert member_ok == {
        "data", "api_info", "changes", "subscribe", "photo/url", "tap",
        "garment/create", "garment/update", "garment/wardrobe",
        "photo/upload", "photo/delete", "photo/remove", "photo/reorder",
        "purchases/match", "purchases/dismiss", "hello",
    }  # fmt: skip


@pytest.mark.parametrize("household", [False, True])
async def test_permission_matrix(
    hass: HomeAssistant, hass_ws_client, member, household: bool
) -> None:
    hub = await setup(hass, app_access="household" if household else "admins")
    admin, kid = await clients(hass, hass_ws_client, member)
    mine, theirs = garments(hub)
    spare = hub.enroll(uid="0A0B0C0D", name="Spare", owner=SAM)  # admin may delete it
    policies = {n: h.laundry_policy for n, h in registered(hass).items()}
    for name in payloads(hub, mine.id):
        own = payloads(hub, mine.id)[name]
        other = payloads(hub, theirs.id)[name]
        # Admins are never refused.
        msg = await admin(name, **payloads(hub, spare.id)[name])
        code = msg.get("error", {}).get("code")
        assert code not in ("unauthorized", "invalid_format"), ("admin", name, msg)
        msg = await kid(name, **own)
        refused = msg.get("error", {}).get("code") == "unauthorized"
        if not household or policies[name] is Policy.ADMIN:
            assert refused, ("member own", name)
            continue
        assert not refused, ("member own", name, msg)
        if policies[name] is Policy.OWN_GARMENT or name == "photo/url":
            msg = await kid(name, **other)
            assert msg["error"]["code"] == "unauthorized", ("member other", name)


async def test_member_field_limits_and_filtered_reads(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    hub = await setup(hass, app_access="household")
    admin, kid = await clients(hass, hass_ws_client, member)
    mine, theirs = garments(hub)
    hub.catalog.record_unknown("AABBCCDD", "closet_1", mine.created)
    bin_id = next(z.id for z in hub.zones.values() if z.type == "wash_bin")
    for payload in ({"owner": SAM}, {"wash_bin": bin_id}, {"add_uid": "01020304"}):
        msg = await kid("garment/update", garment_id=mine.id, **payload)
        assert msg["error"]["code"] == "unauthorized", payload
    msg = await kid("garment/wardrobe", garment_id=mine.id, brand="X")
    assert msg["error"]["code"] == "unauthorized"
    assert (await kid("garment/update", garment_id=mine.id, name="Mine!", notes="x"))[
        "success"
    ]
    size = {"label": "M", "system": "letter"}
    assert (
        await kid("garment/wardrobe", garment_id=mine.id, size=size, wear_for="work")
    )["success"]
    data = (await kid("data"))["result"]
    assert [g["name"] for g in data["garments"]] == ["Mine!"]
    assert data["unknown_tags"] == [] and data["last_unknown"] is None
    assert data["role"] == "member" and data["wash_bins"]
    assert data["purchases"]["cards"] == []
    # Purchase cards: only those matched to the member's garments.
    hub.catalog.purchases["c1"] = {
        "id": "c1",
        "status": "matched",
        "garment_id": mine.id,
        "photos": [],
    }
    hub.catalog.purchases["c2"] = {
        "id": "c2",
        "status": "matched",
        "garment_id": theirs.id,
        "photos": [],
    }
    hub.catalog.purchases["c3"] = {"id": "c3", "status": "new", "photos": []}
    hub.notify_changed()
    data = (await kid("data"))["result"]
    assert [c["id"] for c in data["purchases"]["mine"]] == ["c1"]
    changes = (await kid("changes", since_rev=0))["result"]
    assert {g["id"] for g in changes["garments"]} == {mine.id}
    assert [c["id"] for c in changes["purchases"]] == ["c1"]
    assert changes["unknown_tags"] == []
    # Someone else's garment shows up as "deleted" so the app drops it.
    assert {"id": theirs.id, "kind": "garment", "deleted": True}.items() <= next(
        t for t in changes["deleted"] if t["id"] == theirs.id
    ).items()
    # Admins see everything.
    assert len((await admin("data"))["result"]["garments"]) == 2


async def test_member_subscribe_sees_only_own_taps(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    hub = await setup(hass, app_access="household")
    admin, kid = await clients(hass, hass_ws_client, member)
    mine, theirs = garments(hub)
    assert (await kid("subscribe"))["success"]
    await hub.async_handle_tap("closet_1", theirs.uids[0])
    await hub.async_handle_tap("closet_1", mine.uids[0])
    taps = []
    for _ in range(3):  # changed, changed, own tap (the other tap is filtered)
        event = (await kid.raw.receive_json())["event"]
        if "tap" in event:
            taps.append(event["tap"]["garment_id"])
            assert isinstance(event["rev"], int)
    assert taps == [mine.id]


# --- phone taps --------------------------------------------------------------------------


async def test_phone_tap_unknown_and_known(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    hub = await setup(hass, app_access="household")
    admin, kid = await clients(hass, hass_ws_client, member)
    mine, theirs = garments(hub)
    events: list[Any] = []
    hass.bus.async_listen(TAP_EVENT, events.append)
    assert (await admin("subscribe"))["success"]  # the panel's Add page

    msg = await kid(
        "tap", uid="99:aa:bb:cc", source="phone", device_name="Alex's iPhone"
    )
    # v0.2.4 adds can_enroll / owner (additive).
    assert {"known": False, "uid": "99AABBCC", "rev": hub.revs.rev}.items() <= msg[
        "result"
    ].items()
    assert hub.catalog.unknown_tags["99AABBCC"]["station"] == "phone:Alex's iPhone"
    seen = [(await admin.raw.receive_json())["event"] for _ in range(2)]
    tap = next(e["tap"] for e in seen if "tap" in e)
    assert tap["event_type"] == "unknown" and tap["station"] == "phone:Alex's iPhone"
    assert events == []  # no fake station event: the app talks to HA directly

    msg = (await kid("tap", uid=mine.uids[0], source="phone"))["result"]
    assert msg["known"] is True and msg["garment"]["id"] == mine.id
    assert msg["available_actions"] == [
        "put_away:closet_1",
        "put_away:closet_2",
        f"in_wash:{mine.wash_bin}",
        f"drying:{mine.dry_type}",
    ]
    msg = await kid("tap", uid=theirs.uids[0], source="phone")
    assert msg["error"]["code"] == "unauthorized"
    msg = await kid("tap", uid="01020304", source="phone", action="put_away:closet_1")
    assert msg["error"]["code"] == "tag_not_enrolled"
    msg = await kid("tap", uid=mine.uids[0], source="phone", action="put_away:attic")
    assert msg["error"]["code"] == "invalid_action"


@pytest.mark.parametrize(
    ("action", "state", "location", "event_type"),
    [
        ("put_away:closet_2", "put_away", "closet_2", "put_away"),
        ("in_wash:BIN", "in_wash", "laundry", "in_wash"),
        ("drying:DRY", "drying", "laundry", "drying"),
    ],
)
async def test_phone_tap_actions_use_the_state_machine(
    hass: HomeAssistant,
    hass_ws_client,
    member,
    light_calls,
    action: str,
    state: str,
    location: str,
    event_type: str,
) -> None:
    hub = await setup(hass)
    admin, _ = await clients(hass, hass_ws_client, member)
    g = hub.enroll(uid="11223344", name="A")
    action = action.replace("BIN", g.wash_bin).replace("DRY", g.dry_type)
    rev = hub.garment_view(g)["rev"]
    msg = await admin(
        "tap", uid="11223344", source="phone", device_name="Kitchen iPad", action=action
    )
    result = msg["result"]
    assert result["applied"] == action and result["event_type"] == event_type
    assert result["garment"]["rev"] > rev
    assert (g.state, g.location) == (state, location)
    assert g.recent_taps[-1]["station"] == "phone:Kitchen iPad"
    assert hub.catalog.stations == {}  # station health untouched by phones
    entity = hass.states.get("event.laundry_hub_tap")
    assert entity.attributes["event_type"] == event_type
    assert entity.attributes["station"] == "phone:Kitchen iPad"
    # if_rev on a tap refers to the garment.
    msg = await admin("tap", uid="11223344", source="phone", action=action, if_rev=rev)
    assert msg["error"]["code"] == "conflict"


# --- photos ------------------------------------------------------------------------------


async def test_member_photo_access(
    hass: HomeAssistant, hass_ws_client, hass_client, member
) -> None:
    hub = await setup(hass, app_access="household")
    _admin, kid = await clients(hass, hass_ws_client, member)
    mine, theirs = garments(hub)
    own_photo = await hub.wardrobe.async_add_photo(mine, jpeg())
    other_photo = await hub.wardrobe.async_add_photo(theirs, jpeg())
    http = await hass_client(member[1])

    def form(gid: str) -> FormData:
        data = FormData()
        data.add_field("garment_id", gid)
        data.add_field(
            "file", io.BytesIO(jpeg()), filename="a.jpg", content_type="image/jpeg"
        )
        return data

    assert (
        await http.post("/api/laundry_hub/photos", data=form(mine.id))
    ).status == 200
    assert (
        await http.post("/api/laundry_hub/photos", data=form(theirs.id))
    ).status == 403
    assert (await http.get(f"/api/laundry_hub/photos/{own_photo}")).status == 200
    assert (await http.get(f"/api/laundry_hub/photos/{other_photo}")).status == 404
    assert (await http.get(f"/api/laundry_hub/photo/{theirs.id}")).status == 404
    assert (await http.get("/api/laundry_hub/photos/pending/t/0")).status == 404

    msg = (await kid("photo/url", garment_id=mine.id, filename=own_photo, size="full"))[
        "result"
    ]
    assert msg["expires_in"] == 24 * 3600 and "authSig=" in msg["url"]
    signed = msg["url"]
    anon = await hass_client(None)
    assert (await anon.get(signed)).status == 200  # the signature is the auth
    msg = await kid("photo/url", garment_id=mine.id, filename="nope.jpg")
    assert msg["error"]["code"] == "unknown_photo"
    msg = await kid("photo/url", garment_id=theirs.id, filename=other_photo)
    assert msg["error"]["code"] == "unauthorized"


async def test_household_off_member_gets_nothing_over_http(
    hass: HomeAssistant, hass_client, member
) -> None:
    hub = await setup(hass)
    mine, _ = garments(hub)
    photo = await hub.wardrobe.async_add_photo(mine, jpeg())
    http = await hass_client(member[1])
    assert (await http.get(f"/api/laundry_hub/photos/{photo}")).status == 404
    data = FormData()
    data.add_field("garment_id", mine.id)
    data.add_field(
        "file", io.BytesIO(jpeg()), filename="a.jpg", content_type="image/jpeg"
    )
    assert (await http.post("/api/laundry_hub/photos", data=data)).status == 401


# --- options: app access + people ---------------------------------------------------------


async def test_options_app_access_and_people(hass: HomeAssistant, member) -> None:
    hub = await setup(hass)
    user, _ = member
    flow = await hass.config_entries.options.async_init(hub.entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "settings"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "feedback_timeout": 2,
            "flash_duration": 15,
            "missing_after_days": 14,
            "wardrobe_enabled": True,
            "app_access": "household",
        },
    )
    assert flow["step_id"] == "app_people"
    keys = {str(k): k for k in flow["data_schema"].schema}
    assert keys["Alex Kid"].description == {"suggested_value": ALEX}  # person link
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"Alex Kid": SAM}
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hub.entry
    assert entry.options["app_access"] == "household"
    assert entry.options["user_owners"][user.id] == SAM
    from custom_components.laundry_hub.access import owner_for  # noqa: PLC0415

    hub = entry.runtime_data
    assert owner_for(hub, user.id) == SAM  # the mapping wins over the link
    assert owner_for(hub, None) is None
    # Back to admins only: no people step, mapping kept for later.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "settings"}
    )
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "feedback_timeout": 2,
            "flash_duration": 15,
            "missing_after_days": 14,
            "wardrobe_enabled": True,
            "app_access": "admins",
        },
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["app_access"] == "admins"
    assert entry.options["user_owners"][user.id] == SAM


# --- regression: the v0.2.2 panel's commands --------------------------------------------


async def test_panel_commands_unchanged_for_admins(
    hass: HomeAssistant, hass_ws_client, member
) -> None:
    """Every command the panel calls is still registered and open to admins."""
    hub = await setup(hass)
    admin, _ = await clients(hass, hass_ws_client, member)
    used = set()
    for path in FRONTEND.glob("*.js"):
        used |= set(re.findall(r'ws\("([a-z_/]+)"', path.read_text()))
        used |= set(re.findall(r'type: "laundry_hub/([a-z_/]+)"', path.read_text()))
    assert {"data", "subscribe", "garment/create", "purchases/match"} <= used
    commands = registered(hass)
    g = hub.enroll(uid="11223344", name="A")
    sample = payloads(hub, g.id)
    for name in sorted(used):
        assert name in commands, name
        if name == "subscribe":
            continue
        msg = await admin(name, **sample[name])
        assert msg.get("error", {}).get("code") not in ("unauthorized", "conflict"), (
            name
        )
