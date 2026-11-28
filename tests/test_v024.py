"""v0.2.4: translation placeholders in every flow step, member enrolment, member auto-describe."""

from __future__ import annotations

from collections.abc import Generator
from datetime import timedelta
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import FormData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowHandler
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry

from custom_components.laundry_hub import describe, mailbox
from custom_components.laundry_hub.config_flow import seed_subentries
from custom_components.laundry_hub.const import DOMAIN, ISSUE_PURCHASE_AUTH

from .conftest import OPTIONS
from .fake_imap import FakeImap, load
from .test_app_api import ALEX, SAM, Client, setup
from .test_wardrobe import jpeg

STRINGS = json.loads(
    (
        Path(__file__).parent.parent / "custom_components/laundry_hub/strings.json"
    ).read_text()
)
PASSWORD = "app-password-1234"
ACCOUNT = {"email": "x@example.com", "password": PASSWORD, "provider": "gmail"}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def needed(text: str | None) -> set[str]:
    return set(_PLACEHOLDER.findall(text or ""))


# --- 1. placeholder scan --------------------------------------------------------------


def section(flow: FlowHandler) -> tuple[str, dict[str, Any]]:
    """The strings.json section a flow's steps and aborts live in."""
    name = type(flow).__name__
    if name == "LaundryHubConfigFlow":
        return "config", STRINGS["config"]
    if name == "LaundryHubOptionsFlow":
        return "options", STRINGS["options"]
    if name == "PurchaseAuthRepairFlow":
        return "fix", STRINGS["issues"][ISSUE_PURCHASE_AUTH]["fix_flow"]
    zone = getattr(flow, "zone_type", None)
    return f"sub:{zone}", STRINGS["config_subentries"][zone]


@pytest.fixture
def rendered() -> Generator[list[tuple[str, str, str, set[str], set[str]]]]:
    """Record every form, menu and abort our flows render, with placeholders."""
    seen: list[tuple[str, str, str, set[str], set[str]]] = []
    show_form = FlowHandler.async_show_form
    show_menu = FlowHandler.async_show_menu
    abort = FlowHandler.async_abort

    def record(flow: FlowHandler, kind: str, key: str, given: Any) -> None:
        if not type(flow).__module__.startswith("custom_components.laundry_hub"):
            return
        name, strings = section(flow)
        if kind == "abort":
            text = strings.get("abort", {}).get(key, "")
        else:
            step = strings.get("step", {}).get(key, {})
            text = f"{step.get('title', '')} {step.get('description', '')}"
        seen.append((name, kind, key, needed(text), set(given or {})))

    def form(self: FlowHandler, **kwargs: Any) -> Any:
        record(
            self,
            "step",
            kwargs.get("step_id", ""),
            kwargs.get("description_placeholders"),
        )
        return show_form(self, **kwargs)

    def menu(self: FlowHandler, **kwargs: Any) -> Any:
        record(
            self,
            "step",
            kwargs.get("step_id", ""),
            kwargs.get("description_placeholders"),
        )
        return show_menu(self, **kwargs)

    def stop(self: FlowHandler, **kwargs: Any) -> Any:
        record(
            self,
            "abort",
            kwargs.get("reason", ""),
            kwargs.get("description_placeholders"),
        )
        return abort(self, **kwargs)

    with (
        patch.object(FlowHandler, "async_show_form", form),
        patch.object(FlowHandler, "async_show_menu", menu),
        patch.object(FlowHandler, "async_abort", stop),
    ):
        yield seen


