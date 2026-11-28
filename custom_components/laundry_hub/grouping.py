"""Group purchase emails about the same item into one card (v0.2.6).

Pure functions only; ``purchases.py`` applies them.

* ``norm_ref`` / ``refs_equal``: order numbers compared loosely ("#2089393"
  equals "2089393", "Z1751170" equals "1751170", "8176491-1" equals
  "8176491"). Only ever compared within one store.
* ``item_key``: an item name without a " - colour / size / fit" suffix and
  without words already in the colour, size or cut fields, lower case,
  punctuation ignored.
* ``email_kind``: confirmation, shipping or delivery, from the subject.
* ``same_purchase``: whether an email's item is the same units as a
  purchase already recorded (confirmation → shipping → delivery of one
  order), as opposed to a new purchase of the same item.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any

SAME_PURCHASE_DAYS = 14

_SHIPMENT_SUFFIX = re.compile(r"-\d{1,2}$")
_REF_JUNK = re.compile(r"[\s#\-]+")
_LEADING_LETTERS = re.compile(r"^[A-Z]+")
_SUFFIX = re.compile(r"\s+[-–]\s+[^-–]*/[^-–]*$")
_TOKEN = re.compile(r"[a-z0-9]+")

_KINDS = (
    ("delivery", ("delivered", "has arrived", "was delivered")),
    (
        "shipping",
        (
            "shipped",
            "shipping",
            "on its way",
            "on the way",
            "out for delivery",
            "shipment",
            "tracking",
        ),
    ),
    (
        "confirmation",
        (
            "confirm",
            "thank you for your order",
            "thanks for your order",
            "received",
            "receipt",
            "order placed",
            "we got your order",
            "order #",
            "your order",
        ),
    ),
)


def _forms(ref: Any) -> list[str]:
    """Normalized forms of a reference: as written, and without a shipment suffix."""
    if not isinstance(ref, str | int) or isinstance(ref, bool):
        return []
    text = str(ref).strip().upper()
    forms = [_REF_JUNK.sub("", text)]
    base = _SHIPMENT_SUFFIX.sub("", text)
    if base != text and len(_REF_JUNK.sub("", base)) >= 5:
        forms.append(_REF_JUNK.sub("", base))  # "8176491-1" → "8176491"
    return [f for f in forms if f]


def norm_ref(ref: Any) -> str | None:
    """Upper case, without spaces, "-", "#" or a trailing shipment suffix."""
    forms = _forms(ref)
    return forms[-1] if forms else None


def _core(ref: str) -> str:
    """The reference without a leading-letter prefix ("Z1751170" → "1751170")."""
    core = _LEADING_LETTERS.sub("", ref)
    return core if len(core) >= 4 else ref


def refs_equal(a: Any, b: Any) -> bool:
    """Two order references name the same order (both must be present)."""
    left = {f for form in _forms(a) for f in (form, _core(form))}
    right = {f for form in _forms(b) for f in (form, _core(form))}
    return bool(left & right)


def tokens(text: Any) -> list[str]:
    """Lower-case words and numbers."""
    return _TOKEN.findall(str(text or "").lower())


def item_key(name: Any, color: Any = None, size: Any = None, cut: Any = None) -> str:
    """A comparable item name (see the module docstring)."""
    text = _SUFFIX.sub("", str(name or ""))
    drop = set(tokens(color)) | set(tokens(size)) | set(tokens(cut))
    words = [w for w in tokens(text) if w not in drop]
    return " ".join(words) or " ".join(tokens(text))


def email_kind(subject: str | None) -> str:
    """``confirmation``, ``shipping``, ``delivery`` or ``other`` (from the subject)."""
    text = (subject or "").lower()
    for kind, words in _KINDS:
        if any(word in text for word in words):
            return kind
    return "other"


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def days_apart(a: Any, b: Any) -> int | None:
    """Whole days between two ISO dates, or None if either is missing."""
    left, right = _day(a), _day(b)
    if left is None or right is None:
        return None
    return abs((left - right).days)


def _same_text(a: Any, b: Any) -> bool:
    return " ".join(tokens(a)) == " ".join(tokens(b))


def details_agree(card: dict[str, Any], item: dict[str, Any]) -> bool:
    """Colour, size, cut and price agree wherever both sides have a value."""
    for key in ("color", "size", "cut"):
        if card.get(key) and item.get(key) and not _same_text(card[key], item[key]):
            return False
    left, right = card.get("unit_price"), item.get("unit_price")
    if left is not None and right is not None:
        try:
            return abs(float(left) - float(right)) < 0.01
        except (TypeError, ValueError):
            return False
    return True


def same_item(card: dict[str, Any], item: dict[str, Any]) -> bool:
    """The card and the email item are the same product (same store assumed)."""
    left = item_key(
        card.get("name"), card.get("color"), card.get("size"), card.get("cut")
    )
    right = item_key(
        item.get("name"), item.get("color"), item.get("size"), item.get("cut")
    )
    return bool(left) and left == right


def same_purchase(
    purchase: dict[str, Any],
    card: dict[str, Any],
    item: dict[str, Any],
    *,
    order_ref: Any,
    order_date: Any,
    kind: str,
) -> bool:
    """The email's item is the same units as this recorded purchase.

    * The same normalized order reference: the same purchase.
    * Different references: a new purchase.
    * A reference missing on either side: the same purchase only within
      ``SAME_PURCHASE_DAYS`` days with agreeing colour, size, cut and price,
      and not a second order confirmation (or one after a later email).
    """
    theirs, ours = norm_ref(purchase.get("order_ref")), norm_ref(order_ref)
    if theirs and ours:
        return refs_equal(purchase.get("order_ref"), order_ref)
    kinds = purchase.get("kinds") or []
    if kind == "confirmation" and kinds:
        # A confirmation is the first email of an order: one that arrives
        # after another confirmation, or after a later email about the same
        # item, is a new purchase.
        mine, recorded = _day(order_date), _day(purchase.get("order_date"))
        after = mine is not None and recorded is not None and mine > recorded
        if "confirmation" in kinds or after:
            return False
    apart = days_apart(purchase.get("order_date"), order_date)
    if apart is None or apart > SAME_PURCHASE_DAYS:
        return False
    return details_agree(card, item)


SIZE_LINES = (
    "baby", "infant", "toddler", "kids", "kid", "girls", "boys", "youth",
    "junior", "juniors", "big", "little", "womens", "mens", "tall", "petite",
    "plus", "maternity",
)  # fmt: skip
_SIZE_LINE_ALIASES = {"kid": "kids", "junior": "juniors", "infant": "baby"}


def similarity_keys(card: dict[str, Any]) -> set[str]:
    """Keys shared by similar items, for assignment suggestions.

    ``line:<word>`` for a size line ("Kids", "Toddler", "Girls'") in the
    name or size, and ``product:<store>:<first two words>`` for a product
    line.
    """
    text = f"{card.get('name') or ''} {card.get('size') or ''}".lower()
    words = set(re.findall(r"[a-z]+", text.replace("'", "")))
    keys = {
        f"line:{_SIZE_LINE_ALIASES.get(w, w)}"
        for w in SIZE_LINES
        if w in words and w not in ("womens", "mens", "big", "little")
    }
    name = item_key(
        card.get("name"), card.get("color"), card.get("size"), card.get("cut")
    )
    head = [w for w in name.split() if not w.isdigit()][:2]
    if len(head) == 2:
        keys.add(f"product:{card.get('store_key') or ''}:{' '.join(head)}")
    return keys
