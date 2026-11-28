"""v0.2.1 purchase inbox: fake IMAP server, recorded order emails, mocked AI Task."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.laundry_hub import extract, importer, mailbox
from custom_components.laundry_hub.config_flow import seed_subentries
from custom_components.laundry_hub.const import DOMAIN, ISSUE_PURCHASE_AUTH
from custom_components.laundry_hub.hub import LaundryHub

from .conftest import OPTIONS as BASE_OPTIONS
from .fake_imap import FakeImap, load
from .test_wardrobe import jpeg

PASSWORD = "app-password-1234"
ADDRESS = "shopper@example.com"
ACCOUNT = {"email": ADDRESS, "password": PASSWORD, "provider": "gmail"}
OPTIONS = {
    "wardrobe_enabled": True,
    "ai_task_entity": "ai_task.test",
    "purchase_stores": ["amazon", "old_navy", "target"],
    "purchase_custom_domains": [],
    "purchase_interval": 30,
    "purchase_backfill": 90,
    "purchase_clothing_only": True,
    "purchase_archive_days": 60,
}
JPEG = {"content-type": "image/jpeg"}
IMAGES = (
    "https://m.media-amazon.com/images/I/cardigan.jpg",
    "https://m.media-amazon.com/images/I/socks.jpg",
    "https://oldnavy.gap.com/webcontent/jeans.jpg",
    "https://oldnavy.gap.com/webcontent/jeans-large.jpg",
    "https://target.scene7.com/is/image/tee.jpg",
)

ANSWERS: dict[str, dict[str, Any]] = {
    "amazon": {
        "is_order": True,
        "store": "Amazon",
        "order_ref": "112-7654321-1234567",
        "order_date": "2026-11-20",
        "currency": "USD",
        "items": [
            {
                "name": "Women's Rib-Knit Cardigan, Navy",
                "color": "Navy",
                "size": "M",
                "quantity": 1,
                "unit_price": 34.99,
                "product_url": "https://www.amazon.com/dp/B0CARDIGAN?ref=x&th=1",
                "image_url": "https://m.media-amazon.com/images/I/cardigan.jpg",
                "is_apparel": True,
            },
            {
                "name": "Wool Hiking Socks (3 pairs)",
                "color": "Grey",
                "size": "L",
                "quantity": 2,
                "unit_price": 19.5,
                "product_url": "https://www.amazon.com/dp/B0INVENTED",  # not in the email
                "image_url": "https://m.media-amazon.com/images/I/socks.jpg",
                "is_apparel": True,
            },
            {"name": "AA Batteries, 24 pack", "unit_price": 15.99, "is_apparel": False},
        ],
    },
    "old_navy": {
        "is_order": True,
        "order_ref": "ON-99812",
        "order_date": "2026-11-22",
        "items": [
            {
                "name": "High-Waisted Wow Jeans",
                "brand": "Old Navy",
                "color": "Black",
                "size": "8 Tall",
                "unit_price": 44.99,
                "product_url": "https://oldnavy.gap.com/browse/product.do?pid=123",
                "image_url": "https://oldnavy.gap.com/webcontent/jeans.jpg",
                "is_apparel": True,
            }
        ],
    },
    "old_navy_shipped": {
        "is_order": True,
        "order_ref": "ON-99812",
        "items": [
            {
                "name": "High-Waisted Wow Jeans",
                "image_url": "https://oldnavy.gap.com/webcontent/jeans-large.jpg",
                "is_apparel": True,
            },
        ],
    },
    "target": {
        "is_order": True,
        "order_ref": "9012345678",
        "items": [
            {
                "name": "Women's Short Sleeve T-Shirt",
                "brand": "Universal Thread",
                "color": "White",
                "size": "XS",
                "unit_price": 8.0,
                "image_url": "https://target.scene7.com/is/image/tee.jpg",
                "is_apparel": True,
            },
            {"name": "Desk Lamp", "unit_price": 12.0, "is_apparel": False},
        ],
    },
}


def ai_answer(**kwargs: Any) -> SimpleNamespace:
    """Mocked ai_task: answers by store; never sees the newsletter."""
    text: str = kwargs["instructions"]
    assert "SECRET-NEWSLETTER-BODY" not in text
    if "Amazon" in text:
        key = "amazon"
    elif "on its way" in text:
        key = "old_navy_shipped"
    elif "Old Navy" in text:
        key = "old_navy"
    elif "Target" in text:
        key = "target"
    else:  # pragma: no cover - would be a test bug
        raise AssertionError("unexpected email")
    return SimpleNamespace(data=ANSWERS[key], conversation_id="c")


@pytest.fixture(autouse=True)
def _now(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-27 12:00:00+00:00")


@pytest.fixture
def public_dns():
    """No live DNS: image hosts resolve to a public address."""
    with patch.object(
        importer, "_resolve", AsyncMock(return_value=["93.184.216.34"])
    ) as mock:
        yield mock


@pytest.fixture
def images(aioclient_mock: AiohttpClientMocker, public_dns) -> AiohttpClientMocker:
    """Every product image downloads as a small JPEG."""
    for url in IMAGES:
        aioclient_mock.get(url, content=jpeg(), headers=JPEG)
    return aioclient_mock


@pytest.fixture
def ai() -> Any:
    """Mock AI Task."""
    mock = AsyncMock(side_effect=lambda hass, **kw: ai_answer(**kw))
    with patch.object(extract, "_generate_data", mock):
        yield mock


def mailbox_of(*names: str, **kwargs: Any) -> FakeImap:
    """A fake mailbox with recorded emails at UIDs 1..n."""
    return FakeImap(
        {i: load(name) for i, name in enumerate(names, start=1)},
        password=PASSWORD,
        **kwargs,
    )


@pytest.fixture
def fake() -> Any:
    """The fake server every IMAP connection goes to."""
    server = mailbox_of(
        "amazon_multi.eml",
        "old_navy.eml",
        "newsletter.eml",
        "target_mixed.eml",
        "old_navy_shipped.eml",
    )
    with patch.object(mailbox, "_create_client", server):
        yield server


async def setup_hub(
    hass: HomeAssistant,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> LaundryHub:
    hass.states.async_set("ai_task.test", "unknown")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Laundry Hub",
        data={"purchase_email": dict(ACCOUNT)} if data is None else data,
        options={**BASE_OPTIONS, **OPTIONS, **(options or {})},
        subentries_data=seed_subentries(),
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def cards_by_name(hub: LaundryHub) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in hub.purchases.cards.values()}


# --- the full check ----------------------------------------------------------------


async def test_check_builds_cards_read_only_and_opt_in_only(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any
) -> None:
    hub = await setup_hub(hass)
    summary = await hub.purchases.async_check(manual=True)

    # Read-only: EXAMINE, never SELECT; every connection logs out.
    names = [c[0] for c in fake.commands]
    assert ("EXAMINE", '"[Gmail]/All Mail"') in fake.commands
    assert "SELECT" not in names
    assert fake.logouts == fake.logins == 1
    search = next(c for c in fake.commands if c[0] == "UID SEARCH")
    assert search[1] == "X-GM-RAW"
    assert "from:(amazon.com OR oldnavy.com OR target.com) newer_than:90d" in search[2]

    # The look-alike newsletter came back from the (substring) search; only its
    # headers were read. Its body was never fetched or sent to the AI.
    assert 3 in fake.header_fetches
    assert all(uid != 3 for uid, _ in fake.body_fetches)
    assert summary["skipped_senders"] == 1
    # Text parts only: the Amazon PDF attachment (part 2) was never fetched.
    assert {s for uid, s in fake.body_fetches if uid == 1} == {"1.1", "1.2"}

    cards = cards_by_name(hub)
    assert set(cards) == {
        "Women's Rib-Knit Cardigan, Navy",
        "Wool Hiking Socks (3 pairs)",
        "High-Waisted Wow Jeans",
        "Women's Short Sleeve T-Shirt",
    }  # batteries and the lamp are not clothing; the shipping email added nothing
    assert summary["new_cards"] == 4 and summary["updated_cards"] == 0
    cardigan = cards["Women's Rib-Knit Cardigan, Navy"]
    assert (
        cardigan["store"] == "Amazon" and cardigan["order_ref"] == "112-7654321-1234567"
    )
    assert cardigan["product_url"] == "https://www.amazon.com/dp/B0CARDIGAN?ref=x&th=1"
    assert cardigan["unit_price"] == 34.99 and cardigan["order_date"] == "2026-11-20"
    assert len(cardigan["photos"]) == 1
    assert Path(hass.config.path("laundry_hub/photos", cardigan["photos"][0])).is_file()
    # The invented link is dropped; the card keeps everything else.
    socks = cards["Wool Hiking Socks (3 pairs)"]
    assert socks["product_url"] is None and socks["quantity"] == 2
    # Only card fields are stored: no body, subject or address.
    stored = str(hub.catalog._data())  # noqa: SLF001
    for secret in (ADDRESS, PASSWORD, "Thanks for your order", "SECRET-NEWSLETTER"):
        assert secret not in stored
    assert hass.states.get("sensor.laundry_hub_unmatched_purchases").state == "4"

    # De-duplication: a second check finds nothing new and asks the AI nothing.
    calls = ai.await_count
    assert calls == 4  # amazon, old navy, target, shipping update
    again = await hub.purchases.async_check(manual=True)
    assert again["new_cards"] == 0 and ai.await_count == calls

    # UIDVALIDITY changed: the backfill window is searched again, Message-IDs
    # de-duplicate, and still nothing new is created.
    fake.uidvalidity = 99
    third = await hub.purchases.async_check(manual=True)
    assert third["new_cards"] == 0 and ai.await_count == calls
    assert len(hub.purchases.cards) == 4


async def test_shipping_update_merges_into_existing_card(
    hass: HomeAssistant, ai: AsyncMock, aioclient_mock: AiohttpClientMocker, public_dns
) -> None:
    aioclient_mock.get(IMAGES[2], status=404)  # confirmation photo fails
    aioclient_mock.get(IMAGES[3], content=jpeg(), headers=JPEG)
    server = mailbox_of("old_navy.eml")
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass, {"purchase_stores": ["old_navy"]})
        await hub.purchases.async_check(manual=True)
        (card,) = hub.purchases.cards.values()
        assert card["photos"] == []  # kept without a photo
        server.messages[2] = load("old_navy_shipped.eml")
        summary = await hub.purchases.async_check(manual=True)
    assert summary == {
        "messages": 1,
        "skipped_senders": 0,
        "new_cards": 0,
        "updated_cards": 1,
        "error": None,
    }
    (merged,) = hub.purchases.cards.values()
    assert merged["id"] == card["id"] and len(merged["photos"]) == 1
    assert merged["brand"] == "Old Navy"  # confirmation details kept


async def test_clothing_only_off_keeps_everything(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any
) -> None:
    hub = await setup_hub(hass, {"purchase_clothing_only": False})
    await hub.purchases.async_check(manual=True)
    assert {"AA Batteries, 24 pack", "Desk Lamp"} <= set(cards_by_name(hub))


async def test_generic_imap_uses_since_from_and_subject_filter(
    hass: HomeAssistant, ai: AsyncMock, images: Any
) -> None:
    server = mailbox_of("amazon_multi.eml", "newsletter.eml", folder="INBOX")
    account = {
        "email": ADDRESS,
        "password": PASSWORD,
        "provider": "imap",
        "host": "imap.example.com",
        "port": 993,
        "folder": "INBOX",
    }
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(
            hass,
            {"purchase_stores": ["amazon"], "purchase_custom_domains": ["oldnavy.com"]},
            {"purchase_email": account},
        )
        await hub.purchases.async_check(manual=True)
    searches = [c for c in server.commands if c[0] == "UID SEARCH"]
    assert searches == [
        ("UID SEARCH", "SINCE", "29-Aug-2026", "FROM", '"amazon.com"'),
        ("UID SEARCH", "SINCE", "29-Aug-2026", "FROM", '"oldnavy.com"'),
    ]
    assert all(uid != 2 for uid, _ in server.body_fetches)  # look-alike sender
    assert len(hub.purchases.cards) == 2


async def test_backfill_none_only_reads_new_mail(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any
) -> None:
    hub = await setup_hub(hass, {"purchase_backfill": 0})
    summary = await hub.purchases.async_check(manual=True)
    assert summary["new_cards"] == 0 and ai.await_count == 0
    assert not any(c[0] == "UID SEARCH" for c in fake.commands)
    fake.messages[6] = load("target_mixed.eml").replace(b"<tgt-1@", b"<tgt-2@")
    summary = await hub.purchases.async_check(manual=True)
    assert summary["new_cards"] == 1 and ai.await_count == 1


async def test_ai_failure_retries_from_that_message(
    hass: HomeAssistant, fake: FakeImap, images: Any
) -> None:
    hub = await setup_hub(hass, {"purchase_stores": ["amazon"]})
    with patch.object(
        extract, "_generate_data", AsyncMock(side_effect=HomeAssistantError("down"))
    ):
        summary = await hub.purchases.async_check(manual=True)
    assert summary["error"] == "AIFailed" and not hub.purchases.cards
    assert hub.purchases.meta["last_error"] == "AIFailed"
    with patch.object(
        extract,
        "_generate_data",
        AsyncMock(side_effect=lambda hass, **kw: ai_answer(**kw)),
    ):
        summary = await hub.purchases.async_check(manual=True)
    assert summary["new_cards"] == 2 and summary["error"] is None
    # A malformed answer and a provider crash are handled the same way.
    for bad in (
        AsyncMock(return_value=SimpleNamespace(data="nope")),
        AsyncMock(side_effect=RuntimeError),
    ):
        fake.messages[9] = load("target_mixed.eml").replace(b"<tgt-1@", b"<tgt-9@")
        with patch.object(extract, "_generate_data", bad):
            hub.purchases.opted  # noqa: B018
            hass.config_entries.async_update_entry(
                hub.entry, options={**hub.entry.options, "purchase_stores": ["target"]}
            )
            await hass.async_block_till_done()
            hub = hub.entry.runtime_data
            assert (await hub.purchases.async_check(manual=True))["error"] == "AIFailed"


# --- privacy ---------------------------------------------------------------------


async def test_no_secrets_in_logs_or_diagnostics(
    hass: HomeAssistant,
    hass_client: Any,
    fake: FakeImap,
    ai: AsyncMock,
    images: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert await async_setup_component(hass, "diagnostics", {})
    hub = await setup_hub(hass)
    await hub.purchases.async_check(manual=True)
    fake.password = "rotated"  # now login fails: the warning path logs too
    await hub.purchases.async_check()
    diag = await get_diagnostics_for_config_entry(hass, hass_client, hub.entry)
    text = caplog.text + str(diag)
    for secret in (
        PASSWORD,
        ADDRESS,
        "shopper",
        "SECRET-NEWSLETTER-BODY",
        "Your Amazon.com order",
        "Thanks for your order",
        "auto-confirm",
    ):
        assert secret not in text, secret
    purchases = diag["purchases"]
    assert purchases["stores"] == ["amazon", "old_navy", "target"]
    assert purchases["cards"] == {"new": 4}
    assert purchases["last_error"] == "AuthFailed"
    assert purchases["connected"] is True and purchases["provider"] == "gmail"
    assert "purchase_email" not in str(diag["options"])


def test_aioimaplib_debug_is_muted_during_sessions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    lib = logging.getLogger("aioimaplib.aioimaplib")
    mailbox._QUIET.active += 1  # noqa: SLF001
    try:
        lib.debug("Received : BODY-WITH-SECRETS")
        lib.info("unknown data received BODY-WITH-SECRETS")
        lib.warning("visible warning")
    finally:
        mailbox._QUIET.active -= 1  # noqa: SLF001
    lib.debug("outside our sessions")
    assert "BODY-WITH-SECRETS" not in caplog.text
    assert "visible warning" in caplog.text and "outside our sessions" in caplog.text
    assert repr(mailbox.account_from_data(ACCOUNT)) == (
        "Account(provider='gmail', host='imap.gmail.com')"
    )


# --- auth failure, repair, backoff ---------------------------------------------------


async def test_auth_failure_raises_repair_and_fix_flow(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, hass_client: Any
) -> None:
    fake.password = "revoked"
    hub = await setup_hub(hass)
    with pytest.raises(ServiceValidationError):
        await hub.purchases.async_check(manual=True)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PURCHASE_AUTH)
    assert issue is not None and issue.is_fixable
    # Scheduled checks back off after a failure.
    assert (await hub.purchases.async_check())["skipped"] == "backoff"

    assert await async_setup_component(hass, "repairs", {})
    from homeassistant.components.repairs import (  # noqa: PLC0415
        DOMAIN as REPAIRS,
    )

    flow = await hass.data[REPAIRS]["flow_manager"].async_init(
        DOMAIN, data={"issue_id": ISSUE_PURCHASE_AUTH}
    )
    assert flow["step_id"] == "email_account"
    flow = await hass.data[REPAIRS]["flow_manager"].async_configure(
        flow["flow_id"],
        {"email": ADDRESS, "password": "still-wrong", "provider": "gmail"},
    )
    assert flow["errors"] == {"base": "invalid_auth"}
    fake.password = "new-app-password"
    flow = await hass.data[REPAIRS]["flow_manager"].async_configure(
        flow["flow_id"],
        {"email": ADDRESS, "password": "new-app-password", "provider": "gmail"},
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data["purchase_email"]["password"] == "new-app-password"
    hub = entry.runtime_data
    await hub.purchases.async_check(manual=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PURCHASE_AUTH) is None


async def test_connection_errors_are_reported(
    hass: HomeAssistant, ai: AsyncMock
) -> None:
    server = FakeImap({}, password=PASSWORD, fail_connect=True)
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass)
        assert (await hub.purchases.async_check())["error"] == "CannotConnect"
        with pytest.raises(ServiceValidationError):
            await hub.purchases.async_check(manual=True)
    server = FakeImap({}, password=PASSWORD, folder="Elsewhere")
    with patch.object(mailbox, "_create_client", server):
        hub.purchases._next_allowed = None  # noqa: SLF001
        result = await hub.purchases.async_check()
        assert result.get("error") == "FolderNotFound", result


async def test_check_needs_connection_and_ai(
    hass: HomeAssistant, fake: FakeImap
) -> None:
    hub = await setup_hub(hass, data={})
    assert (await hub.purchases.async_check())["skipped"] == "not_connected"
    with pytest.raises(ServiceValidationError):
        await hub.purchases.async_check(manual=True)
    hub = await setup_hub(hass, {"ai_task_entity": None})
    hub = hass.config_entries.async_entries(DOMAIN)[-1].runtime_data
    assert (await hub.purchases.async_check())["skipped"] == "no_ai_entity"
    with pytest.raises(ServiceValidationError):
        await hub.purchases.async_check(manual=True)


async def test_schedule_runs_and_service(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, freezer
) -> None:
    hub = await setup_hub(hass)
    freezer.tick(timedelta(seconds=61))
    from pytest_homeassistant_custom_component.common import (  # noqa: PLC0415
        async_fire_time_changed,
    )

    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hub.purchases.cards) == 4  # the first check runs a minute after start
    result = await hass.services.async_call(
        DOMAIN, "check_purchase_emails", {}, blocking=True, return_response=True
    )
    assert result["new_cards"] == 0
    assert hub.purchases.status()["new_cards"] == 4


# --- cards: match, dismiss, not clothing, archive -------------------------------------


async def test_match_dismiss_and_not_clothing(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, hass_ws_client
) -> None:
    hub = await setup_hub(hass)
    await hub.purchases.async_check(manual=True)
    cards = cards_by_name(hub)
    ws = await hass_ws_client(hass)

    # The snapshot carries signed card photos.
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    data = (await ws.receive_json())["result"]
    assert data["purchases"]["connected"] is True
    assert len(data["purchases"]["cards"]) == 4
    first = data["purchases"]["cards"][0]
    assert first["photo_urls"][0]["thumb"].startswith("/api/laundry_hub/photos/")
    assert "source_message_id" not in first and "sender_domain" not in first
    client_resp = first["photo_urls"][0]["full"]
    assert "authSig" in client_resp

    garment = hub.enroll(uid="11223344", name="Cardigan")
    jeans = cards["High-Waisted Wow Jeans"]
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/purchases/match",
            "card_id": jeans["id"],
            "garment_id": garment.id,
        }
    )
    view = (await ws.receive_json())["result"]
    assert view["purchase"]["store"] == "Old Navy"
    assert view["purchase"]["source"] == "email"
    assert view["purchase"]["purchased_on"] == "2026-11-22"
    assert view["purchase"]["order_ref"] == "ON-99812"
    assert view["brand"] == "Old Navy" and view["size"]["label"] == "8 Tall"
    assert len(garment.photos) == 1 and garment.photos[0] != jeans["photos"][0]
    assert jeans["status"] == "matched" and jeans["garment_id"] == garment.id
    # Used cards can't be matched twice.
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/purchases/match",
            "card_id": jeans["id"],
            "garment_id": garment.id,
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "purchase_used"

    # Dismissing deletes the card's photo, never the garment's copy.
    photos = Path(hass.config.path("laundry_hub/photos"))
    await hub.purchases.async_dismiss(jeans["id"])
    assert not (
        photos / jeans["photos"][0] if jeans["photos"] else photos / "x"
    ).exists()
    assert (photos / garment.photos[0]).is_file()

    # "Not clothing" is remembered for that store and product name.
    socks = cards["Wool Hiking Socks (3 pairs)"]
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/purchases/dismiss",
            "card_id": socks["id"],
            "not_clothing": True,
        }
    )
    assert (await ws.receive_json())["result"]["status"] == "dismissed"
    assert hub.purchases.meta["not_clothing"]["amazon"] == ["wool hiking socks pairs"]
    hub.purchases.cards.clear()
    hub.purchases.meta["seen_ids"] = []
    hub.purchases.meta["folders"] = {}
    await hub.purchases.async_check(manual=True)
    assert "Wool Hiking Socks (3 pairs)" not in cards_by_name(hub)

    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/dismiss", "card_id": "nope"}
    )
    assert (await ws.receive_json())["error"]["code"] == "unknown_purchase"
    await ws.send_json_auto_id({"type": "laundry_hub/purchases/check"})
    assert (await ws.receive_json())["result"]["new_cards"] == 0


async def test_photo_view_serves_card_photos(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, hass_client
) -> None:
    hub = await setup_hub(hass)
    await hub.purchases.async_check(manual=True)
    card = next(iter(hub.purchases.unmatched()))
    client = await hass_client()
    name = card["photos"][0]
    assert (await client.get(f"/api/laundry_hub/photos/{name}")).status == 200
    thumb = name.replace(".jpg", ".thumb.jpg")
    assert (await client.get(f"/api/laundry_hub/photos/{thumb}")).status == 200
    assert (
        await client.get("/api/laundry_hub/photos/purchase-unknown.jpg")
    ).status == 404


async def test_suggest_ranks_recent_then_owner_store_then_colour(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, hass_ws_client
) -> None:
    hub = await setup_hub(hass)
    await hub.purchases.async_check(manual=True)
    # Same day for two cards so the tie-breakers matter.
    cards = cards_by_name(hub)
    cards["Women's Short Sleeve T-Shirt"]["order_date"] = "2026-11-20"
    darks = next(z for z in hub.zones.values() if z.name == "Darks")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/suggest", "wash_bin": darks.id}
    )
    names = [c["name"] for c in (await ws.receive_json())["result"]["cards"]]
    assert names[0] == "High-Waisted Wow Jeans"  # most recent order (Nov 22)
    # Nov 20: navy cardigan fits "Darks" better than the white tee and grey socks
    # tie on colour family "dark"… grey counts as dark too, so check the tee is last.
    assert names[-1] == "Women's Short Sleeve T-Shirt"

    # An owner's usual store beats colour on the same day.
    owner = "person.sam"
    hass.states.async_set(owner, "home")
    past = hub.enroll(uid="55667788", name="Old tee", owner=owner)
    past.purchase = {"store": "Target"}
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/suggest", "owner": owner, "wash_bin": darks.id}
    )
    names = [c["name"] for c in (await ws.receive_json())["result"]["cards"]]
    assert names[1] == "Women's Short Sleeve T-Shirt"


async def test_auto_archive_and_disconnect_delete(
    hass: HomeAssistant, fake: FakeImap, ai: AsyncMock, images: Any, freezer
) -> None:
    hub = await setup_hub(hass, {"purchase_archive_days": 10})
    await hub.purchases.async_check(manual=True)
    freezer.tick(timedelta(days=11))
    await hub.purchases.async_check(manual=True)
    await hass.async_block_till_done()
    assert {c["status"] for c in hub.purchases.cards.values()} == {"archived"}
    assert all(c["photos"] == [] for c in hub.purchases.cards.values())
    assert hub.purchases.unmatched() == []
    assert await hub.purchases.async_delete_all() == 4
    assert hub.purchases.cards == {}


async def test_wardrobe_off_hides_purchases(
    hass: HomeAssistant, fake: FakeImap
) -> None:
    hub = await setup_hub(hass, {"wardrobe_enabled": False})
    assert hub.snapshot()["purchases"] is None
    assert not hub.purchases.active
    assert (await hub.purchases.async_check())["skipped"] == "not_connected"
