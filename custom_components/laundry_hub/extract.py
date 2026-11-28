"""Turn an order email into purchase items with AI Task.

* ``html_to_text`` strips HTML with the standard library, keeping each
  ``<img>`` source and link target next to the text it belongs to, so the AI
  can tie a product photo and link to an item.
* ``structure`` is the selector-built schema passed to ``ai_task``.
* ``validate`` re-checks the answer: a product or image link is kept only if
  it appears verbatim in the text that was sent (no invented links), numbers
  and dates are parsed, and non-apparel is dropped when asked.

Neither the email text nor the raw AI answer is stored or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
import re
from typing import Any

from homeassistant.helpers import selector
import probatio

TASK_NAME = "Laundry Hub purchase email"
MAX_TEXT = 12_000
MAX_ITEMS = 25
_SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template"}
_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "ul", "ol", "td",
}  # fmt: skip
_TRACKING_PIXEL = re.compile(r"(?:1x1|pixel|spacer|track|beacon|open\.gif)", re.I)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._href: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "img" and not self._skip:
            src = a.get("src", "").strip()
            if src.startswith(("http://", "https://")) and not _tiny(a, src):
                alt = " ".join(a.get("alt", "").split())
                self.out.append(f" [image{': ' + alt if alt else ''}: {src}] ")
        elif tag == "a":
            href = a.get("href", "").strip()
            self._href.append(
                href if href.startswith(("http://", "https://")) else None
            )
        if tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href:
            href = self._href.pop()
            if href and not self._skip:
                self.out.append(f" [link: {href}] ")
        if tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.out.append(data)


def _tiny(attrs: dict[str, str], src: str) -> bool:
    """Tracking pixels and spacers aren't product photos."""
    sizes = [attrs.get("width", ""), attrs.get("height", "")]
    if any(s.strip().rstrip("px") in ("0", "1") for s in sizes):
        return True
    return bool(_TRACKING_PIXEL.search(src.rsplit("/", 1)[-1]))


def html_to_text(html: str, limit: int = MAX_TEXT) -> str:
    """Readable text with [image: URL] and [link: URL] markers, truncated."""
    parser = _TextParser()
    try:
        parser.feed(html)
        parser.close()
    except (ValueError, AssertionError):  # pragma: no cover - parser is lenient
        pass
    text = "".join(parser.out)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    compact = "\n".join(line for line in lines if line)
    return compact[:limit]


def plain_to_text(plain: str, limit: int = MAX_TEXT) -> str:
    """Normalize a text/plain body the same way (whitespace, length)."""
    lines = [" ".join(line.split()) for line in plain.splitlines()]
    return "\n".join(line for line in lines if line)[:limit]


def _text_field() -> dict[str, Any]:
    return {"selector": {"text": {}}}


def structure() -> probatio.Schema:
    """Schema for ai_task.generate_data: loose on purpose.

    Every field is optional and plain text (prices like "$12.99", quantities
    like "2"), items may be empty, and nothing is required, so a provider
    never fails on shape. Everything is parsed and validated locally.
    """
    text = selector.selector({"text": {}})
    boolean = selector.selector({"boolean": {}})
    item_fields = {
        key: _text_field()
        for key in (
            "name", "brand", "color", "size", "quantity", "unit_price",
            "product_url", "image_url",
        )
    }  # fmt: skip
    item_fields["is_apparel"] = {"selector": {"boolean": {}}}
    items = selector.selector({"object": {"fields": item_fields, "multiple": True}})
    descriptions = {
        "is_order": "True if this email confirms, ships or delivers a purchase.",
        "order_ref": "The store's order number, exactly as written.",
        "order_date": "Order date as YYYY-MM-DD.",
        "currency": "ISO 4217 code, e.g. USD.",
        "items": (
            "One entry per product; empty if the email lists none (for example a "
            'shipping notice). Prices may be written as in the email ("$12.99"). '
            "product_url and image_url must be copied exactly from a [link: ...] "
            "or [image: ...] marker, or left out."
        ),
    }
    fields: dict[str, Any] = {
        "is_order": boolean,
        "store": text,
        "order_ref": text,
        "order_date": text,
        "currency": text,
        "total": text,
        "items": items,
    }
    return probatio.Schema(
        {
            probatio.Optional(key, description=descriptions.get(key)): value
            for key, value in fields.items()
        },
        extra=probatio.REMOVE_EXTRA,
    )


def minimal_structure() -> probatio.Schema:
    """Fallback after a failed call: only what a card needs."""
    items = selector.selector(
        {
            "object": {
                "fields": {"name": _text_field(), "image_url": _text_field()},
                "multiple": True,
            }
        }
    )
    return probatio.Schema(
        {
            probatio.Optional("is_order"): selector.selector({"boolean": {}}),
            probatio.Optional("store"): selector.selector({"text": {}}),
            probatio.Optional("order_ref"): selector.selector({"text": {}}),
            probatio.Optional("items"): items,
        },
        extra=probatio.REMOVE_EXTRA,
    )


