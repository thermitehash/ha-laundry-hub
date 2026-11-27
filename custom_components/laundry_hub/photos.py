"""Garment photo storage under /config/laundry_hub/photos/ (size-limited)."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
import re

from homeassistant.core import HomeAssistant

from .const import PHOTO_DIR, PHOTO_MAX_BYTES, PHOTO_TYPES

_SAFE_NAME = re.compile(r"^[0-9A-Za-z_-]+\.(jpg|png|webp)$")


class PhotoError(ValueError):
    """Invalid photo upload."""

    def __init__(self, key: str, message: str) -> None:
        """Initialize."""
        super().__init__(message)
        self.key = key


def photo_dir(hass: HomeAssistant) -> Path:
    """Photo directory."""
    return Path(hass.config.path(PHOTO_DIR))


def photo_path(hass: HomeAssistant, filename: str) -> Path | None:
    """Resolve a stored photo filename safely (no path traversal)."""
    if not _SAFE_NAME.match(filename):
        return None
    return photo_dir(hass) / filename


def decode_photo(content_type: str, data_b64: str) -> tuple[bytes, str]:
    """Validate and decode an uploaded photo. Returns (bytes, extension)."""
    ext = PHOTO_TYPES.get(content_type)
    if ext is None:
        raise PhotoError("photo_type", f"Unsupported photo type {content_type}")
    # Reject oversized input before decoding (base64 is 4/3 of the payload).
    if len(data_b64) > (PHOTO_MAX_BYTES * 4) // 3 + 4:
        raise PhotoError("photo_too_large", "Photo exceeds the size limit")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as err:
        raise PhotoError("photo_invalid", "Photo is not valid base64") from err
    if not raw:
        raise PhotoError("photo_invalid", "Photo is empty")
    if len(raw) > PHOTO_MAX_BYTES:
        raise PhotoError("photo_too_large", "Photo exceeds the size limit")
    return raw, ext


def write_photo(directory: Path, filename: str, raw: bytes) -> None:
    """Write a photo atomically (executor only)."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{filename}.tmp"
    tmp.write_bytes(raw)
    tmp.replace(directory / filename)


def delete_photo(directory: Path, filename: str) -> None:
    """Delete a photo if present (executor only)."""
    if not _SAFE_NAME.match(filename):
        return
    (directory / filename).unlink(missing_ok=True)