async def entry_with(
    hass: HomeAssistant, *, wardrobe: bool, connected: bool, household: bool
) -> MockConfigEntry:
    hass.states.async_set("ai_task.test", "unknown")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Laundry Hub",
        data={"purchase_email": dict(ACCOUNT)} if connected else {},
        options={
            **OPTIONS,
            "wardrobe_enabled": wardrobe,
            "ai_task_entity": "ai_task.test",
            "app_access": "household" if household else "admins",
            "purchase_stores": ["old_navy"] if connected else [],
        },
        subentries_data=seed_subentries(),
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def walk_options(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Open every options step reachable in this state (and back out)."""
    flows = hass.config_entries.options
    first = await flows.async_init(entry.entry_id)
    if first["type"] == "form":  # Wardrobe off: the plain settings form
        flows.async_abort(first["flow_id"])
        return
    for step in first["menu_options"]:
        flow = await flows.async_init(entry.entry_id)
        result = await flows.async_configure(flow["flow_id"], {"next_step_id": step})
        if result["type"] == "form":
            if step == "email_account":  # also show the generic-IMAP server step
                result = await flows.async_configure(
                    result["flow_id"],
                    {
                        "email": "x@example.com",
                        "password": PASSWORD,
                        "provider": "imap",
                    },
                )
            if step == "settings":  # household → the people step
                result = await flows.async_configure(
                    result["flow_id"],
                    {
                        "feedback_timeout": 2,
                        "flash_duration": 15,
                        "missing_after_days": 14,
                        "wardrobe_enabled": True,
                        "app_access": "household",
                    },
                )
            if result["type"] == "form":
                flows.async_abort(result["flow_id"])
            await hass.async_block_till_done()


@pytest.mark.parametrize("wardrobe", [False, True])
@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("household", [False, True])
async def test_every_rendered_step_has_its_placeholders(
    hass: HomeAssistant, rendered, wardrobe: bool, connected: bool, household: bool
) -> None:
    """Heather's log: init rendered without {status} 47 times. Never again."""
    hass.states.async_set(ALEX, "home", {"user_id": None})
    await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    # Connected with zero cards: the mailbox is empty.
    server = FakeImap({}, password=PASSWORD)
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(mailbox.MailboxSession, "search", AsyncMock(return_value=[])),
    ):
        entry = await entry_with(
            hass, wardrobe=wardrobe, connected=connected, household=household
        )
        await walk_options(hass, entry)
        # Zones: add and reconfigure a wash bin and a dry type.
        for zone_type in ("wash_bin", "dry_type"):
            flow = await hass.config_entries.subentries.async_init(
                (entry.entry_id, zone_type), context={"source": "user"}
            )
            hass.config_entries.subentries.async_abort(flow["flow_id"])
            sub_id = next(
                sid
                for sid, s in entry.subentries.items()
                if s.subentry_type == zone_type
            )
            flow = await entry.start_subentry_reconfigure_flow(hass, sub_id)
            hass.config_entries.subentries.async_abort(flow["flow_id"])
    problems = [
        f"{name} {kind} {key}: missing {sorted(need - given)}"
        for name, kind, key, need, given in rendered
        if need - given
    ]
    assert problems == []
    # The bug itself: with Wardrobe off, "init" (whose text needs {status}) is
    # never shown; the plain settings form is.
    shown = {(name, key) for name, kind, key, _n, _g in rendered if kind == "step"}
    if not wardrobe:
        assert ("options", "init") not in shown
        assert ("options", "settings") in shown


