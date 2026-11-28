"""v0.2.1 options flow: Purchase emails (account → stores → options), and units."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.laundry_hub import extract, mailbox, stores
from custom_components.laundry_hub.const import DOMAIN
from custom_components.laundry_hub.extract import html_to_text, validate
from custom_components.laundry_hub.mailbox import (
    TextPart,
    decode_part,
    gmail_query,
    imap_date,
    sender_domain,
    text_parts,
)
from custom_components.laundry_hub.store import migrate_v3_1

from .fake_imap import FakeImap, load
from .test_purchases import ACCOUNT, ADDRESS, PASSWORD, ai_answer, setup_hub

ACCOUNT_INPUT = {"email": ADDRESS, "password": PASSWORD, "provider": "gmail"}


@pytest.fixture(autouse=True)
def _now(freezer) -> None:
    freezer.move_to("2026-11-27 12:00:00+00:00")


@pytest.fixture
def fake() -> Any:
    server = FakeImap(
        {
            1: load("old_navy.eml"),
            2: load("old_navy_shipped.eml"),
            3: load("target_mixed.eml"),
        },
        password=PASSWORD,
    )
    with patch.object(mailbox, "_create_client", server):
        yield server


async def open_menu(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    return await hass.config_entries.options.async_init(entry_id)


async def choose(
    hass: HomeAssistant, flow: dict[str, Any], step: str
) -> dict[str, Any]:
    return await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": step}
    )


async def test_connect_account_stores_options(
    hass: HomeAssistant, fake: FakeImap
) -> None:
    hub = await setup_hub(hass, data={}, options={"purchase_stores": []})
    entry = hub.entry
    flow = await open_menu(hass, entry.entry_id)
    assert flow["menu_options"] == ["settings", "email_account"]
    flow = await choose(hass, flow, "email_account")
    assert flow["step_id"] == "email_account"
    assert flow["description_placeholders"]["app_password_url"].startswith(
        "https://myaccount.google.com/"
    )

    # Wrong password: friendly error, nothing saved.
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**ACCOUNT_INPUT, "password": "my-normal-password"}
    )
    assert flow["errors"] == {"base": "invalid_auth"}
    assert "purchase_email" not in entry.data

    # Server down.
    fake.fail_connect = True
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], ACCOUNT_INPUT
    )
    assert flow["errors"] == {"base": "cannot_connect"}
    fake.fail_connect = False

    # Good login → store list with live counts, none preselected, matches first.
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], ACCOUNT_INPUT
    )
    assert flow["step_id"] == "email_stores"
    assert "purchase_email" not in entry.data  # still nothing saved
    schema = flow["data_schema"].schema
    field = next(k for k in schema if str(k) == "purchase_stores")
    assert field.default() == []
    options = schema[field].config["options"]
    labels = [o["label"] for o in options]
    assert labels[:2] == ["Old Navy — 2 emails", "Target — 1 email"]
    assert "Amazon — 0 emails" in labels
    assert len(options) == len(stores.CATALOG) >= 25
    # The count search is headers-free: only UID SEARCHes, no FETCH at all.
    assert not any(c[0] == "UID FETCH" for c in fake.commands)
    assert ("EXAMINE", '"[Gmail]/All Mail"') in fake.commands

    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert flow["errors"] == {"base": "no_stores"}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"purchase_stores": ["old_navy"], "purchase_custom_domains": "not a domain!"},
    )
    assert flow["errors"] == {"purchase_custom_domains": "invalid_domain"}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "purchase_stores": ["old_navy"],
            "purchase_custom_domains": "@Shop.Example.com, mystore.co.uk",
        },
    )
    assert flow["step_id"] == "email_options"
    assert "purchase_email" not in entry.data
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "purchase_interval": 20,
            "purchase_backfill": "30",
            "purchase_clothing_only": True,
            "purchase_archive_days": 45,
        },
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["purchase_email"] == ACCOUNT
    assert entry.options["purchase_stores"] == ["old_navy"]
    assert entry.options["purchase_custom_domains"] == [
        "shop.example.com",
        "mystore.co.uk",
    ]
    assert entry.options["purchase_interval"] == 20
    assert entry.options["purchase_backfill"] == 30
    assert entry.options["wardrobe_enabled"] is True  # other options kept
    assert entry.runtime_data.purchases.active


async def test_generic_imap_and_folder_errors(hass: HomeAssistant) -> None:
    server = FakeImap({}, password=PASSWORD, folder="INBOX")
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass, data={})
        flow = await choose(
            hass, await open_menu(hass, hub.entry.entry_id), "email_account"
        )
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {**ACCOUNT_INPUT, "provider": "imap"}
        )
        assert flow["step_id"] == "email_server"
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"host": "imap.example.com", "port": 993, "folder": "Orders"},
        )
        assert flow["errors"] == {"base": "folder_not_found"}
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"host": " ", "port": 993, "folder": "INBOX"}
        )
        assert flow["errors"] == {"base": "cannot_connect"}
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"host": "imap.example.com", "port": 993, "folder": "INBOX"},
        )
        assert flow["step_id"] == "email_stores"


async def test_account_needs_ai_entity(hass: HomeAssistant, fake: FakeImap) -> None:
    hub = await setup_hub(hass, data={}, options={"ai_task_entity": None})
    flow = await choose(
        hass, await open_menu(hass, hub.entry.entry_id), "email_account"
    )
    assert flow["errors"] == {"base": "no_ai_entity"}


async def test_connected_menu_change_password_edit_check_disconnect(
    hass: HomeAssistant, fake: FakeImap, aioclient_mock: Any
) -> None:
    from .test_purchases import IMAGES, JPEG  # noqa: PLC0415
    from .test_wardrobe import jpeg  # noqa: PLC0415

    for url in IMAGES:
        aioclient_mock.get(url, content=jpeg(), headers=JPEG)
    hub = await setup_hub(hass, {"purchase_stores": ["old_navy", "target"]})
    entry = hub.entry
    flow = await open_menu(hass, entry.entry_id)
    assert flow["menu_options"] == [
        "settings",
        "email_stores",
        "email_account",
        "email_options",
        "email_check",
        "email_disconnect",
    ]
    assert flow["description_placeholders"]["status"] == (
        "connected (2 stores) · not checked yet · 0 new cards"
    )

    # Check now (through the menu).
    with (
        patch.object(
            extract,
            "_generate_data",
            AsyncMock(side_effect=lambda h, **kw: ai_answer(**kw)),
        ),
        patch(
            "custom_components.laundry_hub.importer._resolve",
            AsyncMock(return_value=["93.184.216.34"]),
        ),
    ):
        result = await choose(hass, flow, "email_check")
    assert result["type"] == "abort" and result["reason"] == "check_done"
    assert result["description_placeholders"]["new"] == "2"  # jeans + tee
    hub = entry.runtime_data
    flow = await open_menu(hass, entry.entry_id)
    assert flow["description_placeholders"]["status"] == (
        "connected (2 stores) · last check 0 min ago · 2 new cards"
    )

    # Change password: tested, saved right away, stores untouched.
    flow = await choose(hass, flow, "email_account")
    fake.password = "rotated-app-password"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**ACCOUNT_INPUT, "password": "rotated-app-password"}
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["purchase_email"]["password"] == "rotated-app-password"
    assert entry.options["purchase_stores"] == ["old_navy", "target"]

    # Edit stores only.
    flow = await choose(hass, await open_menu(hass, entry.entry_id), "email_stores")
    field = next(k for k in flow["data_schema"].schema if str(k) == "purchase_stores")
    assert field.default() == ["old_navy", "target"]
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"purchase_stores": ["target"], "purchase_custom_domains": ""}
    )
    assert flow["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["purchase_stores"] == ["target"]

    # Options step alone.
    flow = await choose(hass, await open_menu(hass, entry.entry_id), "email_options")
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "purchase_interval": 60,
            "purchase_backfill": "365",
            "purchase_clothing_only": False,
            "purchase_archive_days": 30,
        },
    )
    await hass.async_block_till_done()
    assert entry.options["purchase_interval"] == 60
    assert entry.options["purchase_clothing_only"] is False
    assert "purchase_email" in entry.data

    # Main settings from the menu keep the purchase options.
    flow = await choose(hass, await open_menu(hass, entry.entry_id), "settings")
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "feedback_timeout": 2,
            "flash_duration": 15,
            "missing_after_days": 14,
            "wardrobe_enabled": True,
            "ai_task_entity": "ai_task.test",
        },
    )
    await hass.async_block_till_done()
    assert entry.options["purchase_stores"] == ["target"]

    # Disconnect, keeping the cards.
    hub = entry.runtime_data
    assert len(hub.purchases.cards) == 2
    flow = await choose(hass, await open_menu(hass, entry.entry_id), "email_disconnect")
    assert flow["description_placeholders"] == {"cards": "2"}
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"cards": "keep"}
    )
    await hass.async_block_till_done()
    assert "purchase_email" not in entry.data
    hub = entry.runtime_data
    assert len(hub.purchases.cards) == 2 and not hub.purchases.connected
    assert "folders" not in hub.purchases.meta


async def test_disconnect_can_delete_cards(hass: HomeAssistant, fake: FakeImap) -> None:
    hub = await setup_hub(hass)
    hub.purchases.cards["c1"] = {"id": "c1", "status": "new", "photos": []}
    flow = await choose(
        hass, await open_menu(hass, hub.entry.entry_id), "email_disconnect"
    )
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"cards": "delete"}
    )
    await hass.async_block_till_done()
    hub = hub.entry.runtime_data
    assert hub.purchases.cards == {}
    assert "purchase_email" not in hub.entry.data


async def test_check_now_failure_aborts(hass: HomeAssistant) -> None:
    server = FakeImap({}, password="other")
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass)
        flow = await choose(
            hass, await open_menu(hass, hub.entry.entry_id), "email_check"
        )
    assert flow["reason"] == "check_failed"


async def test_store_counts_unavailable(hass: HomeAssistant) -> None:
    server = FakeImap({}, password=PASSWORD)
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass, data={})
        flow = await choose(
            hass, await open_menu(hass, hub.entry.entry_id), "email_account"
        )
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], ACCOUNT_INPUT
        )
        server.fail_connect = True  # counting fails: names only, no error
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"purchase_stores": [], "purchase_custom_domains": "x"}
        )
    labels = [
        o["label"]
        for o in next(
            v
            for k, v in flow["data_schema"].schema.items()
            if str(k) == "purchase_stores"
        ).config["options"]
    ]
    assert "Amazon" in labels
    assert (
        flow["description_placeholders"]["count_note"]
        == "Couldn't count emails right now."
    )


# --- units ---------------------------------------------------------------------------


def test_store_catalog_and_domains() -> None:
    assert len(stores.CATALOG) >= 25
    assert len({s.key for s in stores.CATALOG}) == len(stores.CATALOG)
    assert stores.domain_matches("email.oldnavy.com", "oldnavy.com")
    assert stores.domain_matches("OLDNAVY.COM.", "oldnavy.com")
    assert not stores.domain_matches("oldnavy.com.evil.net", "oldnavy.com")
    assert not stores.domain_matches("notoldnavy.com", "oldnavy.com")
    assert stores.parse_domains("a.com, @B.org; bad, x") == (
        ["a.com", "b.org"],
        ["bad", "x"],
    )
    assert stores.normalize_domain("orders@shop.example.com") == "shop.example.com"
    opted = stores.OptedIn(("gap", "ghost"), ("shop.example.com",))
    assert opted.match("e.gapfactory.com") == ("gap", "Gap")
    assert opted.match("mail.shop.example.com") == (
        "custom:shop.example.com",
        "Example",
    )
    assert opted.match("other.com") is None and opted.match(None) is None
    assert opted.domains() == ["gap.com", "gapfactory.com", "shop.example.com"]
    assert "shipped" in opted.keywords()
    assert stores.OptedIn((), ("a.com",)).keywords() == list(stores.ORDER_KEYWORDS)


def test_gmail_query_and_dates() -> None:
    assert gmail_query(["a.com", "b.com"], 90, ["order", "on its way"]) == (
        'from:(a.com OR b.com) newer_than:90d subject:(order OR "on its way")'
    )
    assert gmail_query(["a.com"], 0, []) == "from:(a.com) newer_than:1d"
    from datetime import date  # noqa: PLC0415

    assert imap_date(date(2026, 9, 1)) == "01-Sep-2026"
    assert sender_domain('"Shop" <Orders@Mail.Shop.COM>') == "mail.shop.com"
    assert sender_domain("no address") is None


def test_bodystructure_skips_attachments_and_decodes() -> None:
    tree = mailbox._tokenize(  # noqa: SLF001
        b'((("text" "plain" ("charset" "utf-8") NIL NIL "quoted-printable" 10 1 NIL NIL NIL NIL)'
        b'("text" "html" ("charset" "iso-8859-1") NIL NIL "base64" 20 1 NIL NIL NIL NIL) "alternative")'
        b'("text" "plain" ("name" "notes.txt") NIL NIL "7bit" 5 1 NIL ("attachment" NIL) NIL NIL)'
        b'("application" "pdf" ("name" "a.pdf") NIL NIL "base64" 99 NIL ("attachment" NIL) NIL NIL) "mixed") extra)'
    )
    parts = text_parts(tree[0])
    assert [(p.section, p.subtype, p.charset, p.encoding) for p in parts] == [
        ("1.1", "plain", "utf-8", "quoted-printable"),
        ("1.2", "html", "iso-8859-1", "base64"),
    ]
    single = mailbox._tokenize(b'("TEXT" "HTML" NIL NIL NIL "7BIT" 3 1)')  # noqa: SLF001
    assert [p.section for p in text_parts(single[0])] == ["1"]
    assert text_parts([]) == [] and text_parts("x") == []
    part = TextPart("1", "html", "utf-8", "base64", 0)
    assert decode_part(b"aGk=\r\n", part) == "hi"
    qp = TextPart("1", "plain", "utf-8", "quoted-printable", 0)
    assert decode_part(b"caf=C3=A9", qp) == "café"
    assert decode_part(b"x", TextPart("1", "plain", "bogus-charset", "7bit", 0)) == "x"


def test_html_to_text_keeps_links_and_images() -> None:
    text = html_to_text(
        "<html><head><style>.x{}</style><title>T</title></head><body>"
        '<p>Order &amp; more</p><a href="https://s.com/p?a=1&amp;b=2">'
        '<img src="https://s.com/i.jpg" alt="Tee"> Tee</a>'
        '<img src="https://s.com/open.gif" width="1"><img src="cid:logo">'
        "<script>alert(1)</script></body></html>"
    )
    assert "Order & more" in text
    assert "[image: Tee: https://s.com/i.jpg]" in text
    assert "[link: https://s.com/p?a=1&b=2]" in text
    assert "open.gif" not in text and "alert" not in text and "cid:" not in text
    assert len(html_to_text("<p>" + "x " * 20000 + "</p>")) == extract.MAX_TEXT


def test_validate_drops_invented_links_and_non_apparel() -> None:
    sent = "Tee [link: https://s.com/tee] [image: https://s.com/tee.jpg]"
    raw = {
        "is_order": True,
        "order_ref": " A-1 ",
        "order_date": "November 20, 2026",
        "currency": "usd",
        "items": [
            {
                "name": "Tee",
                "product_url": "https://s.com/tee",
                "image_url": "https://s.com/fake.jpg",
                "unit_price": "$1,234.50",
                "quantity": "2",
            },
            {"name": "Lamp", "is_apparel": False},
            {"name": "", "is_apparel": True},
            "junk",
            {"name": "Sock", "unit_price": True, "product_url": "javascript:x"},
        ],
    }
    order = validate(raw, sent, clothing_only=True)
    assert order.order_ref == "A-1" and order.currency == "USD"
    assert order.order_date is not None and order.order_date.isoformat() == "2026-11-20"
    assert [i.name for i in order.items] == ["Tee", "Sock"]
    tee = order.items[0]
    assert tee.product_url == "https://s.com/tee" and tee.image_url is None
    assert tee.unit_price == 1234.5 and tee.quantity == 2
    assert order.items[1].unit_price is None and order.items[1].product_url is None
    assert order.dropped_urls == 2
    assert [i.name for i in validate(raw, sent, clothing_only=False).items] == [
        "Tee",
        "Lamp",
        "Sock",
    ]
    assert (
        validate(
            {"is_order": False, "items": [{"name": "x"}]}, "", clothing_only=True
        ).items
        == []
    )
    assert (
        validate({"order_date": "someday"}, "", clothing_only=True).order_date is None
    )
    with pytest.raises(TypeError):
        validate("nope", "", clothing_only=True)
    assert "EMAIL TEXT" in extract.instructions("Shop", "body")
    assert "items" in str(extract.order_structure().schema)
    assert "Tee" in extract.detail_instructions("Shop", "body", ["Tee"])


def test_store_migration_3_1_to_3_2_is_lossless() -> None:
    old = {"garments": {"g": {"id": "g"}}, "owners": {}, "future_key": [1]}
    new = migrate_v3_1(old)
    assert new["purchases"] == {} and new["purchase_meta"] == {}
    assert new["garments"] == old["garments"] and new["future_key"] == [1]
    assert migrate_v3_1({**new, "purchases": {"c": {}}})["purchases"] == {"c": {}}


async def test_store_keeps_unknown_top_level_keys(hass: HomeAssistant) -> None:
    from homeassistant.helpers.storage import Store  # noqa: PLC0415

    from custom_components.laundry_hub.store import Catalog  # noqa: PLC0415

    raw = Store(hass, 3, DOMAIN, minor_version=1)
    await raw.async_save({"garments": {}, "from_the_future": {"x": 1}})
    catalog = Catalog(hass)
    await catalog.async_load()
    assert catalog.purchases == {} and catalog.extra == {"from_the_future": {"x": 1}}
    await catalog.async_flush()
    saved = await Store(hass, 3, DOMAIN, minor_version=2).async_load()
    assert saved["from_the_future"] == {"x": 1} and saved["purchases"] == {}


async def test_repair_flow_generic_imap(hass: HomeAssistant) -> None:
    from homeassistant.components.repairs import DOMAIN as REPAIRS  # noqa: PLC0415
    from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
    from homeassistant.setup import async_setup_component  # noqa: PLC0415

    from custom_components.laundry_hub.const import (  # noqa: PLC0415
        ISSUE_PURCHASE_AUTH,
    )

    server = FakeImap({}, password="new", folder="INBOX")
    account = {
        **ACCOUNT,
        "provider": "imap",
        "host": "imap.example.com",
        "port": 993,
        "folder": "INBOX",
    }
    with patch.object(mailbox, "_create_client", server):
        hub = await setup_hub(hass, data={"purchase_email": account})
        hub.purchases._raise_auth_issue()  # noqa: SLF001
        assert await async_setup_component(hass, "repairs", {})
        manager = hass.data[REPAIRS]["flow_manager"]
        flow = await manager.async_init(DOMAIN, data={"issue_id": ISSUE_PURCHASE_AUTH})
        flow = await manager.async_configure(
            flow["flow_id"], {"email": ADDRESS, "password": "new", "provider": "imap"}
        )
        assert flow["step_id"] == "email_server"
        flow = await manager.async_configure(
            flow["flow_id"], {"host": "imap.example.com", "port": 993, "folder": "Nope"}
        )
        assert flow["errors"] == {"base": "folder_not_found"}
        flow = await manager.async_configure(
            flow["flow_id"],
            {"host": "imap.example.com", "port": 993, "folder": "INBOX"},
        )
        assert flow["type"] == "create_entry"
        await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data["purchase_email"]["password"] == "new"
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_PURCHASE_AUTH) is None
