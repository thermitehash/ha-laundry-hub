"""v0.2.5: every AI Task structure stays within the budget, with fallbacks.

Heather's log showed Anthropic answering "400 … Schema is too complex" for the
old nested extraction structure, so every email fell back to a structure
without is_apparel, colour, size or price.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import probatio
import pytest

from custom_components.laundry_hub import (
    ai_schema,
    describe,
    extract,
    importer,
    mailbox,
)
from custom_components.laundry_hub.const import AI_STRUCTURE_BUDGET

from .fake_imap import FakeImap, load
from .test_purchases import PASSWORD, setup_hub

STORES = {"purchase_stores": ["outdoor_research"]}
EMAIL = "Order OR-5521 [link: https://or.example/p/1] Ferrosi Pants $99.00 size M"


def all_structures() -> dict[str, probatio.Schema]:
    """Every structure the integration can send to ai_task.generate_data."""
    out = {f"extract.{name}": build() for name, build in extract.STRUCTURES.items()}
    out.update({f"describe.{g}": describe.structure(g) for g in describe.GROUPS})
    return out


def name_of(structure: probatio.Schema) -> str:
    for name, candidate in all_structures().items():
        if ai_schema.json_schema(candidate) == ai_schema.json_schema(structure):
            return name
    raise AssertionError(f"unlisted structure sent: {structure.schema}")


def too_complex() -> HomeAssistantError:
    """What Home Assistant's Anthropic integration raises for the 400."""

    class BadRequestError(Exception):  # stands in for anthropic.BadRequestError
        status_code = 400
        message = (
            "Error code: 400 - {'type': 'error', 'error': {'type': "
            "'invalid_request_error', 'message': 'Schema is too complex for "
            "compilation.'}}"
        )

    cause = BadRequestError(BadRequestError.message)
    err = HomeAssistantError(
        translation_domain="anthropic",
        translation_key="api_error",
        translation_placeholders={"message": cause.message},
    )
    err.__cause__ = cause
    return err


@pytest.mark.parametrize("name", sorted(all_structures()))
def test_every_structure_is_within_budget(name: str) -> None:
    measured = ai_schema.complexity(all_structures()[name])
    assert measured.over_budget() == {}, f"{name}: {measured}"
    assert measured.properties > 0


def test_budget_lives_in_one_place_and_catches_the_old_shapes() -> None:
    assert set(AI_STRUCTURE_BUDGET) == {
        "properties",
        "optional",
        "unions",
        "depth",
        "enum_values",
    }
    # The v0.2.4 extraction structure (7 optional fields plus 9 optional
    # fields per item) is over budget, so the check would have caught it.
    from homeassistant.helpers import selector  # noqa: PLC0415

    text = {"selector": {"text": {}}}
    old = probatio.Schema(
        {
            probatio.Optional(k): selector.selector({"text": {}})
            for k in (
                "is_order",
                "store",
                "order_ref",
                "order_date",
                "currency",
                "total",
            )
        }
        | {
            probatio.Optional("items"): selector.selector(
                {
                    "object": {
                        "fields": dict.fromkeys("abcdefghi", text),
                        "multiple": True,
                    }
                }
            )
        }
    )
    over = ai_schema.complexity(old).over_budget()
    assert over["optional"] == (16, 0) and over["properties"] == (16, 12)
    # Describe: every field is asked once, across the groups, and only there.
    asked = [f for fields in describe.GROUPS.values() for f in fields]
    assert len(asked) == len(set(asked))
    # color_hex is folded into colors by normalize().
    assert set(asked) - {"color_hex"} == set(describe.tx.DESCRIPTION_FIELDS)


def test_schema_errors_are_recognised() -> None:
    assert ai_schema.is_schema_error(too_complex())
    plain = Exception("Error code: 400 - invalid_request_error: output schema invalid")
    assert ai_schema.is_schema_error(plain)
    assert not ai_schema.is_schema_error(HomeAssistantError("json_parse_error"))
    assert not ai_schema.is_schema_error(TimeoutError())
    assert not ai_schema.is_schema_error(None)


def replying(
    rejected: set[str], calls: list[str], *, names: list[str] | None = None
) -> Any:
    """Mock AI Task: rejects the named structures as too complex."""
    products = names or ["Ferrosi Pants"]

    async def generate(hass: Any, **kwargs: Any) -> SimpleNamespace:
        name = name_of(kwargs["structure"])
        calls.append(name)
        if name in rejected:
            raise too_complex()
        if name == "extract.order":
            data: dict[str, Any] = {
                "is_order": True,
                "store": "",
                "order_ref": "OR-5521",
                "order_date": "",
                "currency": "USD",
                "items": [{"name": n, "image_url": ""} for n in products],
            }
        elif name == "extract.order_lines":
            data = {"is_order": True, "order_ref": "OR-5521", "items": products}
        else:
            asked = [line[2:] for line in kwargs["instructions"].splitlines()
                     if line.startswith("- ")]  # fmt: skip
            assert len(asked) <= extract.BATCH_SIZE
            row = {
                "is_apparel": True,
                "quantity": "1",
                "unit_price": "$99.00",
                "colour": "Black",
                "size": "M",
                "cut": "Regular",
                "product_url": "https://or.example/p/1",
            }
            data = {"items": [{"name": n, **row} for n in asked]}
        return SimpleNamespace(data=data, conversation_id="c")

    return generate