async def test_placeholder_scan_covers_every_step(
    hass: HomeAssistant, rendered, hass_ws_client
) -> None:
    """Every step and placeholder-bearing abort in strings.json is rendered by
    some test path here, so a new step can't slip by unchecked."""
    hass.states.async_set(ALEX, "home", {"user_id": None})
    await hass.auth.async_create_user("Kid", group_ids=["system-users"])
    server = FakeImap({1: load("old_navy.eml")}, password=PASSWORD)
    with patch.object(mailbox, "_create_client", server):
        entry = await entry_with(hass, wardrobe=True, connected=True, household=True)
        await walk_options(hass, entry)
        # Config flow user step (a second entry is refused: abort).
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert flow["type"] == "abort"
        await hass.config_entries.async_remove(entry.entry_id)
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert flow["step_id"] == "user"
        hass.config_entries.flow.async_abort(flow["flow_id"])
        entry = await entry_with(hass, wardrobe=True, connected=True, household=True)
        # Check now: success and failure aborts.
        with patch(
            "custom_components.laundry_hub.purchases.PurchaseInbox.async_check",
            AsyncMock(return_value={"new_cards": 0, "updated_cards": 0}),
        ):
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            await hass.config_entries.options.async_configure(
                flow["flow_id"], {"next_step_id": "email_check"}
            )
        # Retry unreadable emails (menu option + retry_done abort).
        entry.runtime_data.purchases.meta["unreadable"] = {
            "<x@y>": {
                "message_id": "<x@y>",
                "store": "Outdoor Research",
                "date": "2026-11-20T10:00:00+00:00",
                "sender_domain": "outdoorresearch.narvar.com",
                "error": "HomeAssistantError",
                "uid": 1,
                "folder": "imap.gmail.com:993/[Gmail]/All Mail",
            }
        }
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        assert "email_retry" in flow["menu_options"]
        assert (
            "1 email couldn't be read (Outdoor Research, Nov 20)"
            in (flow["description_placeholders"]["status"])
        )
        with patch(
            "custom_components.laundry_hub.purchases.PurchaseInbox.async_check",
            AsyncMock(return_value={"new_cards": 0, "updated_cards": 0}),
        ):
            await hass.config_entries.options.async_configure(
                flow["flow_id"], {"next_step_id": "email_retry"}
            )
        server.password = "wrong"
        entry.runtime_data.purchases._next_allowed = None  # noqa: SLF001
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "email_check"}
        )
        # Zone flows, including deleting an unused zone (zone_removed abort).
        for zone_type in ("wash_bin", "dry_type"):
            flow = await hass.config_entries.subentries.async_init(
                (entry.entry_id, zone_type), context={"source": "user"}
            )
            hass.config_entries.subentries.async_abort(flow["flow_id"])
            sub_id = next(
                sid
                for sid, s in entry.subentries.items()
                if s.subentry_type == zone_type
            )
            flow = await entry.start_subentry_reconfigure_flow(hass, sub_id)
            await hass.config_entries.subentries.async_configure(
                flow["flow_id"],
                {"name": "x", "color": [1, 2, 3], "order": 0, "delete": True},
            )
            await hass.async_block_till_done()
        # Repair fix flow (both steps).
        assert await async_setup_component(hass, "repairs", {})
        entry.runtime_data.purchases._raise_auth_issue()  # noqa: SLF001
        manager = hass.data["repairs"]["flow_manager"]
        flow = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_PURCHASE_AUTH})
        await manager.async_configure(
            flow["flow_id"],
            {"email": "x@example.com", "password": "p", "provider": "imap"},
        )
    problems = [
        f"{name} {kind} {key}: missing {sorted(need - given)}"
        for name, kind, key, need, given in rendered
        if need - given
    ]
    assert problems == []
    shown = {(name, kind, key) for name, kind, key, _n, _g in rendered}
    expected: set[tuple[str, str, str]] = set()
    for step in STRINGS["config"]["step"]:
        expected.add(("config", "step", step))
    for step in STRINGS["options"]["step"]:
        expected.add(("options", "step", step))
    for zone_type, strings in STRINGS["config_subentries"].items():
        for step in strings["step"]:
            expected.add((f"sub:{zone_type}", "step", step))
    for step in STRINGS["issues"][ISSUE_PURCHASE_AUTH]["fix_flow"]["step"]:
        expected.add(("fix", "step", step))
    for sec in ("config", "options"):
        for reason, text in STRINGS[sec].get("abort", {}).items():
            if needed(text):
                expected.add((sec, "abort", reason))
    for zone_type, strings in STRINGS["config_subentries"].items():
        for reason, text in strings.get("abort", {}).items():
            if needed(text):
                expected.add((f"sub:{zone_type}", "abort", reason))
    assert expected - shown == set()


