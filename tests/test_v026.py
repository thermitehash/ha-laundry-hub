"""v0.2.6: purchase inbox quality, grouping and assignment."""

from __future__ import annotations

from datetime import UTC, date, datetime
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.laundry_hub import (
    ai_schema,
    extract,
    grouping,
    importer,
    mailbox,
    panel,
)
from custom_components.laundry_hub.access import Caller
from custom_components.laundry_hub.const import DOMAIN
from custom_components.laundry_hub.mailbox import Header, gmail_shopify_query
from custom_components.laundry_hub.purchases import cut_fit, cut_size_line
from custom_components.laundry_hub.stores import OptedIn, is_pharmacy, shop_id

from .conftest import OPTIONS as BASE_OPTIONS, seed_subentries
from .fake_imap import FakeImap
from .test_app_api import ALEX, SAM, Client
from .test_purchases import ACCOUNT, PASSWORD

AMALLI_ID = "6723108924"


@pytest.fixture(autouse=True)
def _now(freezer) -> None:
    freezer.move_to("2026-11-28 12:00:00+00:00")


@pytest.fixture(autouse=True)
def _no_photos():
    """Image downloads fail quietly (cards are kept without a photo)."""
    with patch.object(importer, "_resolve", AsyncMock(side_effect=OSError("offline"))):
        yield


async def setup(hass: HomeAssistant, *, connected: bool = True, **options: Any) -> Any:
    hass.states.async_set("ai_task.test", "unknown")
    hass.states.async_set(ALEX, "home", {"user_id": None})
    hass.states.async_set(SAM, "home", {"user_id": None})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Laundry Hub",
        data={"purchase_email": dict(ACCOUNT)} if connected else {},
        options={
            **BASE_OPTIONS,
            "wardrobe_enabled": True,
            "ai_task_entity": "ai_task.test",
            "purchase_stores": [
                "amazon",
                "duluth",
                "eddie_bauer",
                "american_tall",
                "outdoor_research",
                "lands_end",
                "amalli_talli",
            ],
            "purchase_custom_domains": [],
            "purchase_interval": 30,
            "purchase_backfill": 90,
            **options,
        },  # fmt: skip
        subentries_data=seed_subentries(),
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def head(subject: str, mid: str, day: int, domain: str = "shop.example") -> Header:
    return Header(
        uid=1,
        message_id=f"<{mid}>",
        sender_domain=domain,
        subject=subject,
        date=datetime(2026, 11, day, 15, tzinfo=UTC),
    )


def order(ref: str | None, day: int | None, *items: extract.Item) -> extract.Order:
    return extract.Order(
        is_order=True,
        order_ref=ref,
        order_date=date(2026, 11, day) if day else None,
        currency="USD",
        items=list(items),
    )


def item(name: str, **kw: Any) -> extract.Item:
    return extract.Item(name=name, **kw)


STORE = {
    "duluth": "Duluth Trading",
    "eddie_bauer": "Eddie Bauer",
    "american_tall": "American Tall",
    "outdoor_research": "Outdoor Research",
    "lands_end": "Lands' End",
    "amazon": "Amazon",
}

# One case per duplicate pattern seen in Heather's 45 new cards.
OR_ITEMS = ["Ferrosi Pants", "Echo T-Shirt", "Helium Rain Jacket", "Trail Mix Cap",
            "Activeice Sun Gloves"]  # fmt: skip
