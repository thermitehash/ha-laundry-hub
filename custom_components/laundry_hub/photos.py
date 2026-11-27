"""Garment photos under /config/laundry_hub/photos/.

Every upload goes through one pipeline (``process_image``): decode with Pillow,
apply the EXIF orientation, strip all metadata, convert to RGB, then save a
1600 px long-edge JPEG (q85) and a 400 px thumbnail. All file I/O here is
synchronous and must run in the executor.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import io
from pathlib import Path
import re

from homeassistant.core import HomeAssistant
from PIL import Image, ImageOps, UnidentifiedImageError

from .const import (
    PHOTO_DIR,
    PHOTO_LONG_EDGE,
    PHOTO_MAX_BYTES,
    PHOTO_THUMB_EDGE,
    PHOTO_TYPES,
    PHOTO_UPLOAD_MAX_BYTES,
)

# <name>.jpg plus its <name>.thumb.jpg; legacy v0.1 photos may be png/webp.
_SAFE_NAME = re.compile(r"^[0-9A-Za-z_-]+(\.thumb)?\.(jpg|png|webp)$")
JPEG_QUALITY = 85
THUMB_QUALITY = 80


class PhotoError(ValueError):
    """Invalid photo upload."""

    def __init__(self, key: str, message: str) -> None:
        """Initialize."""
        super().__init__(message)
        self.key = key


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """A resized photo and its thumbnail, both metadata-free JPEG bytes."""

    full: bytes
    thumb: bytes
    width: int
    height: int


def photo_dir(hass: HomeAssistant) -> Path:
    """Photo directory."""
    return Path(hass.config.path(PHOTO_DIR))


def photo_path(hass: HomeAssistant, filename: str) -> Path | None:
    """Resolve a stored photo filename safely (no path traversal)."""
    if not _SAFE_NAME.match(filename):
        return None
    return photo_dir(hass) / filename


def thumb_name(filename: str) -> str:
    """Thumbnail filename for a photo (``x.jpg`` -> ``x.thumb.jpg``)."""
    stem, dot, _ext = filename.rpartition(".")
    return f"{stem}.thumb.jpg" if dot and stem else f"{filename}.thumb.jpg"


def is_safe_name(filename: str) -> bool:
    """True for names this module could have written."""
    return bool(_SAFE_NAME.match(filename))


def decode_photo(content_type: str, data_b64: str) -> tuple[bytes, str]:
    """Validate and decode a legacy base64 upload. Returns (bytes, extension)."""
    ext = PHOTO_TYPES.get(content_type)
    if ext is None:
        raise PhotoError("photo_type", f"Unsupported photo type {content_type}")
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


def _encode(image: Image.Image, edge: int, quality: int) -> bytes:
    copy = image.copy()
    copy.info = {}  # drop EXIF, ICC, XMP and comments carried by the source
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    # A freshly encoded image carries no EXIF/ICC/XMP: metadata is stripped.
    copy.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
    return out.getvalue()


def process_image(raw: bytes) -> ProcessedImage:
    """Decode, orient, strip and resize an uploaded image (executor only)."""
    if not raw:
        raise PhotoError("photo_invalid", "Photo is empty")
    if len(raw) > PHOTO_UPLOAD_MAX_BYTES:
        raise PhotoError("photo_too_large", "Photo is larger than 10 MB")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in ("RGBA", "LA", "P"):
                rgba = oriented.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                oriented = flat
            elif oriented.mode != "RGB":
                oriented = oriented.convert("RGB")
            full = _encode(oriented, PHOTO_LONG_EDGE, JPEG_QUALITY)
            thumb = _encode(oriented, PHOTO_THUMB_EDGE, THUMB_QUALITY)
            width, height = oriented.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as err:
        raise PhotoError(
            "photo_invalid", "That file isn't a photo Laundry Hub can read"
        ) from err
    scale = min(1.0, PHOTO_LONG_EDGE / max(width, height))
    return ProcessedImage(full, thumb, round(width * scale), round(height * scale))


def write_processed(directory: Path, filename: str, image: ProcessedImage) -> None:
    """Write a processed photo and its thumbnail atomically (executor only)."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in ((filename, image.full), (thumb_name(filename), image.thumb)):
        tmp = directory / f".{name}.tmp"
        tmp.write_bytes(payload)
        tmp.replace(directory / name)


def write_photo(directory: Path, filename: str, raw: bytes) -> None:
    """Write raw bytes atomically (executor only)."""
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{filename}.tmp"
    tmp.write_bytes(raw)
    tmp.replace(directory / filename)


def delete_photo(directory: Path, filename: str) -> None:
    """Delete a photo and its thumbnail if present (executor only)."""
    if not _SAFE_NAME.match(filename):
        return
    (directory / filename).unlink(missing_ok=True)
    thumb = thumb_name(filename)
    if _SAFE_NAME.match(thumb):
        (directory / thumb).unlink(missing_ok=True)


def ensure_thumbnail(directory: Path, filename: str) -> bool:
    """Create a missing thumbnail for a legacy (v0.1) photo (executor only)."""
    if not _SAFE_NAME.match(filename):
        return False
    source, thumb = directory / filename, directory / thumb_name(filename)
    if thumb.exists() or not source.is_file():
        return False
    try:
        processed = process_image(source.read_bytes())
    except PhotoError:
        return False
    write_photo(directory, thumb.name, processed.thumb)
    return True