async def test_repair_issues_carry_their_placeholders(hass: HomeAssistant) -> None:
    """Issues are rendered by the frontend too: check every one we create."""
    hub = await setup(hass)
    g = hub.enroll(uid="11223344", name="A")
    hub.purchases._raise_auth_issue()  # noqa: SLF001
    bin_sub = next(
        s for s in hub.entry.subentries.values() if s.subentry_id == g.wash_bin
    )
    hass.config_entries.async_remove_subentry(hub.entry, bin_sub.subentry_id)
    await hass.async_block_till_done()
    issues = [
        issue
        for (domain, _), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    ]
    assert {i.translation_key for i in issues} >= {"zone_restored", ISSUE_PURCHASE_AUTH}
    for issue in issues:
        strings = STRINGS["issues"][issue.translation_key]
        need = needed(strings.get("title")) | needed(strings.get("description"))
        assert need <= set(issue.translation_placeholders or {}), issue.translation_key


# --- 2c. member enrolment ---------------------------------------------------------------


async def make_member(
    hass: HomeAssistant, name: str, person: str | None
) -> tuple[Any, str]:
    user = await hass.auth.async_create_user(name, group_ids=["system-users"])
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    if person:
        hass.states.async_set(person, "home", {"user_id": user.id})
    return user, hass.auth.async_create_access_token(refresh)


@pytest.fixture
async def tokens(hass: HomeAssistant, hass_admin_user: Any) -> dict[str, str | None]:
    """Users: alex (mapped), sam (mapped), nobody (no person); admin = default."""
    _, alex_token = await make_member(hass, "Alex", ALEX)
    _, sam_token = await make_member(hass, "Sam", SAM)
    _, nobody_token = await make_member(hass, "Guest", None)
    return {"alex": alex_token, "sam": sam_token, "nobody": nobody_token}


async def connect(
    hass: HomeAssistant, hass_ws_client: Any, tokens: dict[str, str | None]
) -> dict[str, Client]:
    """Websocket clients (after the integration is set up)."""
    out = {"admin": Client(await hass_ws_client(hass))}
    for name, token in tokens.items():
        out[name] = Client(await hass_ws_client(hass, token))
    return out


def create(hub: Any, uid: str, **extra: Any) -> dict[str, Any]:
    bin_id = next(z.id for z in hub.zones.values() if z.type == "wash_bin")
    dry_id = next(z.id for z in hub.zones.values() if z.type == "dry_type")
    return {
        "uid": uid,
        "name": "New jumper",
        "wash_bin": bin_id,
        "dry_type": dry_id,
        **extra,
    }