PATTERNS = {
    "confirmation + shipping (Duluth)": (
        "duluth",
        [
            (
                "Your order is confirmed",
                "WEB35683056",
                10,
                [item("Boot Socks", quantity=2)],
            ),
            (
                "Your order has shipped",
                "WEB35683056",
                12,
                [item("Boot Socks", quantity=1)],
            ),
            ("Your order was delivered", "WEB35683056", 14, [item("Boot Socks")]),
        ],
        {"Boot Socks": (2, 1)},
    ),
    "confirmation + shipping (Eddie Bauer)": (
        "eddie_bauer",
        [
            (
                "Thanks for your order",
                "EB4855650",
                3,
                [item("Guide Pro Pants", size="34")],
            ),
            (
                "Your order has shipped",
                "EB4855650",
                5,
                [item("Guide Pro Pants", size="34")],
            ),
        ],
        {"Guide Pro Pants": (1, 1)},
    ),
    "order_ref with # (American Tall)": (
        "american_tall",
        [
            ("Order confirmed", "2089393", 7, [item("Tall Crew Tee")]),
            ("Your order has shipped", "#2089393", 9, [item("Tall Crew Tee")]),
        ],
        {"Tall Crew Tee": (1, 1)},
    ),
    "leading-letter prefix (Outdoor Research, 5 items)": (
        "outdoor_research",
        [
            ("Order confirmation", "1751170", 1, [item(n) for n in OR_ITEMS]),
            ("Your order is on its way", "Z1751170", 4, [item(n) for n in OR_ITEMS]),
        ],
        dict.fromkeys(OR_ITEMS, (1, 1)),
    ),
    "shipping without order_ref (Lands' End)": (
        "lands_end",
        [
            (
                "Order confirmed",
                "8176491-1",
                20,
                [
                    item(
                        "Wanderweight Down Vest",
                        color="Navy",
                        size="M",
                        unit_price=99.95,
                    )
                ],
            ),
            (
                "Your order has shipped",
                None,
                22,
                [item("Wanderweight Down Vest", color="Navy", size="M")],
            ),
        ],
        {"Wanderweight Down Vest": (1, 1)},
    ),  # fmt: skip
    "the same item in two orders (RUMIA dress)": (
        "amazon",
        [
            (
                "Your Amazon.com order",
                "113-1111111",
                18,
                [item("RUMIA Dress", size="L")],
            ),
            (
                "Your Amazon.com order",
                "113-2222222",
                19,
                [item("RUMIA Dress", size="XL")],
            ),
        ],
        {"RUMIA Dress": (2, 2)},
    ),
}


@pytest.mark.parametrize("pattern", sorted(PATTERNS))
async def test_duplicate_patterns_make_one_group(
    hass: HomeAssistant, pattern: str
) -> None:
    store_key, emails, expected = PATTERNS[pattern]
    hub = await setup(hass)
    inbox = hub.purchases
    for index, (subject, ref, day, items) in enumerate(emails):
        await inbox._async_apply(  # noqa: SLF001
            order(ref, day, *items),
            (store_key, STORE[store_key]),
            head(subject, f"m{index}", day),
        )
    cards = {c["name"]: c for c in inbox.cards.values()}
    assert set(cards) == set(expected), pattern
    for name, (quantity, lines) in expected.items():
        card = cards[name]
        assert card["quantity"] == quantity, (pattern, name)
        assert len(card["history"]) == lines, (pattern, name)
    view = inbox.view(next(iter(inbox.cards.values())))
    assert "message_ids" not in str(view) and "source_message_id" not in view


async def test_later_order_adds_quantity_with_its_own_history_line(
    hass: HomeAssistant,
) -> None:
    hub = await setup(hass)
    inbox = hub.purchases
    match = ("lands_end", "Lands' End")
    vest = {"color": "Navy", "size": "M", "unit_price": 99.95}
    await inbox._async_apply(
        order("8176491", 1), match, head("Order confirmed", "a", 1)
    )  # noqa: SLF001
    await inbox._async_apply(  # noqa: SLF001
        order("8176491", 1, item("Down Vest", **vest)),
        match,
        head("Order confirmed", "b", 1),
    )
    # 20 days later, no order number: a new purchase, not the same units.
    await inbox._async_apply(  # noqa: SLF001
        order(None, 21, item("Down Vest", **vest)),
        match,
        head("Your order has shipped", "c", 21),
    )
    # A second confirmation within 14 days is also a new purchase.
    await inbox._async_apply(  # noqa: SLF001
        order(None, 23, item("Down Vest", **vest)),
        match,
        head("Order confirmed", "d", 23),
    )
    (card,) = inbox.cards.values()
    assert card["quantity"] == 3
    assert [h["order_date"] for h in card["history"]] == [
        "2026-11-01",
        "2026-11-21",
        "2026-11-23",
    ]
    assert [h["order_ref"] for h in inbox.view(card)["history"]] == [
        "8176491",
        None,
        None,
    ]


