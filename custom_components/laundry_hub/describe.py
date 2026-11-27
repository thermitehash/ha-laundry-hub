"""AI garment descriptions via the AI Task integration.

Builds a selector-based ``structure`` (so the AI Task provider validates the
shape), sends up to three photos as media-source attachments (cover first),
then re-validates every field against the fixed taxonomy: values outside the
vocabulary are mapped to the closest valid value or dropped, and fields whose
confidence is below ``CONFIDENCE_MIN`` are stored as null.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import selector
import probatio

from . import taxonomy as tx
from .const import DESCRIBE_MAX_ATTACHMENTS, DOMAIN

TASK_NAME = "Laundry Hub garment description"


def _select(options: tuple[str, ...] | list[str], *, multiple: bool = False) -> Any:
    return selector.selector(
        {
            "select": {
                "options": list(options),
                "multiple": multiple,
                "custom_value": False,
            }
        }
    )


def structure() -> probatio.Schema:
    """Structure passed to ai_task.generate_data (all fields optional)."""
    text = selector.selector({"text": {}})
    texts = selector.selector({"text": {"multiple": True}})
    scale = selector.selector(
        {"number": {"min": 1, "max": 5, "step": 1, "mode": "box"}}
    )
    fields: dict[Any, Any] = {
        "type": _select(tx.TYPES),
        "subtype": _select(tx.all_subtypes()),
        "colors": _select(tuple(tx.COLORS), multiple=True),
        "color_hex": texts,
        "pattern": _select(tx.PATTERNS),
        "sleeve_length": _select(tx.SLEEVE_LENGTHS),
        "neckline": _select(tx.NECKLINES),
        "length": _select(tx.LENGTHS),
        "fit": _select(tx.FITS),
        "rise": _select(tx.RISES),
        "material": _select(tx.MATERIALS, multiple=True),
        "formality": scale,
        "warmth": scale,
        "seasons": _select(tx.SEASONS, multiple=True),
        "occasions": _select(tx.OCCASIONS, multiple=True),
        "style_tags": texts,
        "summary": text,
        "confidence": selector.selector({"object": {}}),
    }
    descriptions = {
        "colors": f"Up to {tx.MAX_COLORS}, primary first.",
        "color_hex": "One hex colour guess per entry in colors, same order.",
        "material": f"Up to {tx.MAX_MATERIALS}.",
        "formality": "1 = very casual, 5 = formal.",
        "warmth": "1 = very light, 5 = very warm.",
        "rise": "Bottoms only; n/a otherwise.",
        "style_tags": f"Free text, at most {tx.MAX_STYLE_TAGS}.",
        "summary": f"One sentence, at most {tx.MAX_SUMMARY_WORDS} words.",
        "confidence": "Object mapping each field name to a confidence from 0 to 1.",
    }
    return probatio.Schema(
        {
            probatio.Optional(key, description=descriptions.get(key)): value
            for key, value in fields.items()
        },
        extra=probatio.REMOVE_EXTRA,
    )


def instructions(context: dict[str, Any]) -> str:
    """Prompt text: what to classify, using only the fixed vocabulary."""
    subtypes = "; ".join(f"{t}: {', '.join(v)}" for t, v in tx.SUBTYPES.items())
    lines = [
        "Classify the garment in the attached photos for a household wardrobe catalog.",
        "Use ONLY the allowed values for each field. If you are not sure, return null",
        "for that field rather than guessing. Give a confidence from 0 to 1 per field.",
        f"Subtypes by type: {subtypes}.",
        "Known details (may be empty):",
    ]
    for key in ("name", "brand", "product_title", "notes"):
        if context.get(key):
            lines.append(f"- {key.replace('_', ' ')}: {context[key]}")
    return "\n".join(lines)


def attachments(photos: list[str], garment_id: str) -> list[dict[str, str]]:
    """Media-source attachments: cover first, at most three, full-size JPEGs."""
    return [
        {
            "media_content_id": f"media-source://{DOMAIN}/{garment_id}/{name}",
            "media_content_type": "image/jpeg",
        }
        for name in photos[:DESCRIBE_MAX_ATTACHMENTS]
    ]


def normalize(raw: Any) -> tuple[dict[str, Any], dict[str, float]]:
    """Validate an AI result: returns (description fields, confidences).

    Raises TypeError if the result isn't an object at all (schema mismatch).
    """
    if not isinstance(raw, dict):
        raise TypeError("AI result is not an object")
    confidence_raw = raw.get("confidence")
    confidence: dict[str, float] = {}
    if isinstance(confidence_raw, dict):
        for key, value in confidence_raw.items():
            try:
                confidence[str(key)] = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                continue
    raw = dict(raw)
    names = raw.get("colors")
    hexes = raw.get("color_hex")
    if isinstance(names, list) and isinstance(hexes, list):
        raw["colors"] = [
            {"name": n, "hex": hexes[i] if i < len(hexes) else None}
            for i, n in enumerate(names)
        ]
    type_ = tx.coerce_field("type", raw.get("type"))
    if confidence.get("type", 1.0) < tx.CONFIDENCE_MIN:
        type_ = None
    out: dict[str, Any] = {}
    for field in tx.DESCRIPTION_FIELDS:
        value = tx.coerce_field(field, raw.get(field), type_=type_)
        if field in confidence and confidence[field] < tx.CONFIDENCE_MIN:
            value = None
        out[field] = value
    out["type"] = type_
    return out, {k: v for k, v in confidence.items() if k in tx.DESCRIPTION_FIELDS}


async def _generate_data(hass: Any, **kwargs: Any) -> Any:
    """Call ai_task.async_generate_data (imported lazily: optional dependency)."""
    from homeassistant.components.ai_task import (  # noqa: PLC0415
        async_generate_data,
    )

    return await async_generate_data(hass, **kwargs)


async def generate(
    hass: Any,
    *,
    entity_id: str,
    garment_id: str,
    photos: list[str],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, float]]:
    """Call AI Task and return validated (fields, confidences)."""
    result = await _generate_data(
        hass,
        task_name=TASK_NAME,
        entity_id=entity_id,
        instructions=instructions(context),
        structure=structure(),
        attachments=attachments(photos, garment_id) or None,
    )
    return normalize(result.data)
