"""Fixed wardrobe vocabulary (v0.2).

Every enum used for filtering lives here. The AI must pick from these lists or
return null; anything else is mapped to the closest valid value or dropped.
Changing a list in a later version is safe: stored values are re-validated on
load (``remap_description``) and anything no longer valid is mapped or nulled.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
import re
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

TAXONOMY_VERSION: Final = 1

TYPES: Final = (
    "top", "bottom", "dress", "jumpsuit", "outerwear", "activewear", "sleepwear",
    "loungewear", "swimwear", "underwear", "socks", "shoes", "accessory", "other",
)  # fmt: skip

SUBTYPES: Final[dict[str, tuple[str, ...]]] = {
    "top": (
        "t-shirt", "tank", "blouse", "button-down", "polo", "sweater", "cardigan",
        "hoodie", "sweatshirt", "turtleneck", "camisole", "bodysuit",
    ),
    "bottom": ("jeans", "trousers", "chinos", "leggings", "joggers", "shorts", "skirt"),
    "dress": ("shift", "wrap", "sheath", "a-line", "maxi dress", "slip", "shirt dress", "sweater dress", "sundress"),
    "jumpsuit": ("jumpsuit", "romper", "overalls", "boilersuit"),
    "outerwear": ("jacket", "coat", "blazer", "vest", "rain shell", "fleece"),
    "activewear": ("sports bra", "athletic top", "athletic shorts", "athletic leggings", "track jacket", "track pants"),
    "sleepwear": ("pajama top", "pajama bottoms", "pajama set", "nightgown", "robe"),
    "loungewear": ("lounge top", "lounge pants", "lounge set"),
    "swimwear": ("one-piece", "bikini top", "bikini bottom", "swim trunks", "rash guard", "cover-up"),
    "underwear": ("bra", "briefs", "boxers", "boxer briefs", "undershirt", "slip", "shapewear"),
    "socks": ("ankle socks", "crew socks", "knee socks", "tights", "no-show socks"),
    "shoes": ("sneakers", "boots", "flats", "heels", "sandals", "loafers", "slippers"),
    "accessory": ("hat", "scarf", "gloves", "belt", "tie", "bag", "other accessory"),
    "other": ("other",),
}  # fmt: skip

# Named palette (name, swatch hex). The hex is a display default only.
COLORS: Final[dict[str, str]] = {
    "black": "#1C1B19", "white": "#FFFFFF", "cream": "#F3EBD7", "grey": "#8C8C8C",
    "navy": "#1E2A5A", "blue": "#2F6BD8", "light blue": "#9CC7EA", "green": "#2E8B57",
    "olive": "#6B6B2F", "red": "#D32F2F", "burgundy": "#7B1E2E", "pink": "#F48FB1",
    "purple": "#7E57C2", "yellow": "#F7D046", "orange": "#F28C28", "brown": "#6D4C41",
    "tan": "#C9A27E", "beige": "#E3D3B8", "gold": "#C9A227", "silver": "#BFC5CC",
    "multicolour": "#9E9E9E",
}  # fmt: skip

PATTERNS: Final = (
    "solid", "striped", "plaid", "checked", "floral", "polka dot", "graphic",
    "animal", "camo", "geometric", "other",
)  # fmt: skip
SLEEVE_LENGTHS: Final = (
    "sleeveless",
    "cap",
    "short",
    "elbow",
    "three-quarter",
    "long",
    "n/a",
)
NECKLINES: Final = (
    "crew", "v-neck", "scoop", "collared", "mock", "turtleneck", "boat", "square",
    "cowl", "hooded", "off-shoulder", "n/a",
)  # fmt: skip
LENGTHS: Final = (
    "cropped", "waist", "hip", "tunic", "mini", "knee", "midi", "maxi", "ankle", "full", "n/a",
)  # fmt: skip
FITS: Final = (
    "slim", "regular", "relaxed", "oversized", "fitted", "straight", "skinny",
    "wide-leg", "bootcut", "tapered", "n/a",
)  # fmt: skip
RISES: Final = ("low", "mid", "high", "n/a")
MATERIALS: Final = (
    "cotton", "denim", "linen", "wool", "cashmere", "silk", "satin", "polyester",
    "nylon", "rayon", "viscose", "spandex blend", "fleece", "knit", "leather",
    "faux leather", "suede", "corduroy", "velvet", "chiffon", "unknown",
)  # fmt: skip
SEASONS: Final = ("spring", "summer", "fall", "winter")
OCCASIONS: Final = ("work", "casual", "lounge", "workout", "outdoor", "formal", "sleep")

WEAR_FOR: Final = ("any", "work", "off_duty", "occasion", "athletic")
SIZE_SYSTEMS: Final = (
    "letter",
    "numeric",
    "waist-inseam",
    "shoe-us",
    "shoe-eu",
    "one-size",
    "other",
)
SIZE_CUTS: Final = ("regular", "petite", "tall", "plus", "maternity", "n/a")
LIFECYCLES: Final = ("active", "stored", "for_sale", "sold", "donated")
PURCHASE_SOURCES: Final = ("manual", "url", "email")

MAX_COLORS: Final = 3
MAX_MATERIALS: Final = 2
MAX_STYLE_TAGS: Final = 6
MAX_SUMMARY_WORDS: Final = 25
CONFIDENCE_MIN: Final = 0.5  # below this an AI value is stored as null
REVIEW_CONFIDENCE: Final = 0.7  # AI values below this show under "Needs review"

# Single-value enum fields and their vocabularies.
ENUM_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "type": TYPES,
    "pattern": PATTERNS,
    "sleeve_length": SLEEVE_LENGTHS,
    "neckline": NECKLINES,
    "length": LENGTHS,
    "fit": FITS,
    "rise": RISES,
}
# Multi-value enum fields: (vocabulary, max items).
LIST_FIELDS: Final[dict[str, tuple[tuple[str, ...], int]]] = {
    "colors": (tuple(COLORS), MAX_COLORS),
    "material": (MATERIALS, MAX_MATERIALS),
    "seasons": (SEASONS, len(SEASONS)),
    "occasions": (OCCASIONS, len(OCCASIONS)),
}
SCALE_FIELDS: Final = ("formality", "warmth")  # integers 1-5
TEXT_FIELDS: Final = ("summary",)
DESCRIPTION_FIELDS: Final = (
    "type", "subtype", "colors", "pattern", "sleeve_length", "neckline", "length",
    "fit", "rise", "material", "formality", "warmth", "seasons", "occasions",
    "style_tags", "summary",
)  # fmt: skip
# Fields whose null value puts a garment under "Needs review".
REVIEW_FIELDS: Final = (
    "type", "subtype", "colors", "pattern", "material", "formality", "warmth", "seasons",
)  # fmt: skip

_SYNONYMS: Final[dict[str, str]] = {
    "gray": "grey", "charcoal": "grey", "multicolor": "multicolour", "multi": "multicolour",
    "off-white": "cream", "ivory": "cream", "khaki": "tan", "camel": "tan", "maroon": "burgundy",
    "wine": "burgundy", "teal": "green", "sky blue": "light blue", "baby blue": "light blue",
    "violet": "purple", "lavender": "purple", "mustard": "yellow", "nude": "beige",
    "tshirt": "t-shirt", "tee": "t-shirt", "t shirt": "t-shirt", "button down": "button-down",
    "button-up": "button-down", "shirt": "button-down", "pants": "trousers", "slacks": "trousers",
    "sweatpants": "joggers", "trainers": "sneakers", "plaid/check": "plaid", "check": "checked",
    "stripe": "striped", "stripes": "striped", "dots": "polka dot", "polka-dot": "polka dot",
    "autumn": "fall", "three quarter": "three-quarter", "3/4": "three-quarter",
    "wide leg": "wide-leg", "boot cut": "bootcut", "elastane": "spandex blend",
    "spandex": "spandex blend", "lycra": "spandex blend", "faux-leather": "faux leather",
    "vegan leather": "faux leather", "acrylic": "knit", "merino": "wool", "jersey": "knit",
    "athletic": "workout", "gym": "workout", "office": "work", "business": "work",
    "na": "n/a", "none": "n/a", "not applicable": "n/a",
}  # fmt: skip


def _key(value: str) -> str:
    return re.sub(r"[\s_]+", " ", value.strip().lower())


def coerce(value: Any, vocabulary: Iterable[str]) -> str | None:
    """Return the vocabulary entry matching ``value`` (closest form), else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    vocab = tuple(vocabulary)
    key = _key(value)
    by_key = {_key(v): v for v in vocab}
    if key in by_key:
        return by_key[key]
    alt = key.replace("-", " ")
    for v in vocab:
        if _key(v).replace("-", " ") == alt:
            return v
    synonym = _SYNONYMS.get(key) or _SYNONYMS.get(alt)
    if synonym in vocab:
        return synonym
    if key.endswith("s") and key[:-1] in by_key:  # plural given, singular listed
        return by_key[key[:-1]]
    if f"{key}s" in by_key:  # singular given, plural listed
        return by_key[f"{key}s"]
    return None