async def test_locked_quantity_split_merge_and_matching_up_to_n(
    hass: HomeAssistant,
) -> None:
    hub = await setup(hass)
    inbox = hub.purchases
    match = ("amazon", "Amazon")
    for i, ref in enumerate(["111-1", "111-2"]):
        await inbox._async_apply(  # noqa: SLF001
            order(ref, 10 + i, item("Gym Shorts", quantity=1)),
            match,
            head("Your order", f"s{i}", 10 + i),
        )
    (card,) = inbox.cards.values()
    assert card["quantity"] == 2
    # A user-set quantity is locked: later emails add history, not units.
    inbox.set_quantity(card["id"], 5)
    await inbox._async_apply(  # noqa: SLF001
        order("111-3", 15, item("Gym Shorts", quantity=2)),
        match,
        head("Your order", "s3", 15),
    )
    assert (
        card["quantity"] == 5 and card["quantity_locked"] and len(card["history"]) == 3
    )
    inbox.set_quantity(card["id"], None)  # unlock: back to the emails' count
    assert card["quantity"] == 4 and not card["quantity_locked"]

    # Split one purchase out into its own card.
    line = card["history"][0]
    twin = await inbox.async_split(card["id"], line["id"])
    assert twin["quantity"] == 1 and twin["history"] == [line]
    assert card["quantity"] == 3 and len(card["history"]) == 2
    with pytest.raises(HomeAssistantError):
        await inbox.async_split(twin["id"], line["id"])  # one purchase: can't split

    # Merge them back.
    kept = await inbox.async_merge(card["id"], twin["id"])
    assert len(inbox.cards) == 1 and kept["quantity"] == 4 and len(kept["history"]) == 3

    # A group of 4: one unit each to two garments, the rest to a third.
    g1 = hub.enroll(uid="11000001", name="Shorts A", owner=ALEX)
    g2 = hub.enroll(uid="11000002", name="Shorts B", owner=SAM)
    g3 = hub.enroll(uid="11000003", name="Shorts C", owner=SAM)
    await inbox.async_match(kept["id"], g1, 1)
    assert kept["status"] == "new" and inbox.view(kept)["remaining"] == 3
    await inbox.async_match(kept["id"], g2, 1)
    assert inbox.unmatched() == [kept]  # stays in the inbox until every unit is used
    with pytest.raises(HomeAssistantError):
        inbox.set_quantity(kept["id"], 1)  # below the 2 matched units
    await inbox.async_match(kept["id"], g3)  # the remaining 2
    assert kept["status"] == "matched"
    assert kept["matches"] == [
        {"garment_id": g1.id, "units": 1},
        {"garment_id": g2.id, "units": 1},
        {"garment_id": g3.id, "units": 2},
    ]
    assert g3.purchase["store"] == "Amazon"
    with pytest.raises(HomeAssistantError):
        await inbox.async_match(kept["id"], g1)  # nothing left

    # One garment for all units, and dismissing what's left of a partly matched group.
    await inbox._async_apply(  # noqa: SLF001
        order("222", 20, item("Wool Socks", quantity=3)),
        match,
        head("Your order", "w", 20),
    )
    socks = next(c for c in inbox.cards.values() if c["name"] == "Wool Socks")
    await inbox.async_match(socks["id"], g1, 1)
    await inbox.async_dismiss(socks["id"])
    assert socks["status"] == "matched" and socks["dismissed_units"] == 2
    assert inbox.view(socks)["remaining"] == 0


def test_normalization_rules() -> None:
    assert grouping.refs_equal("2089393", "#2089393")
    assert grouping.refs_equal("1751170", "Z1751170")
    assert grouping.refs_equal("8176491-1", "8176491")
    assert grouping.refs_equal("web 356-830-56", "WEB35683056")
    assert not grouping.refs_equal("113-1111111", "113-2222222")
    assert not grouping.refs_equal(None, "1")
    assert grouping.item_key("Ferrosi Pants - Black / M / Tall") == "ferrosi pants"
    assert grouping.item_key("Ferrosi Pants Black", color="Black") == "ferrosi pants"
    assert grouping.email_kind("Your order has shipped!") == "shipping"
    assert grouping.email_kind("Order #1863210 confirmed") == "confirmation"
    assert grouping.email_kind("Delivered: your package") == "delivery"


# --- cleanup -----------------------------------------------------------------------


def stored_card(cid: str, name: str, store_key: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": cid, "status": "new", "store": STORE.get(store_key, store_key),
        "store_key": store_key, "sender_domain": "shop.example", "order_ref": None,
        "order_date": "2026-11-20", "currency": "USD", "name": name, "brand": None,
        "color": None, "size": None, "cut": None, "quantity": 1, "unit_price": None,
        "product_url": None, "image_url": None, "photos": [], "garment_id": None,
        "source_message_id": f"<{cid}@x>", "created": "2026-11-20T10:00:00+00:00",
        "updated": "2026-11-20T10:00:00+00:00", "status_changed": "2026-11-20T10:00:00+00:00",
    }  # fmt: skip
    base.update(kw)
    return base


