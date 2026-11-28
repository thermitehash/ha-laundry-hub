"""Keep AI Task structures within provider limits (v0.2.5).

``complexity`` measures a structure the way the provider sees it: the
selector schema is turned into JSON schema with Home Assistant's own
serializer (what the Anthropic, OpenAI and Google integrations send), then
fields, optional fields, unions, object depth and enum values are counted.
The ceiling is ``const.AI_STRUCTURE_BUDGET``.

``is_schema_error`` recognises a provider rejecting the structure itself
("400 … Schema is too complex", or another invalid-schema 400), so callers can
fall back to a smaller structure instead of retrying the same one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.helpers import llm
import probatio

from .const import AI_STRUCTURE_BUDGET


@dataclass(frozen=True, slots=True)
class Complexity:
    """Counts for one structure."""

    properties: int = 0
    optional: int = 0
    unions: int = 0
    depth: int = 0
    enum_values: int = 0

    def over_budget(self) -> dict[str, tuple[int, int]]:
        """{measure: (value, limit)} for every measure above the budget."""
        return {
            key: (value, AI_STRUCTURE_BUDGET[key])
            for key, value in asdict(self).items()
            if value > AI_STRUCTURE_BUDGET[key]
        }


def json_schema(structure: probatio.Schema) -> dict[str, Any]:
    """The JSON schema Home Assistant sends for this structure."""
    return probatio.to_openapi(
        structure,
        custom_serializer=llm.selector_serializer,
        openapi_version="3.1.0",
    )


def complexity(structure: probatio.Schema) -> Complexity:
    """Measure a structure (see the module docstring)."""
    counts = {"properties": 0, "optional": 0, "unions": 0, "enum_values": 0}
    depth = _walk(json_schema(structure), counts, 0)
    return Complexity(depth=depth, **counts)


def _walk(node: Any, counts: dict[str, int], depth: int) -> int:
    """Count one schema node; returns the deepest object level below it."""
    if not isinstance(node, dict):
        return depth
    deepest = depth
    if "anyOf" in node or "oneOf" in node or isinstance(node.get("type"), list):
        counts["unions"] += 1
    if isinstance(node.get("enum"), list):
        counts["enum_values"] += len(node["enum"])
    if node.get("type") == "object":
        level = depth + 1
        deepest = level
        properties = node.get("properties") or {}
        required = set(node.get("required") or [])
        counts["properties"] += len(properties)
        counts["optional"] += sum(1 for key in properties if key not in required)
        for child in properties.values():
            deepest = max(deepest, _walk(child, counts, level))
    if isinstance(node.get("items"), dict):
        deepest = max(deepest, _walk(node["items"], counts, depth))
    for variant in node.get("anyOf") or node.get("oneOf") or []:
        deepest = max(deepest, _walk(variant, counts, depth))
    return deepest


_SCHEMA_WORDS = ("schema is too complex", "too complex for compilation")


def _texts(err: BaseException) -> list[str]:
    texts = [str(err), str(getattr(err, "message", "") or "")]
    placeholders = getattr(err, "translation_placeholders", None) or {}
    texts.extend(str(v) for v in placeholders.values())
    body = getattr(err, "body", None)
    if body:
        texts.append(str(body))
    return [t.lower() for t in texts if t]


def is_schema_error(err: BaseException | None) -> bool:
    """True if a provider rejected the structure itself (checks the cause chain)."""
    seen: set[int] = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        texts = _texts(err)
        if any(word in text for text in texts for word in _SCHEMA_WORDS):
            return True
        status = getattr(err, "status_code", None)
        bad_request = status == 400 or any(
            "400" in text and "invalid_request_error" in text for text in texts
        )
        if bad_request and any("schema" in text for text in texts):
            return True
        err = err.__cause__ or err.__context__
    return False