def extract(text: Any, field: str) -> list[str] | None:
    """Pull vocabulary terms out of free text ("95% Cotton, 5% Elastane").

    Used for product-page attributes; terms are returned in order of appearance,
    synonyms mapped, duplicates and anything not in the vocabulary dropped.
    """
    if field not in LIST_FIELDS or not isinstance(text, str):
        return None
    vocab, cap = LIST_FIELDS[field]
    lowered = f" {_key(text).replace('-', ' ')} "
    found: list[tuple[int, str]] = []
    candidates = {_key(v).replace("-", " "): v for v in vocab}
    candidates.update(
        {k.replace("-", " "): v for k, v in _SYNONYMS.items() if v in vocab}
    )
    for phrase, value in candidates.items():
        match = re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered)
        if match:
            found.append((match.start(), value))
    out: list[str] = []
    for _pos, value in sorted(found):
        if value not in out:
            out.append(value)
    return out[:cap] or None


def all_subtypes() -> tuple[str, ...]:
    """Every subtype across types (deduplicated, stable order)."""
    seen: dict[str, None] = {}
    for values in SUBTYPES.values():
        for v in values:
            seen.setdefault(v, None)
    return tuple(seen)


def coerce_subtype(value: Any, type_: str | None) -> str | None:
    """Subtype valid for ``type_`` (or for any type when type is unknown)."""
    if type_ in SUBTYPES:
        return coerce(value, SUBTYPES[type_])
    return coerce(value, all_subtypes())


