"""Turn an order email into purchase items with AI Task.

* ``html_to_text`` strips HTML with the standard library, keeping each
  ``<img>`` source and link target next to the text it belongs to, so the AI
  can tie a product photo and link to an item.
* Two passes, each with small flat structures (``STRUCTURES``) that stay
  within ``const.AI_STRUCTURE_BUDGET``: pass 1 reads the order and product
  names, pass 2 reads item details for at most ``BATCH_SIZE`` names per call.
* ``validate`` re-checks the answer: a product or image link is kept only if
  it appears verbatim in the text that was sent (no invented links), numbers
  and dates are parsed, and non-apparel is dropped when asked.

Neither the email text nor the raw AI answer is stored or logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
import re
from typing import Any

from homeassistant.helpers import selector
import probatio

from . import ai_schema

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


def _required(selector_config: dict[str, Any]) -> dict[str, Any]:
    """An object-selector field the provider must fill (empty text is fine)."""
    return {"selector": selector_config, "required": True}


_TEXT: dict[str, Any] = {"text": {}}
_BOOLEAN: dict[str, Any] = {"boolean": {}}


def _items(fields: dict[str, dict[str, Any]], description: str) -> dict[Any, Any]:
    """A required list of flat item objects (one nesting level, all required)."""
    return {
        probatio.Required("items", description=description): selector.selector(
            {
                "object": {
                    "fields": {k: _required(v) for k, v in fields.items()},
                    "multiple": True,
                }
            }
        )
    }


def _schema(fields: dict[Any, Any]) -> probatio.Schema:
    return probatio.Schema(fields, extra=probatio.REMOVE_EXTRA)


def _order_fields() -> dict[Any, Any]:
    text = selector.selector(_TEXT)
    return {
        probatio.Required(
            "is_order",
            description="True if this email confirms, ships or delivers a purchase.",
        ): selector.selector(_BOOLEAN),
        probatio.Required("store", description="Store name, or empty."): text,
        probatio.Required(
            "order_ref", description="The order number exactly as written, or empty."
        ): text,
        probatio.Required(
            "order_date", description="Order date as YYYY-MM-DD, or empty."
        ): text,
        probatio.Required(
            "currency", description="ISO 4217 code such as USD, or empty."
        ): text,
    }


_ITEMS_HELP = (
    "One entry per product line, in the order printed; empty if the email lists "
    "no products (for example a shipping notice)."
)


def order_structure() -> probatio.Schema:
    """Pass 1: the order and its product names (with the photo link)."""
    return _schema(
        {
            **_order_fields(),
            **_items(
                {"name": _TEXT, "image_url": _TEXT},
                _ITEMS_HELP + " image_url is copied exactly from an [image: ...] "
                "marker next to the product, or empty.",
            ),
        }
    )


def order_lines_structure() -> probatio.Schema:
    """Pass 1, smaller: product names as plain strings."""
    return _schema(
        {
            **_order_fields(),
            probatio.Required(
                "items", description=_ITEMS_HELP + " One product name per entry."
            ): selector.selector({"text": {"multiple": True}}),
        }
    )


_DETAIL_HELP = (
    "One entry per product name given, with name copied exactly. Leave a value "
    "empty if the email doesn't show it."
)
_APPAREL_FIELDS: dict[str, dict[str, Any]] = {
    "name": _TEXT,
    "is_apparel": _BOOLEAN,
    "is_health": _BOOLEAN,  # medication or health products: never stored (v0.2.6)
    "quantity": _TEXT,
    "unit_price": _TEXT,
    "line_total": _TEXT,  # when only a line total is printed (v0.2.6)
}
_LOOK_FIELDS: dict[str, dict[str, Any]] = {
    "name": _TEXT,
    "colour": _TEXT,
    "size": _TEXT,
    "cut": _TEXT,
    "brand": _TEXT,  # v0.2.6
    "product_url": _TEXT,  # keeps the product link when the split is used
}


_ITEM_FIELDS: dict[str, dict[str, Any]] = {**_APPAREL_FIELDS, **_LOOK_FIELDS}


def item_structure() -> probatio.Schema:
    """Pass 2, per batch of names: every detail of each item."""
    return _schema(
        _items(
            _ITEM_FIELDS,
            _DETAIL_HELP + " product_url is copied exactly from a [link: ...] marker "
            "next to the product, or empty.",
        )
    )


def apparel_structure() -> probatio.Schema:
    """Pass 2, split (a): is_apparel, is_health, quantity and price."""
    return _schema(_items(_APPAREL_FIELDS, _DETAIL_HELP))


def look_structure() -> probatio.Schema:
    """Pass 2, split (b): colour, size, cut, brand and the product link."""
    return _schema(
        _items(
            _LOOK_FIELDS,
            _DETAIL_HELP + " product_url is copied exactly from a [link: ...] marker "
            "next to the product, or empty.",
        )
    )


# Every structure extraction can send, largest first per pass (tests walk this).
STRUCTURES: dict[str, Callable[[], probatio.Schema]] = {
    "order": order_structure,
    "order_lines": order_lines_structure,
    "item": item_structure,
    "item_apparel": apparel_structure,
    "item_look": look_structure,
}
BATCH_SIZE = 10


def _fence(text: str) -> list[str]:
    return ["----- EMAIL TEXT -----", text, "----- END EMAIL TEXT -----"]


def instructions(store_name: str, text: str) -> str:
    """Pass 1 prompt: the order and its products. The email text comes last."""
    return "\n".join(
        [
            "List the purchased products in this order email for a household",
            "wardrobe catalog. Only use information in the email; leave a value empty",
            "if it isn't there. Give each product's name as printed. Never invent or",
            "modify links: image_url must be copied character for character from an",
            "[image: ...] marker next to that product.",
            "If the email is not about a purchase (marketing, newsletter), set",
            "is_order to false and return no items.",
            f"The email is from: {store_name}.",
            *_fence(text),
        ]
    )


def detail_instructions(store_name: str, text: str, names: list[str]) -> str:
    """Pass 2 prompt: details for the named products only."""
    listed = "\n".join(f"- {name}" for name in names)
    return "\n".join(
        [
            "For each product below, read its details from this order email.",
            "Only use information in the email; leave a value empty if it isn't",
            "there. Copy each name exactly as listed.",
            "is_apparel is true only for clothing, shoes and wearable accessories.",
            'quantity and unit_price may be written as in the email ("2", "$12.99").',
            "cut is the fit or cut if printed (slim, relaxed, tall, petite...).",
            "Never invent or modify links: product_url must be copied character for",
            "character from a [link: ...] marker next to that product.",
            f"The email is from: {store_name}.",
            "Products:",
            listed,
            *_fence(text),
        ]
    )


@dataclass(slots=True)
class Item:
    """One validated purchase item."""

    name: str
    brand: str | None = None
    color: str | None = None
    size: str | None = None
    cut: str | None = None
    quantity: int = 1
    unit_price: float | None = None
    product_url: str | None = None
    image_url: str | None = None
    is_apparel: bool = True
    is_health: bool = False


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


_SUFFIX = re.compile(r"^(?P<name>.+?)\s+[-–]\s+(?P<rest>[^-–]+/[^-–]+)$")


def split_suffix(
    name: str, color: str | None, size: str | None, cut: str | None
) -> tuple[str, str | None, str | None, str | None]:
    """Split a " - A / B[ / C]" name suffix into empty colour/size/cut fields.

    "Ferrosi Pants - Black / M / Tall" → ("Ferrosi Pants", "Black", "M",
    "Tall"). Fields that already have a value keep it; the name is cleaned
    only when the suffix was split.
    """
    match = _SUFFIX.match(name)
    if match is None:
        return name, color, size, cut
    parts = [" ".join(p.split()) for p in match.group("rest").split("/")]
    if not 2 <= len(parts) <= 3 or not all(parts) or any(len(p) > 30 for p in parts):
        return name, color, size, cut
    parts += [""] * (3 - len(parts))
    return (
        match.group("name").strip(),
        color or parts[0] or None,
        size or parts[1] or None,
        cut or parts[2] or None,
    )


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
        if entry.get("is_health") is True:
            continue  # medication or health: never stored, never logged by name
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
        quantity_raw = _number(entry.get("quantity"))  # "2", 2, None → 1
        quantity = max(1, int(quantity_raw)) if quantity_raw else 1
        unit_price = _number(entry.get("unit_price"))
        total = _number(entry.get("line_total"))
        if unit_price is None and total is not None:
            unit_price = round(total / quantity, 2)
        color = _clean(entry.get("color") or entry.get("colour"), 40)
        size = _clean(entry.get("size"), 20)
        cut = _clean(entry.get("cut"), 30)
        name, color, size, cut = split_suffix(name, color, size, cut)
        order.items.append(
            Item(
                name=name,
                brand=_clean(entry.get("brand"), 60),
                color=color,
                size=size,
                cut=cut,
                quantity=quantity,
                unit_price=unit_price,
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


SchemaErrorHook = Callable[[str, int], None]


async def _ask(
    hass: Any,
    entity_id: str,
    prompt: str,
    name: str,
    on_schema_error: SchemaErrorHook | None,
) -> dict[str, Any]:
    """One AI Task call with the named structure; reports schema rejections."""
    structure = STRUCTURES[name]()
    try:
        result = await _generate_data(
            hass,
            task_name=TASK_NAME,
            entity_id=entity_id,
            instructions=prompt,
            structure=structure,
        )
    except Exception as err:
        if on_schema_error is not None and ai_schema.is_schema_error(err):
            on_schema_error(name, ai_schema.complexity(structure).properties)
        raise
    data = result.data
    if not isinstance(data, dict):
        raise TypeError("AI result is not an object")
    return data


async def _first_pass(
    hass: Any, entity_id: str, prompt: str, hook: SchemaErrorHook | None
) -> dict[str, Any]:
    """The order and its product names: object items, then plain strings."""
    try:
        return await _ask(hass, entity_id, prompt, "order", hook)
    except Exception as err:
        first = err
    try:
        raw = await _ask(hass, entity_id, prompt, "order_lines", hook)
    except Exception as err:
        raise err from first
    lines = raw.get("items")
    raw["items"] = (
        [{"name": line} for line in lines if isinstance(line, str)]
        if isinstance(lines, list)
        else []
    )
    return raw


def _by_name(
    names: list[str], answers: Any, fields: Iterable[str]
) -> list[dict[str, Any]]:
    """Line answers up with the names asked (by name, else by position).

    Only the structure's own ``fields`` are kept from each answer.
    """
    keep = set(fields) - {"name"}
    rows = (
        [a for a in answers if isinstance(a, dict)] if isinstance(answers, list) else []
    )
    keyed = {_key(a.get("name")): a for a in rows if _key(a.get("name"))}
    out: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        row = keyed.get(_key(name))
        if row is None and len(rows) == len(names):
            row = rows[index]
        out.append({k: v for k, v in (row or {}).items() if k in keep})
    return out


def _key(name: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(name or "").lower()))


async def _details(
    hass: Any,
    entity_id: str,
    prompt: str,
    names: list[str],
    *,
    clothing_only: bool,
    hook: SchemaErrorHook | None,
) -> list[dict[str, Any]]:
    """Pass 2 for one batch: all details, else split in two smaller calls.

    With clothing-only on, a batch is never accepted without is_apparel: if
    even the small is_apparel call fails, the email counts as a failed read.
    """
    try:
        raw = await _ask(hass, entity_id, prompt, "item", hook)
        return _by_name(names, raw.get("items"), _ITEM_FIELDS)
    except Exception as err:
        first: Exception = err
    try:
        apparel = _by_name(
            names,
            (await _ask(hass, entity_id, prompt, "item_apparel", hook)).get("items"),
            _APPAREL_FIELDS,
        )
    except Exception as err:
        if clothing_only:
            raise err from first
        apparel = [{} for _ in names]
    try:
        look = _by_name(
            names,
            (await _ask(hass, entity_id, prompt, "item_look", hook)).get("items"),
            _LOOK_FIELDS,
        )
    except Exception:
        look = [{} for _ in names]
    return [{**a, **b} for a, b in zip(apparel, look, strict=True)]


async def extract(
    hass: Any,
    *,
    entity_id: str,
    store_name: str,
    text: str,
    clothing_only: bool,
    on_schema_error: SchemaErrorHook | None = None,
) -> Order:
    """Ask AI Task for the order (pass 1), then item details in batches (pass 2).

    Every structure stays within ``const.AI_STRUCTURE_BUDGET``. When a call
    fails, the next smaller structure is tried; ``on_schema_error(name,
    fields)`` is called when a provider rejects a structure as too complex.
    """
    raw = await _first_pass(
        hass, entity_id, instructions(store_name, text), on_schema_error
    )
    items = raw.get("items")
    entries = (
        [e for e in items if isinstance(e, dict) and _clean(e.get("name"), 100)]
        if isinstance(items, list)
        else []
    )[:MAX_ITEMS]
    if raw.get("is_order") is not False and entries:
        for start in range(0, len(entries), BATCH_SIZE):
            batch = entries[start : start + BATCH_SIZE]
            names = [str(_clean(e.get("name"), 100)) for e in batch]
            details = await _details(
                hass,
                entity_id,
                detail_instructions(store_name, text, names),
                names,
                clothing_only=clothing_only,
                hook=on_schema_error,
            )
            for entry, extra in zip(batch, details, strict=True):
                entry.update(
                    {k: v for k, v in extra.items() if k not in entry or not entry[k]}
                )
    raw["items"] = entries
    return validate(raw, text, clothing_only=clothing_only)


def classify_instructions(names: list[str]) -> str:
    """Prompt for re-classifying stored product names (no email text)."""
    listed = "\n".join(f"- {name}" for name in names)
    return "\n".join(
        [
            "For each product name below, say whether it is clothing.",
            "is_apparel is true only for clothing, shoes and wearable accessories.",
            "is_health is true for medication, supplements, medical or health",
            "products. Copy each name exactly as listed. Leave quantity, unit_price",
            "and line_total empty.",
            "Products:",
            listed,
        ]
    )


async def classify(
    hass: Any,
    *,
    entity_id: str,
    names: list[str],
    on_schema_error: SchemaErrorHook | None = None,
) -> list[dict[str, Any]]:
    """is_apparel / is_health per name, in batches (v0.2.6 cleanup).

    Uses the small step-2 request (a) structure. A failed batch raises; the
    caller keeps those cards (deletion fails closed).
    """
    out: list[dict[str, Any]] = []
    for start in range(0, len(names), BATCH_SIZE):
        batch = names[start : start + BATCH_SIZE]
        raw = await _ask(
            hass,
            entity_id,
            classify_instructions(batch),
            "item_apparel",
            on_schema_error,
        )
        out.extend(_by_name(batch, raw.get("items"), ("is_apparel", "is_health")))
    return out
