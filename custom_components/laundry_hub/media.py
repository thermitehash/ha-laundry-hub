"""Expose garment photos to AI Task as media-source attachments, hidden.

AI Task only accepts attachments as media-source IDs that resolve to local
files. A ``media_source.py`` platform would make "Laundry Hub" appear at the
root of HA's media browser, so this module is deliberately *not* a platform.
Instead the source is registered directly in media_source's lazy platform
cache at setup: ``media-source://laundry_hub/<garment>/<file>`` resolves (only
for a photo that belongs to that garment), but the root listing, which only
lists integrations that ship a ``media_source.py``, never shows it.
"""

from __future__ import annotations

import logging

from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, NAME, PHOTOS_URL
from .photos import photo_path

_LOGGER = logging.getLogger(__name__)


def async_register_hidden_source(hass: HomeAssistant) -> bool:
    """Make our IDs resolvable without listing us in the media browser.

    Returns False (and AI descriptions can't attach photos) if media_source
    isn't loaded or its internals changed; never raises.
    """
    try:
        from homeassistant.components.media_source.const import (  # noqa: PLC0415
            DATA_MEDIA_SOURCE_PLATFORMS,
        )
    except ImportError:  # pragma: no cover - media_source always ships
        return False
    platforms = hass.data.get(DATA_MEDIA_SOURCE_PLATFORMS)
    processed = getattr(platforms, "_processed", None)
    if not isinstance(processed, dict):
        _LOGGER.warning(
            "Media Source isn't loaded; AI descriptions can't attach garment photos"
        )
        return False
    if not isinstance(processed.get(DOMAIN), LaundryHubMediaSource):
        processed[DOMAIN] = LaundryHubMediaSource(hass)
    return True


class LaundryHubMediaSource(MediaSource):
    """Resolve garment photos (not browsable)."""

    name = NAME

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve <garment_id>/<filename> to the local photo file."""
        garment_id, _, filename = (item.identifier or "").partition("/")
        entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise Unresolvable("Laundry Hub is not loaded")
        garment = entries[0].runtime_data.catalog.garments.get(garment_id)
        if garment is None or filename not in garment.photos:
            raise Unresolvable("Unknown garment photo")
        path = photo_path(self.hass, filename)
        if path is None or not await self.hass.async_add_executor_job(path.is_file):
            raise Unresolvable("Photo file is missing")
        return PlayMedia(f"{PHOTOS_URL}/{filename}", "image/jpeg", path=path)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Garment photos are private to the Laundry panel."""
        raise BrowseError("Laundry Hub photos are only shown in the Laundry panel")
