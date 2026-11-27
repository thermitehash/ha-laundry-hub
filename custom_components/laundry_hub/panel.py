"""Sidebar panel registration and photo serving."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

from aiohttp import web
from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL,
    PHOTO_URL,
    VERSION,
)
from .photos import photo_path

FRONTEND_DIR = Path(__file__).parent / "frontend"
_STATIC_KEY = f"{DOMAIN}_static_registered"


class PhotoView(HomeAssistantView):
    """Serve garment photos (authenticated; panel uses signed paths)."""

    url = PHOTO_URL + "/{garment_id}"
    name = "api:laundry_hub:photo"
    requires_auth = True

    async def get(self, request: web.Request, garment_id: str) -> web.StreamResponse:
        """Return the photo file."""
        hass: HomeAssistant = request.app["hass"]
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


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the static JS, photo view and the admin-only sidebar panel."""
    if not hass.data.get(_STATIC_KEY):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(FRONTEND_DIR), cache_headers=False)]
        )
        hass.http.register_view(PhotoView())
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
