"""Expose garment photos to AI Task as media-source attachments.

Not browsable: it only resolves ``media-source://laundry_hub/<garment>/<file>``
for a photo that belongs to that garment, so AI Task can read the local file.
"""

from __future__ import annotations

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


async def async_get_media_source(hass: HomeAssistant) -> LaundryHubMediaSource:
    """Set up the garment photo media source."""
    return LaundryHubMediaSource(hass)


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