def classifier(not_apparel: set[str], health: set[str], calls: list[int]) -> Any:
    async def generate(hass: Any, **kwargs: Any) -> SimpleNamespace:
        names = [
            line[2:]
            for line in kwargs["instructions"].splitlines()
            if line.startswith("- ")
        ]
        calls.append(len(names))
        assert "EMAIL TEXT" not in kwargs["instructions"]
        assert ai_schema.complexity(kwargs["structure"]).over_budget() == {}
        rows = [{"name": n, "is_apparel": n not in not_apparel and n not in health,
                 "is_health": n in health, "quantity": "", "unit_price": "", "line_total": ""}
                for n in names]  # fmt: skip
        return SimpleNamespace(data={"items": rows}, conversation_id="c")

    return generate


async def test_one_time_cleanup_on_a_store_like_heathers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    hub = await setup(hass)
    inbox = hub.purchases
    photos = Path(hass.config.path("laundry_hub/photos"))
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "purchase-lamp.jpg").write_bytes(b"x")
    garment = hub.enroll(uid="22000001", name="Guide Pro Pants", owner=SAM)
    cards = [
        stored_card("lamp", "Desk Lamp", "amazon", photos=["purchase-lamp.jpg"]),
        stored_card("pills", "Allergy Relief Tablets", "amazon", status="dismissed"),
        stored_card("coins", "Coin Blanks 100 pack", "amazon"),
        # confirmation + shipping of one order (pre-v0.2.5 duplicates)
        stored_card("s1", "Boot Socks", "duluth", order_ref="WEB35683056", quantity=2),
        stored_card("s2", "Boot Socks", "duluth", order_ref="WEB35683056", quantity=2,
                    created="2026-11-22T10:00:00+00:00", status="dismissed"),
        # order_ref written differently
        stored_card("t1", "Tall Crew Tee", "american_tall", order_ref="2089393"),
        stored_card("t2", "Tall Crew Tee", "american_tall", order_ref="#2089393",
                    created="2026-11-21T10:00:00+00:00"),
        # the same dress in two orders a day apart
        stored_card("d1", "RUMIA Dress", "amazon", order_ref="113-1", order_date="2026-11-18"),
        stored_card("d2", "RUMIA Dress", "amazon", order_ref="113-2", order_date="2026-11-19"),
        # matched to a garment: never touched, even with a duplicate waiting
        stored_card("m1", "Guide Pro Pants", "eddie_bauer", order_ref="EB4855650",
                    status="matched", garment_id=garment.id),
        stored_card("m2", "Guide Pro Pants", "eddie_bauer", order_ref="EB4855650"),
        # a name suffix to split
        stored_card("f1", "Ferrosi Pants - Black / M / Regular", "outdoor_research"),
    ]  # fmt: skip
    matched_before = dict(cards[9])
    for card in cards:
        inbox.cards[card["id"]] = card
    calls: list[int] = []
    gen = classifier(
        {"Desk Lamp", "Coin Blanks 100 pack"}, {"Allergy Relief Tablets"}, calls
    )
    with patch.object(extract, "_generate_data", AsyncMock(side_effect=gen)):
        result = await inbox.async_cleanup_once()
        assert await inbox.async_cleanup_once() is None  # runs once
    assert result is not None
    assert (result["removed"], result["grouped"], result["split"]) == (3, 3, 1)
    assert calls and all(n <= extract.BATCH_SIZE for n in calls)
    assert sum(calls) == 11  # every new and dismissed card, never the matched one
    assert {"lamp", "pills", "coins"}.isdisjoint(inbox.cards)
    assert not (photos / "purchase-lamp.jpg").exists()
    socks = inbox.cards["s1"]
    assert (
        "s2" not in inbox.cards
        and socks["quantity"] == 2
        and len(socks["history"]) == 1
    )
    assert socks["status"] == "new" and socks["dismissed_units"] == 0
    assert "t2" not in inbox.cards and inbox.cards["t1"]["quantity"] == 1
    dress = inbox.cards["d1"]
    assert (
        "d2" not in inbox.cards
        and dress["quantity"] == 2
        and len(dress["history"]) == 2
    )
    assert inbox.cards["m1"] == matched_before  # untouched
    assert "m2" in inbox.cards
    ferrosi = inbox.cards["f1"]
    assert (ferrosi["name"], ferrosi["color"], ferrosi["size"], ferrosi["cut"]) == (
        "Ferrosi Pants", "Black", "M", "Regular"
    )  # fmt: skip
    # Shown once in the Settings row, then hidden.
    assert inbox.status()["cleanup"] == {"removed": 3, "grouped": 3}
    inbox.cleanup_seen()
    assert inbox.status()["cleanup"] is None
    # Logged as counts only, never product names.
    assert "3 non-clothing removed, 3 duplicates grouped" in caplog.text
    for name in ("Desk Lamp", "Allergy", "Coin Blanks", "RUMIA"):
        assert name not in caplog.text