async def test_member_enrols_own_tapped_tag(
    hass: HomeAssistant, tokens, hass_ws_client, freezer
) -> None:
    hub = await setup(hass, app_access="household")
    people = await connect(hass, hass_ws_client, tokens)
    alex, sam, admin = people["alex"], people["sam"], people["admin"]
    info = (await alex("api_info"))["result"]
    assert "member_enroll" in info["capabilities"]

    # Station tap of an unknown tag: Alex may not enrol it (didn't tap it).
    await hub.async_handle_tap("closet_1", "AABBCCDD")
    msg = await alex("garment/create", **create(hub, "AABBCCDD"))
    assert msg["error"]["code"] == "tag_not_yours"

    # Alex taps a new tag with the phone: the answer offers enrolment.
    tapped = (await alex("tap", uid="11:22:33:44", source="phone", device_name="Alex"))[
        "result"
    ]
    assert tapped["known"] is False and tapped["can_enroll"] is True
    assert tapped["owner"] == ALEX
    # Alex sees only that tag, never the household list (or the station one).
    data = (await alex("data"))["result"]
    assert [t["uid"] for t in data["unknown_tags"]] == ["11223344"]
    assert "tapped_by" not in data["unknown_tags"][0]
    changes = (await alex("changes", since_rev=0))["result"]
    assert [t["uid"] for t in changes["unknown_tags"]] == ["11223344"]
    # Sam doesn't see Alex's tag and can't enrol it.
    assert (await sam("data"))["result"]["unknown_tags"] == []
    msg = await sam("garment/create", **create(hub, "11223344"))
    assert msg["error"]["code"] == "tag_not_yours"
    # The admin sees the whole list.
    admin_tags = {t["uid"] for t in (await admin("data"))["result"]["unknown_tags"]}
    assert admin_tags == {"AABBCCDD", "11223344"}

    # Admin-only fields and other owners are refused, nothing is created.
    msg = await alex("garment/create", **create(hub, "11223344", owner=SAM))
    assert msg["error"]["code"] == "owner_not_allowed"
    for field, value in (("location", "closet_1"), ("brand", "X")):
        msg = await alex("garment/create", **create(hub, "11223344", **{field: value}))
        assert msg["error"]["code"] in ("unauthorized", "invalid_format"), field
    assert hub.catalog.by_uid("11223344") is None

    # Enrol: the owner is forced to Alex, the tag leaves the unknown list.
    msg = await alex(
        "garment/create", **create(hub, "11223344", owner=ALEX, notes="soft")
    )
    garment = msg["result"]
    assert garment["owner"] == ALEX and garment["name"] == "New jumper"
    assert "11223344" not in hub.catalog.unknown_tags
    assert (await alex("data"))["result"]["unknown_tags"] == []
    # Without an owner value it's still Alex's.
    await alex("tap", uid="55667788", source="phone")
    msg = await alex("garment/create", **create(hub, "55667788"))
    assert msg["result"]["owner"] == ALEX
    # Member edits after enrolment: size and wear_for yes; uniform/purchase no.
    gid = msg["result"]["id"]
    size = {"label": "M", "system": "letter"}
    assert (await alex("garment/wardrobe", garment_id=gid, size=size, wear_for="work"))[
        "success"
    ]
    msg = await alex("garment/wardrobe", garment_id=gid, uniform=True)
    assert msg["error"]["code"] == "unauthorized"

    # "Just tapped": after 30 minutes the tag is no longer the member's to enrol.
    await alex("tap", uid="0A0B0C0D", source="phone")
    freezer.tick(timedelta(minutes=31))
    assert (await alex("data"))["result"]["unknown_tags"] == []
    msg = await alex("garment/create", **create(hub, "0A0B0C0D"))
    assert msg["error"]["code"] == "tag_not_yours"
    # A later station tap of the same tag clears who tapped it.
    await alex("tap", uid="0A0B0C0D", source="phone")
    await hub.async_handle_tap("laundry", "0A0B0C0D")
    msg = await alex("garment/create", **create(hub, "0A0B0C0D"))
    assert msg["error"]["code"] == "tag_not_yours"


@pytest.mark.parametrize("household", [False, True])
async def test_enrol_matrix(
    hass: HomeAssistant, tokens, hass_ws_client, household: bool
) -> None:
    """garment/create and tap→enrol: admin/member × household × mapped/unmapped × owner."""
    hub = await setup(hass, app_access="household" if household else "admins")
    people = await connect(hass, hass_ws_client, tokens)
    cases = [
        # client, owner sent, expected (household on); household off: all members refused
        ("admin", SAM, "ok"),
        ("alex", None, "ok"),
        ("alex", ALEX, "ok"),
        ("alex", SAM, "owner_not_allowed"),
        ("nobody", None, "no_owner_mapping"),
        ("nobody", ALEX, "no_owner_mapping"),
    ]
    for index, (who, owner, outcome) in enumerate(cases):
        expected = outcome
        uid = f"0102030{index}"
        client = people[who]
        tap = await client("tap", uid=uid, source="phone")
        if not household and who != "admin":
            assert tap["error"]["code"] == "unauthorized"
            expected = "unauthorized"
        else:
            assert tap["result"]["can_enroll"] is (who != "nobody")
        payload = create(hub, uid, **({"owner": owner} if owner else {}))
        msg = await client("garment/create", **payload)
        if expected == "ok":
            assert msg["success"], (who, owner, msg)
            assert msg["result"]["owner"] == (owner or ALEX)
        else:
            assert msg["error"]["code"] == expected, (who, owner, msg)


