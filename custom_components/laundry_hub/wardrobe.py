"""Wardrobe catalog (v0.2): purchase details, photos, URL import, AI descriptions.

Everything here is opt-in. With ``wardrobe_enabled`` off, every wardrobe
operation is refused with ``wardrobe_disabled`` and no AI call or network fetch
ever happens. Photos keep working as in v0.1 (one cover photo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.ulid import ulid_now

from . import describe, taxonomy as tx
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_WARDROBE_ENABLED,
    IMPORT_MAX_IMAGES,
    PHOTO_MAX_PER_GARMENT,
)
from .importer import ImportFailed, check_url, fetch_image, fetch_product
from .photos import (
    PhotoError,
    ProcessedImage,
    copy_photo,
    delete_photo,
    ensure_thumbnail,
    photo_dir,
    process_image,
    write_processed,
)

if TYPE_CHECKING:
    from .hub import LaundryHub
    from .models import Garment

_LOGGER = logging.getLogger(__name__)

PURCHASE_KEYS = (
    "store", "purchased_on", "price", "currency", "product_url", "order_ref", "source",
)  # fmt: skip
SIZE_KEYS = ("label", "system", "cut", "brand_size_note")
PENDING_TTL = 30 * 60  # seconds an import preview's images are kept
PENDING_MAX = 24
BULK_FIELDS = (
    "wash_bin", "dry_type", "owner", "wear_for", "uniform", *tx.DESCRIPTION_FIELDS,
)  # fmt: skip


def _err(key: str, message: str, **placeholders: Any) -> HomeAssistantError:
    from .hub import validation_error  # noqa: PLC0415 - avoid import cycle

    return validation_error(key, message, **placeholders)


def _text(value: Any, limit: int, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str | int | float):
        raise _err("invalid_field_value", f"{label} must be text", field=label)
    text = " ".join(str(value).split())
    if len(text) > limit:
        raise _err(
            "invalid_field_value",
            f"{label} is longer than {limit} characters",
            field=label,
        )
    return text or None


@dataclass(slots=True)
class PendingImport:
    """Images downloaded for an import preview, until applied or expired."""

    images: list[ProcessedImage]
    proposal: dict[str, Any]
    created: float = field(default_factory=time.monotonic)


class Wardrobe:
    """Wardrobe operations bound to a hub."""

    def __init__(self, hub: LaundryHub) -> None:
        """Initialize."""
        self.hub = hub
        self.hass = hub.hass
        self._pending: dict[str, PendingImport] = {}

    # --- settings --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the wardrobe catalog is switched on."""
        return bool(self.hub.options.get(CONF_WARDROBE_ENABLED, False))

    @property
    def ai_entity(self) -> str | None:
        """Configured AI Task entity (only meaningful when enabled)."""
        return self.hub.options.get(CONF_AI_TASK_ENTITY) or None

    def require(self) -> None:
        """Raise unless the wardrobe is on."""
        if not self.enabled:
            raise _err(
                "wardrobe_disabled",
                "Turn on Wardrobe in Laundry Hub's settings to use this",
            )

    # --- validation ------------------------------------------------------

    def clean_size(self, value: Any) -> dict[str, Any] | None:
        """Validate a structured size (or None to clear)."""
        if value in (None, {}, ""):
            return None
        if not isinstance(value, dict):
            raise _err("invalid_field_value", "Size must be an object", field="size")
        label = _text(value.get("label"), 20, "size label")
        if label is None:
            return None
        system = tx.coerce(value.get("system"), tx.SIZE_SYSTEMS) or "other"
        cut = tx.coerce(value.get("cut"), tx.SIZE_CUTS) or "n/a"
        note = _text(value.get("brand_size_note"), 100, "size note")
        return {"label": label, "system": system, "cut": cut, "brand_size_note": note}

    def clean_purchase(
        self, value: Any, *, source: str = "manual"
    ) -> dict[str, Any] | None:
        """Validate purchase details (or None to clear)."""
        if value in (None, {}, ""):
            return None
        if not isinstance(value, dict):
            raise _err(
                "invalid_field_value", "Purchase must be an object", field="purchase"
            )
        out: dict[str, Any] = dict.fromkeys(PURCHASE_KEYS)
        out["store"] = _text(value.get("store"), 100, "store")
        out["order_ref"] = _text(value.get("order_ref"), 100, "order reference")
        raw_date = value.get("purchased_on")
        if raw_date:
            try:
                out["purchased_on"] = date.fromisoformat(str(raw_date)[:10]).isoformat()
            except ValueError as err:
                raise _err(
                    "invalid_field_value",
                    "Purchase date must be YYYY-MM-DD",
                    field="date",
                ) from err
        price = value.get("price")
        if price not in (None, ""):
            try:
                amount = round(float(price), 2)
            except (TypeError, ValueError) as err:
                raise _err(
                    "invalid_field_value", "Price must be a number", field="price"
                ) from err
            if amount < 0:
                raise _err(
                    "invalid_field_value", "Price can't be negative", field="price"
                )
            out["price"] = amount
        out["currency"] = (
            _text(value.get("currency"), 3, "currency") or self.hass.config.currency
        )
        url = _text(value.get("product_url"), 2000, "product link")
        if url and urlsplit(url).scheme not in ("http", "https"):
            raise _err(
                "invalid_field_value",
                "Product link must start with http(s)://",
                field="url",
            )
        out["product_url"] = url
        out["source"] = tx.coerce(value.get("source"), tx.PURCHASE_SOURCES) or source
        if all(
            out[k] is None
            for k in ("store", "purchased_on", "price", "product_url", "order_ref")
        ):
            return None
        return out

    # --- details ---------------------------------------------------------

    def update_details(self, garment: Garment, changes: dict[str, Any]) -> None:
        """Brand, size, purchase, wear context (validated before any change)."""
        self.require()
        updates: dict[str, Any] = {}
        if "brand" in changes:
            updates["brand"] = _text(changes["brand"], 60, "brand")
        if "size" in changes:
            updates["size"] = self.clean_size(changes["size"])
        if "purchase" in changes:
            updates["purchase"] = self.clean_purchase(changes["purchase"])
        if "wear_for" in changes:
            wear_for = tx.coerce(changes["wear_for"], tx.WEAR_FOR)
            if wear_for is None:
                raise _err(
                    "invalid_field_value", "Unknown wear setting", field="wear_for"
                )
            updates["wear_for"] = wear_for
        if "uniform" in changes:
            updates["uniform"] = bool(changes["uniform"])
        for key, value in updates.items():
            setattr(garment, key, value)
        if "brand" in updates:
            garment.description_provenance["brand"] = "user"
        if "size" in updates:
            garment.description_provenance["size"] = "user"
        if garment.uniform:
            garment.wear_for = "work"  # a uniform is always a work garment

    def set_description(self, garment: Garment, fields: dict[str, Any]) -> None:
        """User edits to description fields: validated, then locked."""
        self.require()
        current = dict(garment.description or dict.fromkeys(tx.DESCRIPTION_FIELDS))
        type_ = fields.get("type", current.get("type"))
        cleaned: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in tx.DESCRIPTION_FIELDS:
                raise _err("invalid_field", f"Unknown field: {key}", field=key)
            coerced = tx.coerce_field(key, value, type_=tx.coerce(type_, tx.TYPES))
            if value not in (None, "", []) and coerced in (None, []):
                raise _err(
                    "invalid_field_value", f"{value!r} isn't a valid {key}", field=key
                )
            cleaned[key] = coerced
        if "type" in cleaned and "subtype" not in cleaned and current.get("subtype"):
            # A subtype that doesn't fit the new type is cleared.
            cleaned["subtype"] = tx.coerce_subtype(current["subtype"], cleaned["type"])
        current.update(cleaned)
        garment.description = current
        for key in cleaned:
            garment.description_provenance[key] = "user"
            garment.description_confidence.pop(key, None)

    def reset_field(self, garment: Garment, key: str) -> None:
        """Unlock a field and restore the last AI value."""
        self.require()
        if key not in tx.DESCRIPTION_FIELDS:
            raise _err("invalid_field", f"Unknown field: {key}", field=key)
        current = dict(garment.description or dict.fromkeys(tx.DESCRIPTION_FIELDS))
        current[key] = garment.description_ai.get(key)
        garment.description = current
        if key in garment.description_ai:
            garment.description_provenance[key] = "ai"
        else:
            garment.description_provenance.pop(key, None)

    def bulk_set(self, garments: list[Garment], key: str, value: Any) -> int:
        """Set one field on many garments ("Set field…")."""
        if key not in BULK_FIELDS:
            raise _err("invalid_field", f"Can't bulk-set {key}", field=key)
        if key in ("wash_bin", "dry_type"):
            return self.hub.reassign(key, str(value), [g.id for g in garments])
        if key == "owner":
            owner = self.hub.validate_owner(value)
            for g in garments:
                g.owner = owner
            return len(garments)
        self.require()
        if key in ("wear_for", "uniform"):
            for g in garments:
                self.update_details(g, {key: value})
            return len(garments)
        for g in garments:
            self.set_description(g, {key: value})
        return len(garments)

    def needs_review(self, garment: Garment) -> bool:
        """Any review field unset, or an AI value with low confidence."""
        desc = garment.description or {}
        for key in tx.REVIEW_FIELDS:
            if desc.get(key) in (None, []):
                return True
        return any(
            garment.description_provenance.get(k) == "ai" and v < tx.REVIEW_CONFIDENCE
            for k, v in garment.description_confidence.items()
        )

    # --- photos ----------------------------------------------------------

    def photo_limit(self) -> int:
        """Photos allowed per garment (v0.1 parity: one cover when off)."""
        return PHOTO_MAX_PER_GARMENT if self.enabled else 1

    async def async_add_photo(self, garment: Garment, raw: bytes) -> str:
        """Resize, strip and store a photo; returns its filename."""
        try:
            processed = await self.hass.async_add_executor_job(process_image, raw)
        except PhotoError as err:
            raise _err(err.key, str(err)) from err
        return await self._async_store(garment, processed)

    async def _async_store(self, garment: Garment, processed: ProcessedImage) -> str:
        limit = self.photo_limit()
        replace: str | None = None
        if len(garment.photos) >= limit:
            if limit > 1:
                raise _err(
                    "photo_limit",
                    f"A garment can have at most {limit} photos",
                    limit=limit,
                )
            replace = garment.photos[0]  # wardrobe off: the one photo is replaced
        filename = f"{garment.id}-{ulid_now().lower()}.jpg"
        directory = photo_dir(self.hass)
        try:
            await self.hass.async_add_executor_job(
                write_processed, directory, filename, processed
            )
            if replace:
                await self.hass.async_add_executor_job(delete_photo, directory, replace)
        except OSError as err:
            raise HomeAssistantError(f"Could not store photo: {err}") from err
        first = not garment.photos
        garment.photos = [p for p in garment.photos if p != replace] + [filename]
        if replace:
            garment.photos = [filename, *garment.photos[:-1]]
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        if first and self.enabled and self.ai_entity:
            self.hub.entry.async_create_background_task(
                self.hass,
                self._async_auto_describe(garment.id),
                f"laundry_hub describe {garment.id}",
            )
        return filename

    async def async_copy_photo(self, garment: Garment, source: str) -> str | None:
        """Copy a stored photo (e.g. a purchase card's) onto a garment.

        The source file is left alone, so deleting either one later never
        affects the other. Returns the new filename, or None if it is gone.
        """
        if len(garment.photos) >= self.photo_limit():
            return None
        filename = f"{garment.id}-{ulid_now().lower()}.jpg"
        directory = photo_dir(self.hass)
        if not await self.hass.async_add_executor_job(
            copy_photo, directory, source, filename
        ):
            return None
        first = not garment.photos
        garment.photos = [*garment.photos, filename]
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        if first and self.enabled and self.ai_entity:
            self.hub.entry.async_create_background_task(
                self.hass,
                self._async_auto_describe(garment.id),
                f"laundry_hub describe {garment.id}",
            )
        return filename

    async def _async_auto_describe(self, garment_id: str) -> None:
        garment = self.hub.catalog.garments.get(garment_id)
        if garment is None:
            return
        try:
            await self.async_describe(garment)
        except HomeAssistantError as err:
            _LOGGER.warning("Automatic description of %s failed: %s", garment.name, err)

    async def async_delete_photo(self, garment: Garment, filename: str) -> None:
        """Delete one photo (and its thumbnail)."""
        if filename not in garment.photos:
            raise _err("unknown_photo", "That photo isn't on this garment")
        try:
            await self.hass.async_add_executor_job(
                delete_photo, photo_dir(self.hass), filename
            )
        except OSError as err:
            raise HomeAssistantError(f"Could not delete photo: {err}") from err
        garment.photos = [p for p in garment.photos if p != filename]
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()

    async def async_delete_all(self, garment: Garment) -> None:
        """Delete every photo of a garment (garment removal)."""
        directory = photo_dir(self.hass)
        for filename in garment.photos:
            await self.hass.async_add_executor_job(delete_photo, directory, filename)

    def reorder_photos(self, garment: Garment, order: list[str]) -> None:
        """Set the photo order; the first becomes the cover."""
        if sorted(order) != sorted(garment.photos):
            raise _err(
                "invalid_field_value",
                "Photo order must list every photo once",
                field="photos",
            )
        garment.photos = list(order)
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()

    async def async_ensure_thumbnails(self) -> None:
        """Create thumbnails for photos migrated from v0.1 (background)."""
        directory = photo_dir(self.hass)
        for garment in list(self.hub.catalog.garments.values()):
            for filename in garment.photos:
                await self.hass.async_add_executor_job(
                    ensure_thumbnail, directory, filename
                )

    # --- URL import ------------------------------------------------------

    def _prune_pending(self) -> None:
        cutoff = time.monotonic() - PENDING_TTL
        for token in [t for t, p in self._pending.items() if p.created < cutoff]:
            del self._pending[token]
        while len(self._pending) > PENDING_MAX:
            del self._pending[next(iter(self._pending))]

    async def async_import_url(self, url: str) -> dict[str, Any]:
        """Fetch a product page; return a preview (nothing is saved yet)."""
        self.require()
        try:
            await check_url(self.hass, url)
            product = await fetch_product(self.hass, url)
        except ImportFailed as err:
            raise _err(err.key, err.reason) from err
        images: list[ProcessedImage] = []
        for image_url in product.images:
            if len(images) >= IMPORT_MAX_IMAGES:
                break
            try:
                raw = await fetch_image(self.hass, image_url)
                images.append(
                    await self.hass.async_add_executor_job(process_image, raw)
                )
            except (ImportFailed, PhotoError) as err:
                _LOGGER.debug("Skipped import image %s: %s", image_url, err)
        proposal: dict[str, Any] = {
            "name": product.name,
            "brand": product.brand,
            "size": {"label": product.size, "system": "other"}
            if product.size
            else None,
            "purchase": {
                "store": product.store,
                "price": product.price,
                "currency": (product.currency or self.hass.config.currency),
                "product_url": product.url or url,
                "source": "url",
            },
            "colors": tx.coerce_field("colors", tx.extract(product.color, "colors")),
            "material": tx.extract(product.material, "material"),
            "product_title": product.name,
            "image_count": len(images),
        }
        self._prune_pending()
        token = ulid_now()
        self._pending[token] = PendingImport(images, proposal)
        return {"token": token, **proposal}

    def pending_thumb(self, token: str, index: int) -> bytes | None:
        """Thumbnail bytes for an import preview image."""
        pending = self._pending.get(token)
        if pending is None or not 0 <= index < len(pending.images):
            return None
        return pending.images[index].thumb

    async def async_apply_import(
        self,
        garment: Garment,
        token: str,
        *,
        fields: list[str] | None = None,
        images: list[int] | None = None,
    ) -> None:
        """Save the chosen parts of an import preview onto a garment."""
        self.require()
        pending = self._pending.pop(token, None)
        if pending is None:
            raise _err(
                "import_expired", "That import has expired — import the link again"
            )
        proposal = pending.proposal
        chosen = set(fields if fields is not None else proposal)
        if "name" in chosen and proposal.get("name"):
            garment.name = str(proposal["name"])[:100]
        if "brand" in chosen and proposal.get("brand"):
            garment.brand = _text(proposal["brand"], 60, "brand")
            garment.description_provenance["brand"] = "import"
        if "size" in chosen and proposal.get("size"):
            garment.size = self.clean_size(proposal["size"])
            garment.description_provenance["size"] = "import"
        if "purchase" in chosen and proposal.get("purchase"):
            garment.purchase = self.clean_purchase(proposal["purchase"], source="url")
        desc = dict(garment.description or dict.fromkeys(tx.DESCRIPTION_FIELDS))
        for key in ("colors", "material"):
            if (
                key in chosen
                and proposal.get(key)
                and (garment.description_provenance.get(key) != "user")
            ):
                desc[key] = proposal[key]
                garment.description_provenance[key] = "import"
        garment.description = desc
        wanted = images if images is not None else list(range(len(pending.images)))
        for index in wanted:
            if 0 <= index < len(pending.images):
                if len(garment.photos) >= self.photo_limit():
                    break
                await self._async_store(garment, pending.images[index])
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()

    # --- AI description --------------------------------------------------

    async def async_describe(self, garment: Garment) -> dict[str, Any]:
        """Describe a garment with AI Task; never overwrites user/import fields."""
        self.require()
        entity = self.ai_entity
        if not entity:
            raise _err(
                "no_ai_entity", "Choose an AI Task entity in Laundry Hub's settings"
            )
        context = {
            "name": garment.name,
            "brand": garment.brand,
            "product_title": (garment.purchase or {}).get("product_url"),
            "notes": garment.notes,
        }
        try:
            fields, confidence = await describe.generate(
                self.hass,
                entity_id=entity,
                garment_id=garment.id,
                photos=list(garment.photos),
                context=context,
            )
        except (TypeError, ValueError) as err:
            _LOGGER.warning(
                "AI description of %s didn't match the schema: %s", garment.name, err
            )
            raise _err(
                "describe_failed",
                "The AI answer didn't fit the garment fields; try again",
            ) from err
        except HomeAssistantError as err:
            _LOGGER.warning("AI description of %s failed: %s", garment.name, err)
            raise _err("describe_failed", f"The AI provider failed: {err}") from err
        except Exception as err:
            _LOGGER.warning(
                "AI description of %s failed: %s", garment.name, type(err).__name__
            )
            raise _err(
                "describe_failed", "The AI provider failed; try again later"
            ) from err
        desc = dict(garment.description or dict.fromkeys(tx.DESCRIPTION_FIELDS))
        for key, value in fields.items():
            if garment.description_provenance.get(key) in ("user", "import"):
                continue
            desc[key] = value
            if value is None:
                garment.description_provenance.pop(key, None)
            else:
                garment.description_provenance[key] = "ai"
        garment.description = desc
        garment.description_ai = fields
        garment.description_confidence = confidence
        garment.description_model = entity
        garment.description_updated = dt_util.utcnow()
        self.hub.catalog.schedule_save()
        self.hub.notify_changed()
        return desc

    # --- views -----------------------------------------------------------

    def view(self, garment: Garment) -> dict[str, Any]:
        """Wardrobe part of a garment view (empty when off)."""
        if not self.enabled:
            return {}
        return {
            "brand": garment.brand,
            "size": garment.size,
            "purchase": garment.purchase,
            "description": garment.description,
            "description_provenance": garment.description_provenance,
            "description_confidence": garment.description_confidence,
            "description_ai": garment.description_ai,
            "description_model": garment.description_model,
            "description_updated": (
                garment.description_updated.isoformat()
                if garment.description_updated
                else None
            ),
            "wear_for": garment.wear_for,
            "uniform": garment.uniform,
            "needs_review": self.needs_review(garment),
        }