async def test_cleanup_keeps_cards_when_the_ai_fails(hass: HomeAssistant) -> None:
    hub = await setup(hass)
    inbox = hub.purchases
    inbox.cards["lamp"] = stored_card("lamp", "Desk Lamp", "amazon")
    with patch.object(
        extract, "_generate_data", AsyncMock(side_effect=HomeAssistantError("down"))
    ):
        result = await inbox.async_cleanup_once()
    assert result is not None and result["removed"] == 0 and not result["classified"]
    assert "lamp" in inbox.cards  # deletion fails closed


# --- never store non-apparel -------------------------------------------------------


def eml(sender: str, subject: str, mid: str, body: str = "order") -> bytes:
    return (
        f"From: {sender}\r\nTo: shopper@example.com\r\nSubject: {subject}\r\n"
        "Date: Thu, 26 Nov 2026 15:00:00 +0000\r\n"
        f"Message-ID: <{mid}>\r\nMIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        f"<p>{body}</p>\r\n"
    ).encode()


def answering(
    by_body: dict[str, dict[str, Any]], calls: list[str] | None = None
) -> Any:
    """Mock AI Task: answers by a marker in the email body."""

    async def generate(hass: Any, **kwargs: Any) -> SimpleNamespace:
        text = kwargs["instructions"]
        key = next(k for k in by_body if k in text)
        if calls is not None:
            calls.append(key)
        data = by_body[key]
        if "Products:" in text:  # step 2: details for the names asked
            asked = [ln[2:] for ln in text.splitlines() if ln.startswith("- ")]
            rows = {r["name"]: r for r in data.get("details", [])}
            return SimpleNamespace(
                data={"items": [rows.get(n, {"name": n}) for n in asked]},
                conversation_id="c",
            )
        return SimpleNamespace(data=data, conversation_id="c")

    return generate


MIXED = {
    "is_order": True,
    "order_ref": "114-555",
    "items": [
        {"name": n} for n in ("Coin Blanks", "TV Remote", "Watch Bands", "Gym Shorts")
    ],
    "details": [
        {"name": "Coin Blanks", "is_apparel": False},
        {"name": "TV Remote", "is_apparel": False},
        {"name": "Watch Bands", "is_apparel": False},
        {
            "name": "Gym Shorts",
            "is_apparel": True,
            "brand": "Under Armour",
            "cut": "Relaxed",
            "line_total": "$50.00",
            "quantity": "2",
        },
    ],  # fmt: skip
}


async def test_mixed_order_pharmacy_and_marketing_store_nothing_extra(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = FakeImap(
        {
            1: eml("Amazon.com <auto-confirm@amazon.com>", "Your Amazon.com order", "a1", "MIXED"),
            2: eml("Amazon Pharmacy <pharmacy@amazon.com>", "Your order has shipped", "p1", "RX"),
            3: eml("Amazon <ship@pharmacy.amazon.com>", "Your order shipped", "p2", "RX"),
            4: eml("Amazon.com <auto-confirm@amazon.com>", "Your Amazon.com order", "h1", "HEALTH"),
            5: eml("Amalli Talli <info@amallitalli.com>", "Deals up to 40% off: your order of style", "mk", "PROMO"),
        },
        password=PASSWORD,
    )  # fmt: skip
    answers = {
        "MIXED": MIXED,
        "HEALTH": {
            "is_order": True,
            "order_ref": "114-777",
            "items": [{"name": "Allergy Relief Tablets"}, {"name": "Rain Hat"}],
            "details": [
                {
                    "name": "Allergy Relief Tablets",
                    "is_health": True,
                    "is_apparel": False,
                },
                {"name": "Rain Hat", "is_apparel": True},
            ],
        },
        "PROMO": {"is_order": False, "items": []},
        "RX": {"is_order": True, "items": [{"name": "Prescription"}]},
    }
    calls: list[str] = []
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract, "_generate_data", AsyncMock(side_effect=answering(answers, calls))
        ),
    ):
        hub = await setup(hass)
        summary = await hub.purchases.async_check(manual=True)
    names = sorted(c["name"] for c in hub.purchases.cards.values())
    assert names == ["Gym Shorts", "Rain Hat"]
    shorts = next(c for c in hub.purchases.cards.values() if c["name"] == "Gym Shorts")
    assert (shorts["brand"], shorts["cut"], shorts["unit_price"], shorts["quantity"]) == (
        "Under Armour", "Relaxed", 25.0, 2
    )  # fmt: skip
    # Pharmacy mail: rejected at the header check, never body-fetched or read.
    assert {uid for uid, _ in server.body_fetches} == {1, 4, 5}
    assert "RX" not in calls and summary["skipped_senders"] == 2
    # Nothing stored or logged by name for non-apparel or health items.
    stored = str(hub.catalog._data())  # noqa: SLF001
    for secret in (
        "Allergy",
        "Prescription",
        "Coin Blanks",
        "TV Remote",
        "Watch Bands",
    ):
        assert secret not in stored and secret not in caplog.text
    # The marketing email came back is_order=false: read, no card, marked seen.
    assert "<mk>" in hub.purchases.meta["seen_ids"]


