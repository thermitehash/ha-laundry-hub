"""v0.2.2: partial shipments, archived + restore, uniform suppliers, Shopify by name."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.laundry_hub import extract, importer, mailbox, stores
from custom_components.laundry_hub.mailbox import gmail_shopify_query

from .fake_imap import FakeImap, load
from .test_purchases import PASSWORD, setup_hub
from .test_wardrobe import jpeg

JPEG = {"content-type": "image/jpeg"}
IMAGES = (
    "https://s7.landsend.com/is/image/turtleneck.jpg",
    "https://s7.landsend.com/is/image/chinos.jpg",
    "https://s7.landsend.com/is/image/scarf.jpg",
    "https://s7.landsend.com/is/image/mug.jpg",
    "https://www.blauer.com/img/8703.jpg",
    "https://cdn.shopify.com/s/files/ft/vertex.jpg",
)
SHOPIFY_OPTIONS = {"purchase_stores": ["first_tactical"]}


def _item(name: str, image: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"name": name, **({"image_url": image} if image else {}), **extra}


def answer(**kwargs: Any) -> SimpleNamespace:
    """Mocked AI Task: answers by email content; never sees a rejected sender."""
    text: str = kwargs["instructions"]
    assert "SECRET-" not in text, "a rejected sender's body reached the AI"
    if "Shipment 2 of 2" in text:
        data = {
            "is_order": True,
            "order_ref": "LE-7781",
            "items": [
                _item("Women's Mid Rise Chino Pants"),
                _item("Cashmere Scarf", IMAGES[2], color="Red", is_apparel=True),
                _item("Lands' End Travel Mug", IMAGES[3], is_apparel=False),
                _item("Wool Hiking Socks"),  # no apparel flag: counts as clothing
            ],
        }
    elif "Shipment 1 of 2" in text:
        data = {
            "is_order": True,
            "order_ref": "LE-7781",
            "items": [_item("Women's Cotton Rib Turtleneck")],
        }
    elif "LE-7781" in text:
        data = {
            "is_order": True,
            "order_ref": "LE-7781",
            "order_date": "2026-11-18",
            "items": [
                _item("Women's Cotton Rib Turtleneck", IMAGES[0], is_apparel=True),
                _item("Women's Mid Rise Chino Pants", IMAGES[1], is_apparel=True),
            ],
        }
    elif "B-3301" in text:
        data = {
            "is_order": True,
            "order_ref": "B-3301",
            "items": [_item("8703 Long Sleeve Shirt", IMAGES[4], size="L")],
        }
    elif "FT1001" in text:
        data = {
            "is_order": True,
            "order_ref": "FT1001",
            "items": [_item("V2 Tactical Pant", IMAGES[5], size="34x32")],
        }
    else:  # pragma: no cover - test bug
        raise AssertionError("unexpected email")
    return SimpleNamespace(data=data, conversation_id="c")


@pytest.fixture(autouse=True)
def _now(freezer) -> None:
    freezer.move_to("2026-11-27 12:00:00+00:00")


@pytest.fixture
def images(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    with patch.object(importer, "_resolve", AsyncMock(return_value=["93.184.216.34"])):
        for url in IMAGES:
            aioclient_mock.get(url, content=jpeg(), headers=JPEG)
        yield aioclient_mock


@pytest.fixture
def ai() -> Any:
    mock = AsyncMock(side_effect=lambda hass, **kw: answer(**kw))
    with patch.object(extract, "_generate_data", mock):
        yield mock


def server(*names: str, folder: str = "[Gmail]/All Mail") -> FakeImap:
    return FakeImap(
        {i: load(n) for i, n in enumerate(names, start=1)},
        password=PASSWORD,
        folder=folder,
    )


def by_name(hub: Any) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in hub.purchases.cards.values()}


# --- 1. partial shipments -----------------------------------------------------------


async def test_partial_shipment_adds_only_apparel_cards(
    hass: HomeAssistant, ai: AsyncMock, images: Any
) -> None:
    fake = server("landsend_order.eml", "landsend_ship_1.eml", "landsend_ship_2.eml")
    with patch.object(mailbox, "_create_client", fake):
        # Clothing-only off: the mug would be kept on a new order, but a
        # shipment only ever adds clothing to a known order.
        hub = await setup_hub(
            hass, {"purchase_stores": ["lands_end"], "purchase_clothing_only": False}
        )
        summary = await hub.purchases.async_check(manual=True)
    cards = by_name(hub)
    assert set(cards) == {
        "Women's Cotton Rib Turtleneck",
        "Women's Mid Rise Chino Pants",
        "Cashmere Scarf",
        "Wool Hiking Socks",
    }
    assert summary["new_cards"] == 4  # 2 from the order + 2 from shipment 2
    scarf = cards["Cashmere Scarf"]
    assert scarf["order_ref"] == "LE-7781" and len(scarf["photos"]) == 1
    assert scarf["order_date"] == "2026-11-23"  # from the shipment email
    # Existing cards were matched by name, not duplicated.
    assert sum(1 for c in cards if c.startswith("Women's")) == 2
    # The Narvar senders (landsend.narvar.com / .narvarnotify.com) were read.
    assert {uid for uid, _ in fake.body_fetches} == {1, 2, 3}


async def test_single_item_shipment_still_merges_renamed_item(
    hass: HomeAssistant, images: Any
) -> None:
    """One card + an email about one item: a slightly renamed item merges."""
    fake = server("landsend_order.eml")
    replies = [
        {"is_order": True, "order_ref": "LE-7781", "items": [_item("Turtleneck A")]},
        {"is_order": True, "order_ref": "LE-7781", "items": [_item("Rib turtle B")]},
    ]
    mock = AsyncMock(
        side_effect=[SimpleNamespace(data=r, conversation_id="c") for r in replies]
    )
    with (
        patch.object(mailbox, "_create_client", fake),
        patch.object(extract, "_generate_data", mock),
    ):
        hub = await setup_hub(hass, {"purchase_stores": ["lands_end"]})
        await hub.purchases.async_check(manual=True)
        fake.messages[2] = load("landsend_ship_1.eml")
        summary = await hub.purchases.async_check(manual=True)
    assert summary["new_cards"] == 0 and len(hub.purchases.cards) == 1


# --- 2. archived + restore ----------------------------------------------------------


async def test_archived_view_and_restore(
    hass: HomeAssistant,
    ai: AsyncMock,
    images: Any,
    hass_ws_client,
    freezer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = server("landsend_order.eml")
    # Patched for the whole test: scheduled checks fire as the clock moves.
    monkeypatch.setattr(mailbox, "_create_client", fake)
    hub = await setup_hub(
        hass, {"purchase_stores": ["lands_end"], "purchase_archive_days": 10}
    )
    await hub.purchases.async_check(manual=True)
    cards = by_name(hub)
    turtle = cards["Women's Cotton Rib Turtleneck"]
    chinos = cards["Women's Mid Rise Chino Pants"]
    photo_dir = Path(hass.config.path("laundry_hub/photos"))
    old_photo = turtle["photos"][0]

    await hub.purchases.async_dismiss(turtle["id"], not_clothing=True)
    assert not (photo_dir / old_photo).exists()
    freezer.tick(timedelta(minutes=5))
    chinos["image_url"] = None  # an older card without a stored image link
    await hub.purchases.async_dismiss(chinos["id"])

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    data = (await ws.receive_json())["result"]["purchases"]
    assert [c["name"] for c in data["archived"]] == [
        "Women's Mid Rise Chino Pants",  # newest first
        "Women's Cotton Rib Turtleneck",
    ]
    assert "image_url" not in data["archived"][0]

    # Restore re-fetches the photo through the pinned pipeline and lifts the
    # "Not clothing" rule for that product.
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/restore", "card_id": turtle["id"]}
    )
    restored = (await ws.receive_json())["result"]
    assert restored["status"] == "new" and len(restored["photos"]) == 1
    assert restored["photo_urls"][0]["thumb"].startswith("/api/laundry_hub/photos/")
    assert (photo_dir / restored["photos"][0]).is_file()
    assert hub.purchases.meta["not_clothing"]["lands_end"] == []
    # Without a stored image link, the card comes back without a photo.
    restored = await hub.purchases.async_restore(chinos["id"])
    assert restored["photos"] == [] and restored["status"] == "new"
    # Only archived or dismissed cards can be restored.
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/restore", "card_id": turtle["id"]}
    )
    assert (await ws.receive_json())["error"]["code"] == "purchase_not_archived"

    # A restored card is archived again only after the full period from its
    # restore, not from when it was first created.
    freezer.tick(timedelta(days=9))
    await hass.async_block_till_done(wait_background_tasks=True)
    await hub.purchases.async_check(manual=True)
    assert turtle["status"] == "new"
    freezer.tick(timedelta(days=2))
    await hass.async_block_till_done(wait_background_tasks=True)
    await hub.purchases.async_check(manual=True)
    assert turtle["status"] == "archived"
    assert turtle in hub.purchases.archived()


async def test_restore_photo_failure_keeps_card(
    hass: HomeAssistant, ai: AsyncMock, aioclient_mock: AiohttpClientMocker
) -> None:
    with patch.object(importer, "_resolve", AsyncMock(return_value=["10.0.0.5"])):
        fake = server("landsend_order.eml")
        with patch.object(mailbox, "_create_client", fake):
            hub = await setup_hub(hass, {"purchase_stores": ["lands_end"]})
            await hub.purchases.async_check(manual=True)
        card = next(iter(hub.purchases.cards.values()))
        assert card["photos"] == []  # private address: refused by the SSRF guard
        await hub.purchases.async_dismiss(card["id"])
        restored = await hub.purchases.async_restore(card["id"])
    assert restored["status"] == "new" and restored["photos"] == []


# --- 3. uniform suppliers ------------------------------------------------------------


async def test_uniform_preset_on_match_for_uniform_suppliers_only(
    hass: HomeAssistant, ai: AsyncMock, images: Any, hass_ws_client
) -> None:
    fake = server("blauer_order.eml", "landsend_order.eml")
    with patch.object(mailbox, "_create_client", fake):
        hub = await setup_hub(hass, {"purchase_stores": ["blauer", "lands_end"]})
        await hub.purchases.async_check(manual=True)
    cards = by_name(hub)
    shirt = cards["8703 Long Sleeve Shirt"]
    turtle = cards["Women's Cotton Rib Turtleneck"]
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    views = {
        c["name"]: c for c in (await ws.receive_json())["result"]["purchases"]["cards"]
    }
    assert views["8703 Long Sleeve Shirt"]["uniform_supplier"] is True
    assert views["Women's Cotton Rib Turtleneck"]["uniform_supplier"] is False

    a = hub.enroll(uid="11223344", name="Duty shirt")
    b = hub.enroll(uid="55667788", name="Turtleneck")
    await hub.purchases.async_match(shirt["id"], a)
    await hub.purchases.async_match(turtle["id"], b)
    assert a.uniform is True and a.wear_for == "work"
    assert b.uniform is False and b.wear_for == "any"
    # The preset is only a preset: the user can untick it.
    hub.wardrobe.update_details(a, {"uniform": False, "wear_for": "any"})
    assert a.uniform is False and a.wear_for == "any"

    # Unticked (or matched before v0.2.2): offered once for that store.
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    offers = (await ws.receive_json())["result"]["purchases"]["uniform_offers"]
    assert offers == [{"store_key": "blauer", "store": "Blauer", "count": 1}]
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/uniform", "store_key": "blauer"}
    )
    assert (await ws.receive_json())["result"] == {"updated": 1}
    assert a.uniform is True
    assert hub.purchases.uniform_offers() == []  # once per store
    a.uniform = False
    assert hub.purchases.uniform_offers() == []  # still done for Blauer

    # Non-uniform stores are never offered or marked.
    await ws.send_json_auto_id(
        {"type": "laundry_hub/purchases/uniform", "store_key": "lands_end"}
    )
    assert (await ws.receive_json())["error"]["code"] == "not_uniform_supplier"
    assert b.uniform is False


async def test_uniform_offer_can_be_declined(hass: HomeAssistant) -> None:
    hub = await setup_hub(hass, {"purchase_stores": ["first_tactical"]})
    garment = hub.enroll(uid="11223344", name="Pants")
    hub.purchases.cards["c1"] = {
        "id": "c1",
        "status": "matched",
        "store": "First Tactical",
        "store_key": "first_tactical",
        "garment_id": garment.id,
        "photos": [],
    }
    assert hub.purchases.uniform_offers()[0]["store"] == "First Tactical"
    assert hub.purchases.mark_store_uniform("first_tactical", accept=False) == 0
    assert hub.purchases.uniform_offers() == [] and garment.uniform is False
    with pytest.raises(ServiceValidationError):
        hub.purchases.mark_store_uniform("amazon")


def test_uniform_suppliers_are_blauer_and_first_tactical_only() -> None:
    uniform = {s.key for s in stores.CATALOG if s.uniform_supplier}
    assert uniform == {"blauer", "first_tactical"}
    assert stores.is_uniform_supplier("blauer")
    assert not stores.is_uniform_supplier("lands_end")
    assert not stores.is_uniform_supplier(None)
    for key in (
        "galls",
        "511",
        "propper",
        "rtut",
        "quartermaster",
        "elbeco",
        "dickies",
        "cintas",
    ):
        assert key not in stores.BY_KEY


# --- Heather's stores, Narvar ---------------------------------------------------------


HEATHER = {
    "lands_end": {"landsend.narvar.com", "landsend.narvarnotify.com", "landsend.com"},
    "american_tall": {"americantall.com"},
    "llbean": {"llbean.com"},
    "duluth": {"duluthtrading.com", "duluthtrading.narvar.com"},
    "eddie_bauer": {"eddiebauer.com"},
    "outdoor_research": {"outdoorresearch.com", "outdoorresearch.narvar.com"},
    "backcountry": {"backcountry.com", "backcountry.narvar.com"},
    "columbia": {"columbia1.narvar.com", "columbia.com"},
    "carhartt": {"carhartt.com"},
    "coldwater_creek": {"coldwatercreek.com", "coldwatercreek.narvar.com"},
    "brooks_brothers": {"brooksbrothers.com"},
    "orvis": {"email-orvis.com", "orvis.com"},
    "darn_tough": {"darntough.com"},
    "woolx": {"woolx.com"},
    "outway": {"outway.com"},
    "meundies": {"meundies.com"},
    "underoutfit": {"underoutfit.com"},
    "truekind": {"truekind.com"},
    "cinderella_shoes": {"cinderellashoes.com"},
    "blauer": {"blauer.com"},
}


def test_heathers_stores_and_narvar_rules() -> None:
    for key, domains in HEATHER.items():
        assert set(stores.BY_KEY[key].domains) == domains, key
    # The senders she listed match through subdomain matching.
    every = stores.OptedIn(tuple(HEATHER), ())
    for sender, key in (
        ("e5.llbean.com", "llbean"),
        ("mail.duluthtrading.com", "duluth"),
        ("orders.backcountry.com", "backcountry"),
        ("nat.carhartt.com", "carhartt"),
        ("info.meundies.com", "meundies"),
        ("web.blauer.com", "blauer"),
        ("landsend.narvarnotify.com", "lands_end"),
    ):
        assert every.match(sender) is not None and every.match(sender)[0] == key
    # narvar.com is never matched on its own, nor another brand's subdomain.
    everything = stores.OptedIn(tuple(stores.BY_KEY), ())
    for sender in ("narvar.com", "somebrand.narvar.com", "narvarnotify.com"):
        assert everything.match(sender) is None, sender
    for store in stores.CATALOG:
        assert not set(store.domains) & {"narvar.com", "narvarnotify.com"}, store.key
    assert stores.parse_domains("narvar.com, shopifyemail.com, ok.narvar.com") == (
        ["ok.narvar.com"],
        ["narvar.com", "shopifyemail.com"],
    )
    # Left out on purpose (see DECISIONS): food, electronics, delivery apps.
    for key in ("kroger", "doordash", "life_is_best_outside"):
        assert key not in stores.BY_KEY


async def test_narvar_senders_never_body_fetched(
    hass: HomeAssistant, ai: AsyncMock, images: Any
) -> None:
    """Generic IMAP FROM is a substring search; header checks still reject them."""
    fake = server("narvar_bare.eml", "narvar_otherbrand.eml", folder="INBOX")
    account = {
        "email": "x@example.com",
        "password": PASSWORD,
        "provider": "imap",
        "host": "imap.example.com",
        "port": 993,
        "folder": "INBOX",
    }
    with patch.object(mailbox, "_create_client", fake):
        hub = await setup_hub(
            hass,
            # Even if a shared domain got into the options, it is ignored.
            {
                "purchase_stores": ["lands_end"],
                "purchase_custom_domains": ["narvar.com"],
            },
            {"purchase_email": account},
        )
        assert hub.purchases.opted.domains() == [
            "landsend.com",
            "landsend.narvar.com",
            "landsend.narvarnotify.com",
        ]
        await hub.purchases.async_check(manual=True)
    assert fake.body_fetches == [] and ai.await_count == 0


# --- Shopify by name -------------------------------------------------------------------


async def test_shopify_by_name_gmail(
    hass: HomeAssistant, ai: AsyncMock, images: Any
) -> None:
    fake = server("firsttactical_order.eml", "shopify_other.eml")
    with patch.object(mailbox, "_create_client", fake):
        hub = await setup_hub(hass, SHOPIFY_OPTIONS)
        summary = await hub.purchases.async_check(manual=True)
    search = [c for c in fake.commands if c[0] == "UID SEARCH"]
    assert len(search) == 1  # no domain stores: only the Shopify search
    query = search[0][2].replace('\\"', '"')
    assert 'from:shopifyemail.com ("First Tactical") newer_than:90d' in query
    # The other shop came back from the search (its body mentions First
    # Tactical) but the header check rejected it before any body fetch.
    assert 2 in fake.header_fetches
    assert {uid for uid, _ in fake.body_fetches} == {1}
    assert summary["skipped_senders"] == 1
    (card,) = hub.purchases.cards.values()
    assert card["store"] == "First Tactical" and card["store_key"] == "first_tactical"
    garment = hub.enroll(uid="11223344", name="Pants")
    await hub.purchases.async_match(card["id"], garment)
    assert garment.uniform is True


async def test_shopify_user_names_generic_imap(
    hass: HomeAssistant, ai: AsyncMock, images: Any
) -> None:
    fake = server("firsttactical_order.eml", "shopify_other.eml", folder="INBOX")
    account = {
        "email": "x@example.com",
        "password": PASSWORD,
        "provider": "imap",
        "host": "imap.example.com",
        "port": 993,
        "folder": "INBOX",
    }
    ai.side_effect = lambda hass, **kw: (
        SimpleNamespace(
            data={
                "is_order": True,
                "order_ref": "2001",
                "items": [_item("Candle Tee")],
            },
            conversation_id="c",
        )
        if "Cozy Candle" in kw["instructions"]
        else answer(**kw)
    )
    with patch.object(mailbox, "_create_client", fake):
        hub = await setup_hub(
            hass,
            {"purchase_stores": [], "purchase_shopify_names": ["Cozy Candle Co"]},
            {"purchase_email": account},
        )
        await hub.purchases.async_check(manual=True)
    searches = [c for c in fake.commands if c[0] == "UID SEARCH"]
    assert searches == [
        ("UID SEARCH", "SINCE", "29-Aug-2026", "FROM", '"shopifyemail.com"')
    ]
    # First Tactical isn't opted in here: its email is rejected at the header.
    assert {uid for uid, _ in fake.body_fetches} == {2}
    (card,) = hub.purchases.cards.values()
    assert (
        card["store_key"] == "shopify:cozy candle co"
        and card["store"] == "Cozy Candle Co"
    )
    assert hub.purchases.status()["stores"] == 1


def test_shopify_name_matching_rules() -> None:
    opted = stores.OptedIn(("first_tactical",), (), ("Cozy Candle Co",))
    shop = "t.shopifyemail.com"
    assert opted.match(shop, "First Tactical", "Order #1") == (
        "first_tactical",
        "First Tactical",
    )
    assert opted.match(shop, "Shop", "Your FIRST TACTICAL order")[0] == "first_tactical"
    assert opted.match(shop, "First Tacticalware", "Order") is None  # whole words
    assert opted.match(shop, "Other shop", "Order #1") is None
    assert opted.match(shop, None, None) is None
    assert opted.match(shop, "cozy candle co", "")[0] == "shopify:cozy candle co"
    # The display name only counts on Shopify's shared sender.
    assert opted.match("evil.example.com", "First Tactical", "Order") is None
    assert opted.domains() == [] and opted.count() == 2
    assert stores.parse_names("A, First Tactical, first tactical, , " + "x" * 61) == (
        ["First Tactical"],
        ["A", "x" * 61],
    )
    assert stores.name_in("Ace & Co", "Order from ace & co today")
    assert gmail_shopify_query(["First Tactical", "Ace"], 30, ["order"]) == (
        'from:shopifyemail.com ("First Tactical" OR Ace) newer_than:30d subject:(order)'
    )


async def test_shopify_count_reads_headers_not_bodies(hass: HomeAssistant) -> None:
    from custom_components.laundry_hub.purchases import (  # noqa: PLC0415
        async_count_store_emails,
    )

    fake = server("firsttactical_order.eml", "shopify_other.eml", "blauer_order.eml")
    account = mailbox.account_from_data(
        {"email": "x@example.com", "password": PASSWORD, "provider": "gmail"}
    )
    assert account is not None
    counts = await async_count_store_emails(account, 90, fake)
    assert counts["first_tactical"] == 1  # the other shop isn't counted
    assert counts["blauer"] == 1
    assert fake.body_fetches == []


async def test_options_flow_shopify_names_and_shared_domains(
    hass: HomeAssistant, hass_client: Any
) -> None:
    fake = server("firsttactical_order.eml")
    with patch.object(mailbox, "_create_client", fake):
        hub = await setup_hub(hass, {"purchase_stores": ["blauer"]})
        entry = hub.entry
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "email_stores"}
        )
        labels = {
            o["value"]: o["label"]
            for o in next(
                v
                for k, v in flow["data_schema"].schema.items()
                if str(k) == "purchase_stores"
            ).config["options"]
        }
        assert labels["first_tactical"] == "First Tactical — 1 email"
        assert list(labels)[0] == "first_tactical"  # matches sort first
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"purchase_stores": ["blauer"], "purchase_custom_domains": "narvar.com"},
        )
        assert flow["errors"] == {"purchase_custom_domains": "invalid_domain"}
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"purchase_stores": ["blauer"], "purchase_shopify_names": "X"},
        )
        assert flow["errors"] == {"purchase_shopify_names": "invalid_shop_name"}
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "purchase_stores": [],
                "purchase_shopify_names": "Cozy Candle Co, Ace & Co",
            },
        )
        assert flow["type"] == "create_entry"
        await hass.async_block_till_done()
    assert entry.options["purchase_shopify_names"] == ["Cozy Candle Co", "Ace & Co"]
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["description_placeholders"]["status"].startswith("connected (2 stores)")

    assert await async_setup_component(hass, "diagnostics", {})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diag["purchases"]["shopify_name_count"] == 2
    assert "Cozy Candle" not in str(diag)