def _scale(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 5 else None


def _hex(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", value.strip()):
        return "#" + value.strip().lstrip("#").upper()
    return None


def coerce_field(field: str, value: Any, *, type_: str | None = None) -> Any:
    """Validate one description field; invalid values become None (or [])."""
    if field in ENUM_FIELDS:
        coerced = coerce(value, ENUM_FIELDS[field])
        if field == "rise" and type_ is not None and type_ != "bottom" and coerced:
            return "n/a"
        return coerced
    if field == "subtype":
        return coerce_subtype(value, type_)
    if field == "colors":
        return _coerce_colors(value)
    if field in LIST_FIELDS:
        vocab, cap = LIST_FIELDS[field]
        items = value if isinstance(value, list) else [value] if value else []
        out: list[str] = []
        for item in items:
            c = coerce(item, vocab)
            if c and c not in out:
                out.append(c)
        return out[:cap] or None
    if field in SCALE_FIELDS:
        return _scale(value)
    if field == "style_tags":
        items = value if isinstance(value, list) else []
        tags = [str(t).strip()[:40] for t in items if str(t).strip()]
        return tags[:MAX_STYLE_TAGS] or None
    if field == "summary":
        if not isinstance(value, str) or not value.strip():
            return None
        words = value.strip().split()
        return " ".join(words[:MAX_SUMMARY_WORDS])
    raise KeyError(field)


def _coerce_colors(value: Any) -> list[dict[str, str]] | None:
    """Colors as [{"name", "hex"}]; accepts names or {name, hex} dicts."""
    items = value if isinstance(value, list) else [value] if value else []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            name, hex_ = coerce(item.get("name"), COLORS), _hex(item.get("hex"))
        else:
            name, hex_ = coerce(item, COLORS), None
        if name and all(c["name"] != name for c in out):
            out.append({"name": name, "hex": hex_ or COLORS[name]})
    return out[:MAX_COLORS] or None


def remap_description(description: dict[str, Any] | None) -> dict[str, Any] | None:
    """Re-validate stored values against the current taxonomy (logs changes)."""
    if not description:
        return None
    type_ = coerce(description.get("type"), TYPES)
    out: dict[str, Any] = {}
    for field in DESCRIPTION_FIELDS:
        old = description.get(field)
        new = coerce_field(field, old, type_=type_)
        if old not in (None, []) and new != old and _changed(field, old, new):
            _LOGGER.info("Taxonomy remap %s: %r -> %r", field, old, new)
        out[field] = new
    return out


def _changed(field: str, old: Any, new: Any) -> bool:
    if field == "colors" and isinstance(old, list) and new:
        return [c.get("name") if isinstance(c, dict) else c for c in old] != [
            c["name"] for c in new
        ]
    return True


def vocabulary() -> dict[str, Any]:
    """Everything the panel needs to render filters and editors."""
    return {
        "version": TAXONOMY_VERSION,
        "types": list(TYPES),
        "subtypes": {k: list(v) for k, v in SUBTYPES.items()},
        "colors": [{"name": k, "hex": v} for k, v in COLORS.items()],
        "pattern": list(PATTERNS),
        "sleeve_length": list(SLEEVE_LENGTHS),
        "neckline": list(NECKLINES),
        "length": list(LENGTHS),
        "fit": list(FITS),
        "rise": list(RISES),
        "material": list(MATERIALS),
        "seasons": list(SEASONS),
        "occasions": list(OCCASIONS),
        "wear_for": list(WEAR_FOR),
        "size_systems": list(SIZE_SYSTEMS),
        "size_cuts": list(SIZE_CUTS),
        "lifecycles": list(LIFECYCLES),
        "description_fields": list(DESCRIPTION_FIELDS),
        "review_fields": list(REVIEW_FIELDS),
        "review_confidence": REVIEW_CONFIDENCE,
    }