def test_pharmacy_and_shop_id_helpers() -> None:
    assert is_pharmacy("pharmacy", "amazon.com")
    assert is_pharmacy("pharmacy-noreply", "amazon.com")
    assert is_pharmacy("ship", "orders.pharmacy.amazon.com")
    assert is_pharmacy("x", "amazon.com", "Amazon Pharmacy")
    assert not is_pharmacy("auto-confirm", "amazon.com", "Amazon.com")
    assert shop_id("store+6723108924") == AMALLI_ID
    assert shop_id("info") is None


# --- better fields -----------------------------------------------------------------


def test_brand_cut_suffix_and_line_total_are_extracted() -> None:
    raw = {
        "is_order": True,
        "items": [
            {"name": "Ferrosi Pants - Black / 34 / Tall", "brand": "OR", "line_total": "$198.00",
             "quantity": "2"},
            {"name": "Echo Tee", "unit_price": "$45", "line_total": "$90", "quantity": "2",
             "colour": "Blue"},
        ],
    }  # fmt: skip
    pants, tee = extract.validate(raw, "", clothing_only=True).items
    assert (pants.name, pants.color, pants.size, pants.cut, pants.brand) == (
        "Ferrosi Pants", "Black", "34", "Tall", "OR"
    )  # fmt: skip
    assert pants.unit_price == 99.0 and tee.unit_price == 45.0 and tee.color == "Blue"
    assert cut_fit("Slim Fit") == "slim" and cut_size_line("Slim Fit") is None
    assert cut_fit("Relaxed") == "relaxed"
    assert cut_fit("Tall") is None and cut_size_line("Tall") == "tall"
    assert cut_size_line("Petite") == "petite" and cut_fit("Classic") is None


async def test_match_copies_brand_fit_and_size_cut(hass: HomeAssistant) -> None:
    hub = await setup(hass)
    inbox = hub.purchases
    inbox.cards["c1"] = stored_card(
        "c1", "Chinos", "lands_end", brand="Lands' End", size="32", cut="Slim"
    )
    inbox.cards["c2"] = stored_card(
        "c2", "Tee", "american_tall", brand="AT", size="L", cut="Tall"
    )
    inbox.cards["c3"] = stored_card(
        "c3", "Jeans", "amazon", brand="Levi's", size="30", cut="Slim"
    )
    slim = hub.enroll(uid="33000001", name="Chinos", owner=SAM)
    tall = hub.enroll(uid="33000002", name="Tee", owner=SAM)
    mine = hub.enroll(uid="33000003", name="Jeans", owner=SAM)
    mine.brand = "My label"
    mine.description_provenance["brand"] = "user"
    await inbox.async_match("c1", slim)
    await inbox.async_match("c2", tall)
    await inbox.async_match("c3", mine)
    assert slim.brand == "Lands' End" and slim.description["fit"] == "slim"
    assert slim.size["cut"] == "n/a"
    assert tall.size == {
        "label": "L",
        "system": "other",
        "cut": "tall",
        "brand_size_note": None,
    }
    assert (tall.description or {}).get("fit") in (None, "n/a")
    assert mine.brand == "My label"  # never overwrite a user-set brand
    view = inbox.view(inbox.cards["c1"])
    assert view["cut"] == "Slim" and view["fit"] == "slim"


# --- Amalli Talli and Shopify --------------------------------------------------------


