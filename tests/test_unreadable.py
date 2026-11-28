"""v0.2.4: one unreadable email never blocks the purchase import (Heather's install)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.laundry_hub import ai_schema, extract, importer, mailbox

from .fake_imap import FakeImap, load
from .test_purchases import PASSWORD, setup_hub

STORES = {"purchase_stores": ["outdoor_research"]}


@pytest.fixture(autouse=True)
def _now(freezer) -> None:
    freezer.move_to("2026-11-27 12:00:00+00:00")


@pytest.fixture(autouse=True)
def _no_photos():
    """Image downloads fail quietly (cards are kept without a photo)."""
    with patch.object(importer, "_resolve", AsyncMock(side_effect=OSError("offline"))):
        yield


def answers(*, broken: str | None = None, calls: list[dict[str, Any]] | None = None):
    """Mock AI Task by email content. ``broken``: text whose emails always fail."""

    async def generate(hass: HomeAssistant, **kwargs: Any) -> SimpleNamespace:
        text: str = kwargs["instructions"]
        if calls is not None:
            calls.append(kwargs)
        if broken and broken in text:
            raise HomeAssistantError(
                "Failed to parse JSON response for orders@outdoorresearch.com"
            )
        if "on its way" in text:  # Narvar shipping notice: no item list
            data: dict[str, Any] = {
                "is_order": True,
                "store": "Outdoor Research",
                "items": [],
            }
        elif "OR-5521" in text:
            data = {
                "is_order": True,
                "order_ref": "OR-5521",
                "items": [
                    {"name": "Ferrosi Pants", "unit_price": "$99.00", "size": "M"}
                ],
            }
        else:
            data = {
                "is_order": True,
                "order_ref": "OR-5600",
                "items": [
                    {"name": "Echo T-Shirt", "unit_price": "USD 45", "quantity": None}
                ],
            }
        return SimpleNamespace(data=data, conversation_id="c")

    return generate


def mailbox_of(*names: str) -> FakeImap:
    return FakeImap(
        {i: load(n) for i, n in enumerate(names, start=1)}, password=PASSWORD
    )


async def test_shipping_notice_without_items_is_skipped_quietly(
    hass: HomeAssistant,
) -> None:
    server = mailbox_of("or_shipped_noitems.eml", "or_order.eml")
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(extract, "_generate_data", AsyncMock(side_effect=answers())),
    ):
        hub = await setup_hub(hass, STORES)
        summary = await hub.purchases.async_check(manual=True)
    assert summary["error"] is None and summary["new_cards"] == 1
    assert "pending" not in summary and "unreadable" not in summary
    assert [c["name"] for c in hub.purchases.cards.values()] == ["Ferrosi Pants"]
    assert hub.purchases.meta["last_error"] is None
    assert hub.purchases.unreadable() == []
    assert "<or-ship@narvar.com>" in hub.purchases.meta["seen_ids"]  # never re-read


async def test_failing_email_never_blocks_and_is_marked_unreadable(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    server = mailbox_of("or_shipped_noitems.eml", "or_order.eml", "or_order_2.eml")
    calls: list[dict[str, Any]] = []
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract,
            "_generate_data",
            AsyncMock(side_effect=answers(broken="on its way", calls=calls)),
        ),
    ):
        hub = await setup_hub(hass, STORES)
        inbox = hub.purchases
        folder = next(iter(inbox.meta.get("folders") or {}), None)
        assert folder is None

        # Run 1: the shipping notice fails (full structure, then the minimal
        # retry), but the two orders after it still become cards.
        first = await inbox.async_check(manual=True)
        assert first["error"] == "AIFailed" and first["new_cards"] == 2
        assert sorted(c["name"] for c in inbox.cards.values()) == [
            "Echo T-Shirt",
            "Ferrosi Pants",
        ]
        assert len([c for c in calls if "on its way" in c["instructions"]]) == 2
        minimal = calls[1]["structure"]
        assert "unit_price" not in str(ai_schema.json_schema(minimal))
        assert next(iter(inbox.meta["failures"].values()))["count"] == 1
        assert "attempt 1 of 3" in caplog.text
        # The redacted exception text is logged, never the address.
        assert "Failed to parse JSON response for <address>" in caplog.text
        assert "orders@outdoorresearch.com" not in caplog.text
        # Uncached (fresh backfill not saved), so the window will repeat it.

        # Run 2: retried (count 2); the other two are already seen (no AI call).
        calls.clear()
        second = await inbox.async_check(manual=True)
        assert second["error"] == "AIFailed" and second["new_cards"] == 0
        assert all("on its way" in c["instructions"] for c in calls)
        assert next(iter(inbox.meta["failures"].values()))["count"] == 2

        # Run 3: third failure → marked unreadable; the import moves past it.
        third = await inbox.async_check(manual=True)
        assert third["error"] is None and third["unreadable"] == 1
        assert inbox.meta.get("failures") == {}
        assert inbox.meta["last_error"] is None
        assert inbox.unreadable() == [
            {
                "store": "Outdoor Research",
                "date": "2026-11-15T15:00:00+00:00",
                "sender_domain": "outdoorresearch.narvar.com",
                "error": "HomeAssistantError",
            }
        ]
        assert "couldn't be read after 3 attempts" in caplog.text
        state = next(iter(inbox.meta["folders"].values()))
        assert state["last_uid"] == 3  # the window advanced past it

        # Run 4: not read again.
        calls.clear()
        await inbox.async_check(manual=True)
        assert calls == []
        assert inbox.status()["unreadable"][0]["store"] == "Outdoor Research"

    # Retry: the mark is cleared and the email is read again (now it works).
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(extract, "_generate_data", AsyncMock(side_effect=answers())),
    ):
        result = await inbox.async_retry_unreadable()
    assert result["retried"] == 1 and result["error"] is None
    assert inbox.unreadable() == []
    assert "<or-ship@narvar.com>" in inbox.meta["seen_ids"]


async def test_retry_unreadable_over_websocket_and_diagnostics(
    hass: HomeAssistant, hass_ws_client, hass_client
) -> None:
    from homeassistant.setup import async_setup_component  # noqa: PLC0415
    from pytest_homeassistant_custom_component.components.diagnostics import (  # noqa: PLC0415
        get_diagnostics_for_config_entry,
    )

    server = mailbox_of("or_shipped_noitems.eml")
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract,
            "_generate_data",
            AsyncMock(side_effect=answers(broken="on its way")),
        ),
    ):
        hub = await setup_hub(hass, STORES)
        for _ in range(3):
            await hub.purchases.async_check(manual=True)
    assert len(hub.purchases.unreadable()) == 1
    assert await async_setup_component(hass, "diagnostics", {})
    diag = await get_diagnostics_for_config_entry(hass, hass_client, hub.entry)
    assert diag["purchases"]["unreadable_emails"] == 1
    assert diag["purchases"]["unreadable_senders"] == ["outdoorresearch.narvar.com"]
    assert "or-ship" not in str(diag)  # no message ids
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "laundry_hub/data"})
    status = (await ws.receive_json())["result"]["purchases"]
    assert status["unreadable"][0]["store"] == "Outdoor Research"
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(extract, "_generate_data", AsyncMock(side_effect=answers())),
    ):
        await ws.send_json_auto_id({"type": "laundry_hub/purchases/retry_unreadable"})
        result = (await ws.receive_json())["result"]
    assert result["retried"] == 1 and hub.purchases.unreadable() == []


async def test_minimal_structure_retry_rescues_an_email(hass: HomeAssistant) -> None:
    server = mailbox_of("or_order.eml")
    replies = [
        HomeAssistantError("response_not_found"),
        # v0.2.5: the smaller pass-1 structure lists names as plain strings,
        SimpleNamespace(
            data={"is_order": True, "order_ref": "OR-5521", "items": ["Ferrosi Pants"]},
            conversation_id="c",
        ),
        # then pass 2 reads the item's details.
        SimpleNamespace(
            data={"items": [{"name": "Ferrosi Pants", "is_apparel": True}]},
            conversation_id="c",
        ),
    ]
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(extract, "_generate_data", AsyncMock(side_effect=replies)),
    ):
        hub = await setup_hub(hass, STORES)
        summary = await hub.purchases.async_check(manual=True)
    assert summary["error"] is None and summary["new_cards"] == 1
    assert hub.purchases.meta.get("failures") in (None, {})


def test_loose_values_still_make_a_card() -> None:
    """Price strings, missing quantity and null fields are cleaned locally."""
    raw = {
        "is_order": None,
        "order_ref": None,
        "items": [
            {
                "name": "Echo T-Shirt",
                "unit_price": "$12.99",
                "quantity": None,
                "color": None,
                "size": None,
                "brand": None,
                "product_url": None,
                "image_url": None,
                "is_apparel": None,
            },
            {"name": "Socks", "unit_price": "USD 1,234.50", "quantity": "2 pcs"},
            {"name": "Hat", "unit_price": 7, "quantity": 3},
            {"name": None, "unit_price": "$5"},
        ],
    }
    order = extract.validate(raw, "", clothing_only=True)
    assert order.is_order is True
    tee, socks, hat = order.items
    assert (tee.unit_price, tee.quantity, tee.color, tee.is_apparel) == (
        12.99,
        1,
        None,
        True,
    )
    assert (socks.unit_price, socks.quantity) == (1234.5, 2)
    assert (hat.unit_price, hat.quantity) == (7.0, 3)
    assert extract.validate({"items": []}, "", clothing_only=True).items == []
    # v0.2.5: structures require every key (empty text is fine, and providers
    # don't validate answers) and take prices as text.
    row = dict.fromkeys(
        ("name", "is_apparel", "quantity", "colour", "size", "cut", "product_url"), ""
    )
    row.update(unit_price="$12.99", is_apparel=True)
    assert extract.item_structure()({"items": [row]}) == {"items": [row]}
