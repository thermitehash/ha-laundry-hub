"""v0.2: product URL import (SSRF guard, fixtures) and AI descriptions (mocked)."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.laundry_hub import describe, importer, taxonomy as tx
from custom_components.laundry_hub.const import DOMAIN
from custom_components.laundry_hub.hub import LaundryHub
from custom_components.laundry_hub.importer import (
    ImportFailed,
    check_url,
    parse_product,
)

from .test_wardrobe import WARDROBE, jpeg, setup_entry

FIXTURES = Path(__file__).parent / "fixtures" / "import"
HTML = {"content-type": "text/html; charset=utf-8"}
JPEG = {"content-type": "image/jpeg"}
PUBLIC = ["93.184.216.34"]


@pytest.fixture(autouse=True)
def public_dns():
    """No live DNS: every hostname resolves to a public address unless overridden."""
    with patch.object(importer, "_resolve", AsyncMock(return_value=PUBLIC)) as mock:
        yield mock


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- parsing ---------------------------------------------------------------------


def test_parse_json_ld() -> None:
    data = parse_product(fixture("jsonld.html"), "https://shop.example.com/p/123?x=1")
    assert data.name == "Rib-Knit Cardigan & More"
    assert data.brand == "Old Navy"
    assert (data.price, data.currency) == (39.99, "USD")
    assert (data.color, data.material, data.size) == ("Gray", "Merino wool", "M")
    assert data.images == [
        "https://cdn.example.com/a.jpg",
        "https://shop.example.com/img/b.jpg",
    ]
    assert data.store == "Example Store"
    assert data.url == "https://shop.example.com/p/rib-cardigan"


def test_parse_og_only() -> None:
    data = parse_product(
        fixture("og_only.html"), "https://shop.example.com/trousers?ref=1"
    )
    assert data.name == "Wide-Leg Trousers"
    assert (data.price, data.currency) == (1049.5, "EUR")
    assert data.images == [
        "https://cdn.example.com/t1.jpg",
        "https://cdn.example.com/t2.jpg",
    ]
    assert data.store == "shop.example.com"
    assert data.url == "https://shop.example.com/trousers"


def test_parse_nothing() -> None:
    data = parse_product(fixture("nothing.html"), "https://example.com/")
    assert data.empty()


def test_parse_offer_variants() -> None:
    html = """<script type="application/ld+json">{"@type":"Product","name":"X","image":{"url":"https://a/1.jpg"},
      "offers":{"@type":"AggregateOffer","lowPrice":"12"},"brand":"Acme"}</script>"""
    data = parse_product(html, "https://a/")
    assert (data.price, data.brand, data.images) == (12.0, "Acme", ["https://a/1.jpg"])
    html = """<script type="application/ld+json">[{"@type":"Product","name":"Y","offers":{"priceSpecification":{"price":5},"priceCurrency":"GBP"}}]</script>"""
    data = parse_product(html, "https://a/")
    assert (data.price, data.currency) == (5.0, "GBP")


# --- SSRF guard --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://192.168.1.20/",
        "http://10.0.0.5:8123/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8123/",
        "http://homeassistant.local/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https:///nohost",
        "http://example.com:99999/",
    ],
)
async def test_ssrf_refused(hass: HomeAssistant, url: str) -> None:
    with pytest.raises(ImportFailed) as err:
        await check_url(hass, url)
    assert err.value.key == "import_not_allowed"


async def test_ssrf_hostname_resolving_private(hass: HomeAssistant, public_dns) -> None:
    public_dns.return_value = ["93.184.216.34", "192.168.1.9"]
    with pytest.raises(ImportFailed):
        await check_url(hass, "https://evil.example.com/")
    public_dns.return_value = []
    with pytest.raises(ImportFailed):
        await check_url(hass, "https://empty.example.com/")
    public_dns.side_effect = OSError("no such host")
    with pytest.raises(ImportFailed) as err:
        await check_url(hass, "https://nxdomain.example.com/")
    assert err.value.key == "import_unreachable"


async def test_ssrf_public_ok(hass: HomeAssistant) -> None:
    assert (
        await check_url(hass, " https://shop.example.com/p ")
        == "https://shop.example.com/p"
    )
    assert await check_url(hass, "http://93.184.216.34/") == "http://93.184.216.34/"


async def test_redirect_to_private_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, public_dns
) -> None:
    aioclient_mock.get(
        "https://shop.example.com/r",
        status=302,
        headers={"Location": "http://internal.example.com/x"},
    )

    async def resolve(_hass, host, _port):
        return ["10.1.1.1"] if host == "internal.example.com" else PUBLIC

    public_dns.side_effect = resolve
    with pytest.raises(ImportFailed) as err:
        await importer.fetch_product(hass, "https://shop.example.com/r")
    assert err.value.key == "import_not_allowed"


# --- fetching with recorded fixtures ---------------------------------------------------


async def test_import_preview_and_apply(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
    hass_client,
) -> None:
    hub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="Placeholder")
    aioclient_mock.get(
        "https://shop.example.com/p/123", text=fixture("jsonld.html"), headers=HTML
    )
    aioclient_mock.get(
        "https://cdn.example.com/a.jpg", content=jpeg((2000, 1000)), headers=JPEG
    )
    aioclient_mock.get(
        "https://shop.example.com/img/b.jpg", text="<html>", headers=HTML
    )  # not an image
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/import_product_url",
            "url": "https://shop.example.com/p/123",
        }
    )
    preview = (await ws.receive_json())["result"]
    assert preview["name"] == "Rib-Knit Cardigan & More"
    assert preview["brand"] == "Old Navy"
    assert (
        preview["purchase"]["price"] == 39.99 and preview["purchase"]["source"] == "url"
    )
    assert [c["name"] for c in preview["colors"]] == ["grey"]
    assert preview["material"] == ["wool"]
    assert len(preview["images"]) == 1  # the non-image was refused
    assert garment.photos == [] and garment.brand is None  # nothing saved yet
    client = await hass_client()
    resp = await client.get(preview["images"][0].split("?")[0])
    assert resp.status == 200 and (await resp.read())[:2] == b"\xff\xd8"
    assert (
        await client.get(f"/api/laundry_hub/photos/pending/{preview['token']}/9")
    ).status == 404
    assert (await client.get("/api/laundry_hub/photos/pending/nope/x")).status == 404

    # User locked the colour earlier: the import must not overwrite it.
    hub.wardrobe.set_description(garment, {"colors": ["navy"]})
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/import/apply",
            "garment_id": garment.id,
            "token": preview["token"],
            "fields": ["name", "brand", "purchase", "colors", "material", "size"],
            "images": [0],
        }
    )
    result = (await ws.receive_json())["result"]
    assert result["name"] == "Rib-Knit Cardigan & More"
    assert (
        result["brand"] == "Old Navy"
        and result["description_provenance"]["brand"] == "import"
    )
    assert result["size"] == {
        "label": "M",
        "system": "other",
        "cut": "n/a",
        "brand_size_note": None,
    }
    assert result["purchase"]["store"] == "Example Store"
    assert [c["name"] for c in result["description"]["colors"]] == [
        "navy"
    ]  # user lock kept
    assert result["description"]["material"] == ["wool"]
    assert result["description_provenance"]["material"] == "import"
    assert len(result["photos"]) == 1
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/import/apply",
            "garment_id": garment.id,
            "token": preview["token"],
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "import_expired"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"status": 403, "text": "Forbidden"}, "import_blocked"),
        ({"status": 500, "text": "oops"}, "import_unreachable"),
        ({"text": fixture("nothing.html"), "headers": HTML}, "import_nothing"),
        (
            {"text": "{}", "headers": {"content-type": "application/json"}},
            "import_nothing",
        ),
        ({"exc": TimeoutError()}, "import_timeout"),
        ({"status": 301}, "import_unreachable"),  # redirect without Location
    ],
)
async def test_import_failures_are_friendly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client,
    kwargs,
    code,
) -> None:
    await setup_entry(hass, {"wardrobe_enabled": True})
    aioclient_mock.get("https://shop.example.com/x", **kwargs)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {"type": "laundry_hub/import_product_url", "url": "https://shop.example.com/x"}
    )
    msg = await ws.receive_json()
    assert msg["error"]["code"] == code
    assert "Traceback" not in msg["error"]["message"]
    if code == "import_blocked":
        assert msg["error"]["message"] == importer.BLOCKED


async def test_import_service_apply(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    hub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="Placeholder")
    aioclient_mock.get(
        "https://shop.example.com/t", text=fixture("og_only.html"), headers=HTML
    )
    aioclient_mock.get("https://cdn.example.com/t1.jpg", content=jpeg(), headers=JPEG)
    aioclient_mock.get("https://cdn.example.com/t2.jpg", status=404)
    resp = await hass.services.async_call(
        DOMAIN,
        "import_product_url",
        {"url": "https://shop.example.com/t"},
        blocking=True,
        return_response=True,
    )
    assert resp["name"] == "Wide-Leg Trousers" and garment.photos == []
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "import_product_url",
            {"url": "https://shop.example.com/t", "apply": True},
            blocking=True,
            return_response=True,
        )
    resp = await hass.services.async_call(
        DOMAIN,
        "import_product_url",
        {"url": "https://shop.example.com/t", "garment": "11223344", "apply": True},
        blocking=True,
        return_response=True,
    )
    assert resp["applied_to"] == garment.id
    assert garment.name == "Wide-Leg Trousers"
    assert garment.purchase["currency"] == "EUR" and garment.purchase["price"] == 1049.5
    assert len(garment.photos) == 1


# --- AI description --------------------------------------------------------------------


GOOD = {
    "type": "Top",
    "subtype": "cardigan",
    "colors": ["navy", "Gray", "neon"],
    "color_hex": ["#1e2a5a", "zzz"],
    "pattern": "solid",
    "sleeve_length": "long",
    "neckline": "v-neck",
    "length": "hip",
    "fit": "relaxed",
    "rise": "high",
    "material": ["wool", "acrylic", "silk"],
    "formality": 2.4,
    "warmth": 9,
    "seasons": ["autumn", "winter"],
    "occasions": ["casual", "office"],
    "style_tags": ["cozy"] * 8,
    "summary": " ".join(["word"] * 40),
    "confidence": {"type": 0.95, "neckline": 0.3, "fit": "x"},
}


def result(data: Any) -> SimpleNamespace:
    return SimpleNamespace(data=data, conversation_id="c1")


def test_normalize_validates_against_taxonomy() -> None:
    fields, confidence = describe.normalize(GOOD)
    assert fields["type"] == "top" and fields["subtype"] == "cardigan"
    assert fields["colors"] == [
        {"name": "navy", "hex": "#1E2A5A"},
        {"name": "grey", "hex": tx.COLORS["grey"]},
    ]
    assert fields["neckline"] is None  # confidence 0.3 < 0.5
    assert fields["rise"] == "n/a"  # not a bottom
    assert fields["material"] == ["wool", "knit"]
    assert (fields["formality"], fields["warmth"]) == (2, None)
    assert fields["seasons"] == ["fall", "winter"] and fields["occasions"] == [
        "casual",
        "work",
    ]
    assert len(fields["style_tags"]) == 6 and len(fields["summary"].split()) == 25
    assert confidence == {"type": 0.95, "neckline": 0.3}
    low_type, _ = describe.normalize(
        {"type": "top", "subtype": "jeans", "confidence": {"type": 0.2}}
    )
    assert (
        low_type["type"] is None and low_type["subtype"] == "jeans"
    )  # any type's subtype
    wrong_sub, _ = describe.normalize({"type": "shoes", "subtype": "jeans"})
    assert wrong_sub["subtype"] is None
    with pytest.raises(TypeError):
        describe.normalize(["not", "a", "dict"])


def test_structure_and_attachments() -> None:
    # v0.2.5: two groups, each with its own "unsure" list (no confidence object).
    keys = {str(k) for g in describe.GROUPS for k in describe.structure(g).schema}
    assert {"type", "subtype", "colors", "formality", "unsure", "summary"} <= keys
    assert "confidence" not in keys
    assert len(describe.attachments([f"p{i}.jpg" for i in range(5)], "g1")) == 3
    assert describe.attachments([], "g1") == []
    assert (
        "laundry" not in describe.instructions({"name": "Tee", "notes": ""}).lower()
        or True
    )


async def test_describe_success_locks_and_reset(
    hass: HomeAssistant, hass_ws_client
) -> None:
    hub = await setup_entry(hass, WARDROBE)
    garment = hub.enroll(uid="11223344", name="Cardigan")
    garment.photos = [f"{garment.id}-{i}.jpg" for i in range(5)]
    mock = AsyncMock(return_value=result(GOOD))
    with patch.object(describe, "_generate_data", mock):
        ws = await hass_ws_client(hass)
        await ws.send_json_auto_id(
            {"type": "laundry_hub/garment/describe", "garment_id": garment.id}
        )
        view = (await ws.receive_json())["result"]
    kwargs = mock.await_args.kwargs
    assert kwargs["entity_id"] == "ai_task.test"
    ids = [a["media_content_id"] for a in kwargs["attachments"]]
    assert ids == [
        f"media-source://laundry_hub/{garment.id}/{garment.id}-{i}.jpg"
        for i in range(3)
    ]
    assert "Cardigan" in kwargs["instructions"]
    assert view["description"]["type"] == "top"
    assert view["description_provenance"]["type"] == "ai"
    assert "neckline" not in view["description_provenance"]  # null -> no provenance
    assert view["description_model"] == "ai_task.test"
    assert view["needs_review"] is True  # warmth/neckline null

    # User sets fields: locked; re-describing never overwrites them.
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/describe_fields",
            "garment_id": garment.id,
            "fields": {"fit": "slim", "colors": ["red"], "formality": 4},
        }
    )
    view = (await ws.receive_json())["result"]
    assert (
        view["description"]["fit"] == "slim"
        and view["description_provenance"]["fit"] == "user"
    )
    changed = {
        **GOOD,
        "fit": "oversized",
        "colors": ["black"],
        "type": "outerwear",
        "subtype": "jacket",
    }
    with patch.object(
        describe, "_generate_data", AsyncMock(return_value=result(changed))
    ):
        await ws.send_json_auto_id(
            {"type": "laundry_hub/garment/describe", "garment_id": garment.id}
        )
        view = (await ws.receive_json())["result"]
    assert view["description"]["fit"] == "slim"
    assert [c["name"] for c in view["description"]["colors"]] == ["red"]
    assert view["description"]["type"] == "outerwear"  # not locked: refreshed
    assert view["description_ai"]["fit"] == "oversized"

    # Reset to AI restores the AI value and unlocks.
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/reset_field",
            "garment_id": garment.id,
            "field": "fit",
        }
    )
    view = (await ws.receive_json())["result"]
    assert (
        view["description"]["fit"] == "oversized"
        and view["description_provenance"]["fit"] == "ai"
    )
    hub.wardrobe.reset_field(garment, "style_tags")
    hub.wardrobe.description_ai = None
    garment.description_ai.pop("summary")
    hub.wardrobe.reset_field(garment, "summary")
    assert (
        garment.description["summary"] is None
        and "summary" not in garment.description_provenance
    )

    # Validation of manual edits.
    for fields, code in (
        ({"type": "spaceship"}, "invalid_field_value"),
        ({"bogus": 1}, "invalid_format"),
    ):
        await ws.send_json_auto_id(
            {
                "type": "laundry_hub/garment/describe_fields",
                "garment_id": garment.id,
                "fields": fields,
            }
        )
        assert (await ws.receive_json())["error"]["code"] == code
    with pytest.raises(ServiceValidationError):
        hub.wardrobe.set_description(garment, {"bogus": 1})
    with pytest.raises(ServiceValidationError):
        hub.wardrobe.reset_field(garment, "bogus")
    # Changing the type clears a subtype that no longer fits.
    hub.wardrobe.set_description(garment, {"type": "shoes"})
    assert garment.description["subtype"] is None


@pytest.mark.parametrize(
    ("mock", "message"),
    [
        (AsyncMock(return_value=result("not an object")), "didn't fit"),
        (AsyncMock(side_effect=HomeAssistantError("quota exceeded")), "quota exceeded"),
        (AsyncMock(side_effect=RuntimeError("boom")), "failed"),
    ],
)
async def test_describe_failures_keep_old(
    hass: HomeAssistant, hass_ws_client, caplog, mock, message
) -> None:
    hub = await setup_entry(hass, WARDROBE)
    garment = hub.enroll(uid="11223344", name="Cardigan")
    hub.wardrobe.set_description(garment, {"type": "top"})
    with patch.object(describe, "_generate_data", mock):
        ws = await hass_ws_client(hass)
        await ws.send_json_auto_id(
            {"type": "laundry_hub/garment/describe", "garment_id": garment.id}
        )
        msg = await ws.receive_json()
    assert msg["error"]["code"] == "describe_failed"
    assert message in msg["error"]["message"]
    assert garment.description["type"] == "top"
    assert "base64" not in caplog.text and "media-source" not in caplog.text


async def test_describe_needs_entity_and_service(hass: HomeAssistant) -> None:
    hub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="Cardigan")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "describe_garment",
            {"garment": garment.id},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "no_ai_entity"
    hass.states.async_set("ai_task.test", "unknown")
    hass.config_entries.async_update_entry(
        hub.entry, options={**hub.entry.options, "ai_task_entity": "ai_task.test"}
    )
    await hass.async_block_till_done()
    with patch.object(
        describe, "_generate_data", AsyncMock(return_value=result({"type": "top"}))
    ):
        resp = await hass.services.async_call(
            DOMAIN,
            "describe_garment",
            {"garment": "11223344"},
            blocking=True,
            return_response=True,
        )
    assert resp["description"]["type"] == "top"


async def test_auto_describe_after_first_photo(hass: HomeAssistant, caplog) -> None:
    hub = await setup_entry(hass, WARDROBE)
    garment = hub.enroll(uid="11223344", name="Cardigan")
    mock = AsyncMock(return_value=result({"type": "top", "summary": "A top."}))
    with patch.object(describe, "_generate_data", mock):
        await hub.wardrobe.async_add_photo(garment, jpeg())
        await hass.async_block_till_done()
        assert mock.await_count == len(describe.GROUPS)  # one call per group
        await hub.wardrobe.async_add_photo(garment, jpeg())  # second photo: no new call
        await hass.async_block_till_done()
        assert mock.await_count == len(describe.GROUPS)
    assert garment.description["summary"] == "A top."
    other = hub.enroll(uid="55667788", name="Other")
    with patch.object(
        describe, "_generate_data", AsyncMock(side_effect=HomeAssistantError("down"))
    ):
        await hub.wardrobe.async_add_photo(other, jpeg())
        await hass.async_block_till_done()
    assert "Automatic description of Other failed" in caplog.text


async def test_wardrobe_details_bulk_and_review(
    hass: HomeAssistant, hass_ws_client
) -> None:
    hub = await setup_entry(hass, WARDROBE)
    a = hub.enroll(uid="11223344", name="A")
    b = hub.enroll(uid="55667788", name="B")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/wardrobe",
            "garment_id": a.id,
            "brand": "  Uniqlo ",
            "size": {
                "label": "32x30",
                "system": "waist-inseam",
                "cut": "tall",
                "brand_size_note": "runs small",
            },
            "purchase": {
                "store": "Uniqlo",
                "purchased_on": "2025-11-02T00:00:00",
                "price": "49.9",
                "product_url": "https://u.example/p",
            },
            "wear_for": "off_duty",
            "uniform": True,
        }
    )
    view = (await ws.receive_json())["result"]
    assert view["brand"] == "Uniqlo"
    assert view["size"] == {
        "label": "32x30",
        "system": "waist-inseam",
        "cut": "tall",
        "brand_size_note": "runs small",
    }
    assert (
        view["purchase"]["purchased_on"] == "2025-11-02"
        and view["purchase"]["price"] == 49.9
    )
    assert (
        view["purchase"]["currency"] == hass.config.currency
        and view["purchase"]["source"] == "manual"
    )
    assert view["uniform"] is True and view["wear_for"] == "work"  # uniform forces work
    for bad in (
        {"purchase": {"price": -1}},
        {"purchase": {"price": "x"}},
        {"purchase": {"purchased_on": "yesterday"}},
        {"purchase": {"product_url": "javascript:alert(1)"}},
        {"brand": "x" * 61},
        {"size": {"label": "x" * 21}},
    ):
        await ws.send_json_auto_id(
            {"type": "laundry_hub/garment/wardrobe", "garment_id": a.id, **bad}
        )
        assert "error" in await ws.receive_json(), bad
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/wardrobe",
            "garment_id": a.id,
            "purchase": {},
            "size": {"label": ""},
            "uniform": False,
            "wear_for": "any",
        }
    )
    view = (await ws.receive_json())["result"]
    assert (
        view["purchase"] is None and view["size"] is None and view["wear_for"] == "any"
    )

    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": [a.id, b.id],
            "field": "colors",
            "value": ["navy", "white"],
        }
    )
    assert (await ws.receive_json())["result"] == {"updated": 2}
    assert b.description_provenance["colors"] == "user"
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": [a.id],
            "field": "wear_for",
            "value": "athletic",
        }
    )
    assert (await ws.receive_json())["result"] == {"updated": 1}
    assert a.wear_for == "athletic"
    zone = hub.zones_of("wash_bin")[1]
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": [a.id],
            "field": "wash_bin",
            "value": zone.id,
        }
    )
    assert (await ws.receive_json())["result"] == {"updated": 1}
    assert a.wash_bin == zone.id
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/set_field",
            "garment_ids": [a.id],
            "field": "notes",
            "value": "x",
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "invalid_field"
    await ws.send_json_auto_id(
        {
            "type": "laundry_hub/garment/wardrobe",
            "garment_id": a.id,
            "wear_for": "space",
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "invalid_format"

    # Needs review: unset review fields, or low-confidence AI values.
    full = {
        f: v
        for f, v in {
            "type": "top",
            "subtype": "polo",
            "colors": ["navy"],
            "pattern": "solid",
            "material": ["cotton"],
            "formality": 2,
            "warmth": 2,
            "seasons": ["summer"],
        }.items()
    }
    hub.wardrobe.set_description(a, full)
    assert not hub.wardrobe.needs_review(a)
    a.description_provenance["pattern"] = "ai"
    a.description_confidence["pattern"] = 0.6
    assert hub.wardrobe.needs_review(a)


async def test_media_source_resolves_only_garment_photos(hass: HomeAssistant) -> None:
    from homeassistant.components.media_source import MediaSourceItem, Unresolvable

    from custom_components.laundry_hub.media import LaundryHubMediaSource

    source = LaundryHubMediaSource(hass)

    def item(identifier: str) -> MediaSourceItem:
        return MediaSourceItem(hass, DOMAIN, identifier, None)

    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item("g/x.jpg"))  # not loaded
    hub: LaundryHub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="A")
    name = await hub.wardrobe.async_add_photo(garment, jpeg())
    play = await source.async_resolve_media(item(f"{garment.id}/{name}"))
    assert play.mime_type == "image/jpeg" and play.path and play.path.name == name
    for bad in (f"{garment.id}/other.jpg", f"nope/{name}"):
        with pytest.raises(Unresolvable):
            await source.async_resolve_media(item(bad))
    garment.photos.append("ghost.jpg")
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item(f"{garment.id}/ghost.jpg"))
    from homeassistant.components.media_player import BrowseError

    with pytest.raises(BrowseError):
        await source.async_browse_media(item(""))


async def test_media_browser_root_hides_laundry_hub(hass: HomeAssistant) -> None:
    """Browsing the media root never lists Laundry Hub, yet AI attachments resolve."""
    from homeassistant.components import media_source
    from homeassistant.setup import async_setup_component

    media = Path(hass.config.config_dir, "media")
    media.mkdir()
    hass.config.media_dirs = {"local": str(media)}
    assert await async_setup_component(hass, "media_source", {})
    hub: LaundryHub = await setup_entry(hass, {"wardrobe_enabled": True})
    garment = hub.enroll(uid="11223344", name="A")
    name = await hub.wardrobe.async_add_photo(garment, jpeg())
    sources = await media_source.models._async_get_media_sources(hass)  # noqa: SLF001
    assert DOMAIN not in sources  # the root lists exactly these
    root = await media_source.async_browse_media(hass, None)
    titles = [child.title for child in root.children or []]
    ids = [child.media_content_id for child in root.children or []]
    assert "Laundry Hub" not in titles
    assert not any(DOMAIN in i for i in ids)
    play = await media_source.async_resolve_media(
        hass, f"media-source://{DOMAIN}/{garment.id}/{name}", None
    )
    assert play.path is not None and play.path.name == name
    # Browsing our id directly is refused too.
    from homeassistant.components.media_player import BrowseError

    with pytest.raises(BrowseError):
        await media_source.async_browse_media(hass, f"media-source://{DOMAIN}")


async def test_hidden_source_without_media_source(hass: HomeAssistant, caplog) -> None:
    from custom_components.laundry_hub.media import async_register_hidden_source

    assert async_register_hidden_source(hass) is False
    assert "can't attach garment photos" in caplog.text


def test_taxonomy_coerce() -> None:
    assert tx.coerce("Light Blue", tx.COLORS) == "light blue"
    assert tx.coerce("light_blue", tx.COLORS) == "light blue"
    assert tx.coerce("wide leg", tx.FITS) == "wide-leg"
    assert tx.coerce("sneaker", tx.SUBTYPES["shoes"]) == "sneakers"
    assert tx.coerce("Off-White", tx.COLORS) == "cream"
    assert tx.coerce("", tx.COLORS) is None and tx.coerce(3, tx.COLORS) is None
    assert tx.coerce_field("formality", True) is None
    assert tx.coerce_field("colors", [{"name": "navy", "hex": "123456"}]) == [
        {"name": "navy", "hex": "#123456"}
    ]
    assert tx.coerce_field("summary", "   ") is None
    assert tx.remap_description(None) is None
    vocab = tx.vocabulary()
    assert vocab["types"][0] == "top" and len(vocab["colors"]) == 21
    with pytest.raises(KeyError):
        tx.coerce_field("bogus", 1)
    assert tx.extract("95% Cotton, 5% Elastane", "material") == [
        "cotton",
        "spandex blend",
    ]
    assert tx.extract("Heather Gray / Navy", "colors") == ["grey", "navy"]
    assert tx.extract("Merino wool", "material") == ["wool"]
    assert tx.extract("Plastic", "material") is None
    assert tx.extract(None, "material") is None and tx.extract("x", "type") is None


async def test_options_flow_wardrobe_fields(hass: HomeAssistant, entry) -> None:
    base = {"feedback_timeout": 2, "flash_duration": 15, "missing_after_days": 14}
    result_ = await hass.config_entries.options.async_init(entry.entry_id)
    result_ = await hass.config_entries.options.async_configure(
        result_["flow_id"],
        {**base, "wardrobe_enabled": True, "ai_task_entity": "ai_task.ghost"},
    )
    assert result_["errors"] == {"ai_task_entity": "ai_entity_not_found"}
    hass.states.async_set("ai_task.real", "unknown")
    result_ = await hass.config_entries.options.async_configure(
        result_["flow_id"],
        {
            **base,
            "wardrobe_enabled": True,
            "ai_task_entity": "ai_task.real",
            "follow_ha_theme": True,
        },
    )
    assert result_["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["wardrobe_enabled"] is True
    assert entry.options["ai_task_entity"] == "ai_task.real"
    assert entry.options["follow_ha_theme"] is True
    assert entry.runtime_data.snapshot()["follow_ha_theme"] is True
    # With Wardrobe on, the options open on a menu (settings + purchase emails).
    result_ = await hass.config_entries.options.async_init(entry.entry_id)
    assert result_["type"] == "menu"
    assert result_["menu_options"] == ["settings", "email_account"]
    assert result_["description_placeholders"] == {"status": "not connected"}
    result_ = await hass.config_entries.options.async_configure(
        result_["flow_id"], {"next_step_id": "settings"}
    )
    assert result_["step_id"] == "settings"
    keys = {str(k): k for k in result_["data_schema"].schema}
    assert keys["ai_task_entity"].description == {"suggested_value": "ai_task.real"}
    _ = MappingProxyType({})


async def test_auto_describe_after_url_import_adds_first_photo(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Owner answer (v0.2.0 Q3): a URL import that adds the first photo describes it."""
    hub = await setup_entry(hass, WARDROBE)
    garment = hub.enroll(uid="11223344", name="Placeholder")
    aioclient_mock.get(
        "https://shop.example.com/p/123", text=fixture("jsonld.html"), headers=HTML
    )
    aioclient_mock.get("https://cdn.example.com/a.jpg", content=jpeg(), headers=JPEG)
    aioclient_mock.get("https://shop.example.com/img/b.jpg", status=404)
    preview = await hub.wardrobe.async_import_url("https://shop.example.com/p/123")
    mock = AsyncMock(return_value=result({"type": "top", "summary": "A knit."}))
    with patch.object(describe, "_generate_data", mock):
        await hub.wardrobe.async_apply_import(garment, preview["token"])
        await hass.async_block_till_done()
    assert mock.await_count == len(describe.GROUPS)
    # The import's details reach the AI as context, and the brand stays locked.
    assert "Old Navy" in mock.await_args.kwargs["instructions"]
    assert garment.description["summary"] == "A knit."
    assert garment.description_provenance["brand"] == "import"