async def test_amalli_talli_by_name_and_shop_id(hass: HomeAssistant) -> None:
    server = FakeImap(
        {
            1: eml(f"Amalli Talli <store+{AMALLI_ID}@t.shopifyemail.com>", "Order #1863210 confirmed", "o1", "AT1"),
            2: eml(f"Shop <store+{AMALLI_ID}@t.shopifyemail.com>", "Order #1863211 confirmed", "o2", "AT2"),
            3: eml("Other Shop <store+11112222@t.shopifyemail.com>", "Order #5 confirmed", "x1", "NOPE"),
            4: eml("Amalli Talli <store+99998888@t.shopifyemail.com>", "Order #6 confirmed", "x2", "NOPE"),
        },
        password=PASSWORD,
    )  # fmt: skip
    answers = {
        "AT1": {"is_order": True, "order_ref": "1863210", "items": [{"name": "Tall Wide Leg Pant"}],
                "details": [{"name": "Tall Wide Leg Pant", "is_apparel": True, "size": "L"}]},
        "AT2": {"is_order": True, "order_ref": "1863211", "items": [{"name": "Tall Tee"}],
                "details": [{"name": "Tall Tee", "is_apparel": True}]},
    }  # fmt: skip
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract, "_generate_data", AsyncMock(side_effect=answering(answers))
        ),
    ):
        hub = await setup(hass, purchase_stores=["amalli_talli"])
        await hub.purchases.async_check(manual=True)
    assert sorted(c["name"] for c in hub.purchases.cards.values()) == [
        "Tall Tee",
        "Tall Wide Leg Pant",
    ]
    assert {c["store"] for c in hub.purchases.cards.values()} == {"Amalli Talli"}
    assert {uid for uid, _ in server.body_fetches} == {
        1,
        2,
    }  # other shops: headers only
    query = next(
        c for c in server.commands if c[0] == "UID SEARCH" and "shopifyemail" in str(c)
    )
    assert f"from:store+{AMALLI_ID}@" in str(query)
    assert "amallitalli.com" in str(server.commands)  # its own domain is searched too


async def test_user_added_shopify_name_learns_the_shop_id(hass: HomeAssistant) -> None:
    server = FakeImap(
        {
            1: eml(
                "Ace Outfitters <store+55556666@t.shopifyemail.com>",
                "Order confirmed",
                "a",
                "ACE",
            )
        },
        password=PASSWORD,
    )
    answers = {"ACE": {"is_order": True, "items": [{"name": "Field Jacket"}],
                       "details": [{"name": "Field Jacket", "is_apparel": True}]}}  # fmt: skip
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract, "_generate_data", AsyncMock(side_effect=answering(answers))
        ),
    ):
        hub = await setup(
            hass, purchase_stores=[], purchase_shopify_names=["Ace Outfitters"]
        )
        await hub.purchases.async_check(manual=True)
    assert hub.purchases.meta["shop_ids"] == {"shopify:ace outfitters": "55556666"}
    opted = hub.purchases.opted
    # Later mail from that shop needn't name it; another shop with the name is refused.
    assert opted.match("t.shopifyemail.com", "Shop", "Order #9", "store+55556666")
    assert (
        opted.match("t.shopifyemail.com", "Ace Outfitters", "Order", "store+77778888")
        is None
    )
    assert "from:store+55556666@" in gmail_shopify_query(
        ["Ace Outfitters"], 7, [], ["55556666"]
    )


def test_shopify_matching_rules() -> None:
    opted = OptedIn(("amalli_talli",), ())
    assert opted.match("t.shopifyemail.com", "Amalli Talli", "Order #1 confirmed")
    assert opted.match("t.shopifyemail.com", "", "Order #1", f"store+{AMALLI_ID}")
    assert (
        opted.match("t.shopifyemail.com", "Other", "Order #1", "store+12345678") is None
    )
    assert opted.match("amallitalli.com", "Amalli Talli", "Deals") == (
        "amalli_talli",
        "Amalli Talli",
    )
    assert (
        "amallitalli.com" in opted.domains()
        and "shopifyemail.com" not in opted.domains()
    )


# --- assignment ----------------------------------------------------------------------


@pytest.fixture
async def kid(hass: HomeAssistant) -> Any:
    user = await hass.auth.async_create_user("Alex Kid", group_ids=["system-users"])
    from homeassistant.auth.const import GROUP_ID_USER  # noqa: PLC0415, F401

    refresh = await hass.auth.async_create_refresh_token(
        user, "https://example.com/app"
    )
    return user, hass.auth.async_create_access_token(refresh)