async def test_member_without_mapping_cannot_enrol_from_tap(
    hass: HomeAssistant, tokens, hass_ws_client
) -> None:
    hub = await setup(hass, app_access="household", user_owners={})
    people = await connect(hass, hass_ws_client, tokens)
    tap = (await people["nobody"]("tap", uid="01020304", source="phone"))["result"]
    assert tap["can_enroll"] is False and tap["owner"] is None
    info = (await people["nobody"]("api_info"))["result"]
    assert info["owner"] is None
    assert hub.catalog.by_uid("01020304") is None


async def test_member_enroll_capability_only_with_household(
    hass: HomeAssistant, tokens, hass_ws_client
) -> None:
    await setup(hass)
    people = await connect(hass, hass_ws_client, tokens)
    caps = (await people["admin"]("api_info"))["result"]["capabilities"]
    assert "member_enroll" not in caps


# --- 2a. member AI describe: automatic only --------------------------------------------


async def test_member_first_photo_auto_describes_own_garment_only(
    hass: HomeAssistant, hass_client, tokens, hass_ws_client
) -> None:
    hub = await setup(hass, app_access="household", ai_task_entity="ai_task.test")
    hass.states.async_set("ai_task.test", "unknown")
    mine = hub.enroll(uid="11223344", name="Alex hoodie", owner=ALEX)
    theirs = hub.enroll(uid="55667788", name="Sam shirt", owner=SAM)
    alex_user = next(u for u in await hass.auth.async_get_users() if u.name == "Alex")
    refresh = await hass.auth.async_create_refresh_token(alex_user, CLIENT_ID)
    http = await hass_client(hass.auth.async_create_access_token(refresh))
    ai = AsyncMock(
        return_value=SimpleNamespace(
            data={"type": "top", "summary": "A hoodie."}, conversation_id="c"
        )
    )

    def form(gid: str) -> FormData:
        data = FormData()
        data.add_field("garment_id", gid)
        data.add_field(
            "file", io.BytesIO(jpeg()), filename="a.jpg", content_type="image/jpeg"
        )
        return data

    with patch.object(describe, "_generate_data", ai):
        assert (
            await http.post("/api/laundry_hub/photos", data=form(theirs.id))
        ).status == 403
        await hass.async_block_till_done(wait_background_tasks=True)
        assert ai.await_count == 0  # never for someone else's garment
        assert (
            await http.post("/api/laundry_hub/photos", data=form(mine.id))
        ).status == 200
        await hass.async_block_till_done(wait_background_tasks=True)
        assert ai.await_count == len(describe.GROUPS)  # first photo, own garment
        assert mine.description["summary"] == "A hoodie."
        # Runs under the integration's configured AI Task entity.
        assert ai.await_args.kwargs["entity_id"] == "ai_task.test"
        assert (
            await http.post("/api/laundry_hub/photos", data=form(mine.id))
        ).status == 200
        await hass.async_block_till_done(wait_background_tasks=True)
        assert ai.await_count == len(describe.GROUPS)  # second photo: no new call

    alex = (await connect(hass, hass_ws_client, tokens))["alex"]
    for type_, payload in (
        ("garment/describe", {}),
        ("garment/describe_fields", {"fields": {"pattern": "solid"}}),
        ("garment/reset_field", {"field": "pattern"}),
    ):
        msg = await alex(type_, garment_id=mine.id, **payload)
        assert msg["error"]["code"] == "unauthorized", type_