@pytest.mark.parametrize(
    ("rejected", "expected", "complete"),
    [
        (set(), ["extract.order", "extract.item"], True),
        (
            {"extract.order", "extract.item"},
            [
                "extract.order",
                "extract.order_lines",
                "extract.item",
                "extract.item_apparel",
                "extract.item_look",
            ],
            True,
        ),
        (
            {"extract.item", "extract.item_look"},
            [
                "extract.order",
                "extract.item",
                "extract.item_apparel",
                "extract.item_look",
            ],
            False,  # no colour, size, cut or link, but is_apparel and price
        ),
    ],
)
async def test_fallback_order_keeps_is_apparel(
    rejected: set[str], expected: list[str], complete: bool
) -> None:
    calls: list[str] = []
    hook: list[tuple[str, int]] = []
    with patch.object(
        extract, "_generate_data", AsyncMock(side_effect=replying(rejected, calls))
    ):
        order = await extract.extract(
            None,
            entity_id="ai_task.test",
            store_name="Outdoor Research",
            text=EMAIL,
            clothing_only=True,
            on_schema_error=lambda name, fields: hook.append((name, fields)),
        )
    assert calls == expected
    assert [name for name, _ in hook] == [
        c.split(".")[1] for c in calls if c in rejected
    ]
    assert all(fields > 0 for _, fields in hook)
    # Every pass-2 structure sent with clothing-only on asks for is_apparel,
    # except the colour/size one, which only runs after is_apparel was read.
    for name in calls:
        if name.startswith("extract.item") and name != "extract.item_look":
            items = ai_schema.json_schema(all_structures()[name])["properties"]["items"]
            assert "is_apparel" in items["items"]["required"]
    (item,) = order.items
    assert item.is_apparel is True and item.unit_price == 99.0
    assert item.product_url == ("https://or.example/p/1" if complete else None)
    assert (item.color, item.size, item.cut) == (
        ("Black", "M", "Regular") if complete else (None, None, None)
    )


async def test_without_is_apparel_clothing_only_fails_the_email() -> None:
    calls: list[str] = []
    rejected = {"extract.item", "extract.item_apparel"}
    with (
        patch.object(
            extract, "_generate_data", AsyncMock(side_effect=replying(rejected, calls))
        ),
        pytest.raises(HomeAssistantError),
    ):
        await extract.extract(
            None,
            entity_id="ai_task.test",
            store_name="Outdoor Research",
            text=EMAIL,
            clothing_only=True,
        )
    # Never fell back to the colour/size structure (no is_apparel there).
    assert calls == ["extract.order", "extract.item", "extract.item_apparel"]

    # With clothing-only off, the item is kept with what could be read.
    calls.clear()
    with patch.object(
        extract, "_generate_data", AsyncMock(side_effect=replying(rejected, calls))
    ):
        order = await extract.extract(
            None,
            entity_id="ai_task.test",
            store_name="Outdoor Research",
            text=EMAIL,
            clothing_only=False,
        )
    assert calls[-1] == "extract.item_look"
    assert [(i.name, i.size) for i in order.items] == [("Ferrosi Pants", "M")]


async def test_items_are_asked_in_batches_of_ten() -> None:
    calls: list[str] = []
    names = [f"Tee {n}" for n in range(23)]
    with patch.object(
        extract,
        "_generate_data",
        AsyncMock(side_effect=replying(set(), calls, names=names)),
    ):
        order = await extract.extract(
            None,
            entity_id="ai_task.test",
            store_name="Outdoor Research",
            text=EMAIL,
            clothing_only=True,
        )
    assert calls == ["extract.order"] + ["extract.item"] * 3
    assert [i.name for i in order.items] == names
    assert all(i.size == "M" for i in order.items)


async def test_describe_groups_merge_and_unsure_drops_a_value() -> None:
    sent: list[str] = []

    async def generate(hass: Any, **kwargs: Any) -> SimpleNamespace:
        name = name_of(kwargs["structure"])
        sent.append(name)
        assert kwargs["attachments"]
        if name == "describe.identity":
            data = {"type": "top", "subtype": "t-shirt", "colors": ["navy"],
                    "summary": "A tee.", "unsure": ["subtype"]}  # fmt: skip
        else:
            data = {"fit": "slim", "seasons": ["summer"], "unsure": []}
        return SimpleNamespace(data=data, conversation_id="c")

    with patch.object(describe, "_generate_data", AsyncMock(side_effect=generate)):
        fields, confidence = await describe.generate(
            None, entity_id="ai_task.test", garment_id="g", photos=["a.jpg"], context={}
        )
    assert sent == ["describe.identity", "describe.details"]
    assert fields["type"] == "top" and fields["fit"] == "slim"
    assert fields["subtype"] is None and confidence["subtype"] == 0.0
    assert fields["summary"] == "A tee." and fields["seasons"] == ["summer"]


@pytest.fixture
def _no_photos():
    with patch.object(importer, "_resolve", AsyncMock(side_effect=OSError("offline"))):
        yield


@pytest.mark.usefixtures("_no_photos")
async def test_schema_rejection_is_logged_once_per_run(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, freezer
) -> None:
    freezer.move_to("2026-11-27 12:00:00+00:00")
    caplog.set_level(logging.WARNING)
    server = FakeImap(
        {1: load("or_order.eml"), 2: load("or_order_2.eml")}, password=PASSWORD
    )
    calls: list[str] = []
    with (
        patch.object(mailbox, "_create_client", server),
        patch.object(
            extract,
            "_generate_data",
            AsyncMock(side_effect=replying({"extract.item"}, calls)),
        ),
    ):
        hub = await setup_hub(hass, STORES)
        summary = await hub.purchases.async_check(manual=True)
    assert summary["error"] is None and summary["new_cards"] >= 1
    assert calls.count("extract.item") == 2  # rejected for both emails
    logged = [r for r in caplog.records if "too complex" in r.getMessage()]
    assert len(logged) == 1
    assert "'item' (9 fields)" in logged[0].getMessage()
    card = next(iter(hub.purchases.cards.values()))
    assert card["size"] == "M" and card["cut"] == "Regular"