async def test_assignment_visibility_actions_and_suggestions(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, kid
) -> None:
    hub = await setup(hass, app_access="household")
    hass.states.async_set(ALEX, "home", {"user_id": kid[0].id})
    inbox = hub.purchases
    for card in (
        stored_card("alex1", "Rain Jacket", "lands_end", owner=ALEX),
        stored_card("alex2", "Fleece", "lands_end", owner=ALEX),
        stored_card("sam1", "Work Shirt", "lands_end", owner=SAM),
        stored_card("open", "Wool Socks", "lands_end"),
        stored_card("k1", "Kids Fleece Jacket", "lands_end"),
        stored_card("k2", "Kids Rain Pants", "lands_end"),
        stored_card("k3", "Kids Snow Boots", "lands_end"),
    ):
        inbox.cards[card["id"]] = card
    hood = hub.enroll(uid="44000001", name="Alex hoodie", owner=ALEX)
    sams = hub.enroll(uid="44000002", name="Sam shirt", owner=SAM)
    admin = Client(await hass_ws_client(hass))
    member = Client(await hass_ws_client(hass, kid[1]))

    info = (await admin("api_info"))["result"]
    assert {"purchase_assign", "purchase_groups"} <= set(info["capabilities"])

    # Bulk assign, then suggestions after two similar cards went to one owner.
    res = await admin("purchases/assign", card_ids=["k1", "k2"], owner=SAM)
    assert [c["owner"] for c in res["result"]["cards"]] == [SAM, SAM]
    data = (await admin("data"))["result"]["purchases"]
    k3 = next(c for c in data["cards"] if c["id"] == "k3")
    assert k3["suggested_owner"] == SAM and k3["owner"] is None  # never auto-assigned
    res = await admin("purchases/assign", card_id="k2", owner=None)
    assert res["result"]["cards"][0]["owner"] is None
    data = (await admin("data"))["result"]["purchases"]
    assert next(c for c in data["cards"] if c["id"] == "k3")["suggested_owner"] is None
    assert (await admin("purchases/assign", card_id="k2", owner="person.nobody"))[
        "error"
    ]["code"] == "invalid_owner"

    # A member sees only cards assigned to their owner.
    data = (await member("data"))["result"]["purchases"]
    assert sorted(c["id"] for c in data["cards"]) == ["alex1", "alex2"]
    changes = (await member("changes", since_rev=0))["result"]
    assert sorted(c["id"] for c in changes["purchases"]) == ["alex1", "alex2"]
    hidden = {t["id"] for t in changes["deleted"] if t["kind"] == "purchase"}
    assert {"sam1", "open"} <= hidden

    # Member actions on their own cards; refused on others' and on admin commands.
    ok = await member("purchases/match", card_id="alex1", garment_id=hood.id)
    assert ok["success"], ok
    assert (await member("purchases/match", card_id="alex2", garment_id=sams.id))[
        "error"
    ]["code"] == "unauthorized"
    assert (await member("purchases/match", card_id="open", garment_id=hood.id))[
        "error"
    ]["code"] == "unauthorized"
    assert (await member("purchases/dismiss", card_id="sam1"))["error"][
        "code"
    ] == "unauthorized"
    assert (await member("purchases/dismiss", card_id="alex2", not_clothing=True))[
        "success"
    ]
    for command, payload in (
        ("purchases/assign", {"card_id": "alex2", "owner": ALEX}),
        ("purchases/set_quantity", {"card_id": "alex2", "quantity": 2}),
        ("purchases/merge", {"card_id": "alex1", "other_id": "alex2"}),
        ("purchases/check", {}),
    ):
        assert (await member(command, **payload))["error"]["code"] == "unauthorized", (
            command
        )
    # Matched cards stay visible to the member; photo access follows the same rule.
    data = (await member("data"))["result"]["purchases"]
    assert [c["id"] for c in data["mine"]] == ["alex1"]
    inbox.cards["sam1"]["photos"] = ["purchase-sam1-a.jpg"]
    inbox.cards["alex2"]["photos"] = ["purchase-alex2-a.jpg"]
    caller = Caller(kid[0].id, False, ALEX, True)
    assert panel._may_see_photo(hub, caller, "purchase-alex2-a.jpg")  # noqa: SLF001
    assert not panel._may_see_photo(hub, caller, "purchase-sam1-a.jpg")  # noqa: SLF001


async def test_classify_uses_a_budgeted_structure_in_batches(
    hass: HomeAssistant,
) -> None:
    calls: list[int] = []
    gen = classifier({"Lamp"}, set(), calls)
    with patch.object(extract, "_generate_data", AsyncMock(side_effect=gen)):
        rows = await extract.classify(
            None,
            entity_id="ai_task.test",
            names=[f"Tee {i}" for i in range(12)] + ["Lamp"],
        )
    assert calls == [10, 3]
    assert [r["is_apparel"] for r in rows][-2:] == [True, False]