def instructions(store_name: str, text: str) -> str:
    """Prompt: what to extract. The email text comes last, clearly fenced."""
    return "\n".join(
        [
            "Extract the purchased products from this order email for a household",
            "wardrobe catalog. Only use information in the email. If a value isn't in",
            "the email, leave it out. Never invent or modify links: product_url and",
            "image_url must be copied character for character from a [link: ...] or",
            "[image: ...] marker that sits next to that product.",
            "Set is_apparel to true only for clothing, shoes and wearable accessories.",
            "If the email is not about a purchase (marketing, newsletter), set",
            "is_order to false and return no items.",
            f"The email is from: {store_name}.",
            "----- EMAIL TEXT -----",
            text,
            "----- END EMAIL TEXT -----",
        ]
    )


@dataclass(slots=True)
class Item:
    """One validated purchase item."""

    name: str
    brand: str | None = None
    color: str | None = None
    size: str | None = None
    quantity: int = 1
    unit_price: float | None = None
    product_url: str | None = None
    image_url: str | None = None
    is_apparel: bool = True


@dataclass(slots=True)
class Order:
    """A validated order email."""

    is_order: bool
    order_ref: str | None = None
    order_date: date | None = None
    currency: str | None = None
    items: list[Item] = field(default_factory=list)
    dropped_urls: int = 0


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str | int | float) or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number(value: Any) -> float | None:
    """12.99, "12.99", "$12.99", "USD 1,234.50", "2 pcs" → number; else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    else:
        match = _AMOUNT.search(str(value))
        if match is None:
            return None
        number = float(match.group(0).replace(",", ""))
    return round(number, 2) if 0 <= number < 1_000_000 else None


def _date(value: Any) -> date | None:
    text = _clean(value, 40)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _verbatim(url: Any, sent: str) -> str | None:
    """Keep a URL only if it appears exactly in the text that was sent."""
    text = _clean(url, 2000)
    if not text or not text.startswith(("http://", "https://")):
        return None
    return text if text in sent else None


def validate(raw: Any, sent_text: str, *, clothing_only: bool) -> Order:
    """Validate an AI answer against the email text (TypeError if not an object)."""
    if not isinstance(raw, dict):
        raise TypeError("AI result is not an object")
    order = Order(
        is_order=raw.get("is_order") is not False,
        order_ref=_clean(raw.get("order_ref"), 60),
        order_date=_date(raw.get("order_date")),
        currency=(_clean(raw.get("currency"), 3) or "").upper() or None,
    )
    items = raw.get("items")
    if not order.is_order or not isinstance(items, list):
        return order
    for entry in items[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        name = _clean(entry.get("name"), 100)
        if not name:
            continue
        apparel = entry.get("is_apparel")
        is_apparel = apparel is not False  # unknown counts as clothing
        if clothing_only and not is_apparel:
            continue
        product_url = _verbatim(entry.get("product_url"), sent_text)
        image_url = _verbatim(entry.get("image_url"), sent_text)
        order.dropped_urls += sum(
            1
            for given, kept in (
                (entry.get("product_url"), product_url),
                (entry.get("image_url"), image_url),
            )
            if given and not kept
        )
        quantity = _number(entry.get("quantity"))  # "2", 2, None → 1 if missing
        order.items.append(
            Item(
                name=name,
                brand=_clean(entry.get("brand"), 60),
                color=_clean(entry.get("color"), 40),
                size=_clean(entry.get("size"), 20),
                quantity=max(1, int(quantity)) if quantity else 1,
                unit_price=_number(entry.get("unit_price")),
                product_url=product_url,
                image_url=image_url,
                is_apparel=is_apparel,
            )
        )
    return order


async def _generate_data(hass: Any, **kwargs: Any) -> Any:
    """Call ai_task.async_generate_data (imported lazily: optional dependency)."""
    from homeassistant.components.ai_task import (  # noqa: PLC0415
        async_generate_data,
    )

    return await async_generate_data(hass, **kwargs)


async def extract(
    hass: Any,
    *,
    entity_id: str,
    store_name: str,
    text: str,
    clothing_only: bool,
) -> Order:
    """Ask AI Task for the order details and validate them."""
    prompt = instructions(store_name, text)
    try:
        result = await _generate_data(
            hass,
            task_name=TASK_NAME,
            entity_id=entity_id,
            instructions=prompt,
            structure=structure(),
        )
        return validate(result.data, text, clothing_only=clothing_only)
    except Exception as err:
        first = err
    # Once more with the smallest structure that still makes a card.
    try:
        result = await _generate_data(
            hass,
            task_name=TASK_NAME,
            entity_id=entity_id,
            instructions=prompt,
            structure=minimal_structure(),
        )
        return validate(result.data, text, clothing_only=clothing_only)
    except Exception as err:
        raise err from first
