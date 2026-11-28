"""Sidebar panel registration and photo serving."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import (
    KEY_HASS_USER,
    HomeAssistantView,
    StaticPathConfig,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.http import KEY_HASS

from .const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL,
    PHOTO_UPLOAD_MAX_BYTES,
    PHOTO_URL,
    PHOTOS_URL,
    VERSION,
)
from .photos import is_safe_name, photo_path, thumb_name

FRONTEND_DIR = Path(__file__).parent / "frontend"
_STATIC_KEY = f"{DOMAIN}_static_registered"


class PhotoView(HomeAssistantView):
    """Serve garment photos (authenticated; panel uses signed paths)."""

    url = PHOTO_URL + "/{garment_id}"
    name = "api:laundry_hub:photo"
    requires_auth = True

    async def get(self, request: web.Request, garment_id: str) -> web.StreamResponse:
        """Return the photo file."""
        hass: HomeAssistant = request.app[KEY_HASS]
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        garment = entries[0].runtime_data.catalog.garments.get(garment_id)
        if garment is None or not garment.photo:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        path = photo_path(hass, garment.photo)
        if path is None or not await hass.async_add_executor_job(path.is_file):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


def _hub(hass: HomeAssistant) -> Any:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


class PhotosView(HomeAssistantView):
    """Serve a garment photo or thumbnail by filename (authenticated)."""

    url = PHOTOS_URL + "/{filename}"
    name = "api:laundry_hub:photos"
    requires_auth = True

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        """Return the file if it belongs to a garment."""
        hass: HomeAssistant = request.app[KEY_HASS]
        hub = _hub(hass)
        if hub is None or not is_safe_name(filename):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        owned = any(
            filename in g.photos or filename in {thumb_name(p) for p in g.photos}
            for g in hub.catalog.garments.values()
        ) or any(
            hub.purchases.owns_photo(name)
            for name in (filename, filename.replace(".thumb.jpg", ".jpg"))
            if name.startswith("purchase-")
        )
        path = photo_path(hass, filename)
        if not owned or path is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        if not await hass.async_add_executor_job(path.is_file):
            # Legacy photo without a thumbnail yet: serve the photo itself.
            stem = filename.removesuffix(".thumb.jpg")
            for g in hub.catalog.garments.values():
                for p in g.photos:
                    if thumb_name(p) == filename and stem == p.rpartition(".")[0]:
                        path = photo_path(hass, p)
            if path is None or not await hass.async_add_executor_job(path.is_file):
                return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.FileResponse(
            path, headers={"Cache-Control": "private, max-age=86400"}
        )


class PendingPhotoView(HomeAssistantView):
    """Thumbnails of images downloaded for a URL-import preview."""

    url = PHOTOS_URL + "/pending/{token}/{index}"
    name = "api:laundry_hub:photos:pending"
    requires_auth = True

    async def get(self, request: web.Request, token: str, index: str) -> web.Response:
        """Return the preview thumbnail."""
        hub = _hub(request.app[KEY_HASS])
        data = None
        if hub is not None and index.isdigit():
            data = hub.wardrobe.pending_thumb(token, int(index))
        if data is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(body=data, content_type="image/jpeg")


class PhotoUploadView(HomeAssistantView):
    """Upload one photo (multipart: garment_id, file). Admin only."""

    url = PHOTOS_URL
    name = "api:laundry_hub:photos:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Resize, strip and store the photo; returns the garment view."""
        hass: HomeAssistant = request.app[KEY_HASS]
        user = request.get(KEY_HASS_USER)
        if user is None or not user.is_admin:
            return self.json_message("Admin only", HTTPStatus.UNAUTHORIZED)
        hub = _hub(hass)
        if hub is None:
            return self.json_message("Laundry Hub is not set up", HTTPStatus.NOT_FOUND)
        request._client_max_size = PHOTO_UPLOAD_MAX_BYTES + 64 * 1024
        try:
            form = await request.post()
        except ValueError:
            return self.json_message(
                "Photo is larger than 10 MB", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
            )
        upload = form.get("file")
        garment_id = form.get("garment_id")
        if not isinstance(upload, web.FileField) or not isinstance(garment_id, str):
            return self.json_message("Send garment_id and file", HTTPStatus.BAD_REQUEST)
        raw = await hass.async_add_executor_job(upload.file.read)
        try:
            garment = hub.resolve_garment(garment_id)
            await hub.wardrobe.async_add_photo(garment, raw)
        except ServiceValidationError as err:
            return self.json(
                {"code": err.translation_key, "message": str(err)},
                HTTPStatus.BAD_REQUEST,
            )
        except HomeAssistantError as err:
            return self.json_message(str(err), HTTPStatus.INTERNAL_SERVER_ERROR)
        return self.json(hub.garment_view(garment))


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the static JS, photo view and the admin-only sidebar panel."""
    if not hass.data.get(_STATIC_KEY):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.http.register_view(PhotoView())
        hass.http.register_view(PhotosView())
        hass.http.register_view(PendingPhotoView())
        hass.http.register_view(PhotoUploadView())
        hass.data[_STATIC_KEY] = True
    if PANEL_URL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_STATIC_URL}/laundry-panel.js?v={VERSION}",
        require_admin=True,
        config={"domain": DOMAIN},
    )


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel."""
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
