"""Fixtures for Laundry Hub tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.laundry_hub.config_flow import seed_subentries
from custom_components.laundry_hub.const import (
    CONF_FEEDBACK_TIMEOUT,
    CONF_FLASH_DURATION,
    CONF_MISSING_AFTER,
    CONF_NOTIFY_TARGET,
    CONF_STATION_DEVICES,
    DOMAIN,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from custom_components.laundry_hub.hub import LaundryHub


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in every test."""


@pytest.fixture(autouse=True)
def _shared_import_session(
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Route pinned import fetches through HA's (mockable) shared session.

    The real one-off pinned session is tested on its own (marker
    ``pinned_session``); everywhere else ``aioclient_mock`` should see requests.
    """
    if request.node.get_closest_marker("pinned_session"):
        yield
        return
    from homeassistant.helpers.aiohttp_client import (  # noqa: PLC0415
        async_get_clientsession,
    )

    from custom_components.laundry_hub import importer  # noqa: PLC0415

    def shared(hass: HomeAssistant, host: str, addresses: list[str]) -> Any:
        session = async_get_clientsession(hass)
        session._laundry_shared = True  # type: ignore[attr-defined]  # noqa: SLF001
        return session

    with patch.object(importer, "_open_session", shared):
        yield


@pytest.fixture(autouse=True)
def _photo_dir(hass: HomeAssistant, tmp_path: Any) -> None:
    hass.config.config_dir = str(tmp_path)


OPTIONS: dict[str, Any] = {
    CONF_STATION_DEVICES: {},
    CONF_FEEDBACK_TIMEOUT: 2.0,
    CONF_FLASH_DURATION: 15,
    CONF_MISSING_AFTER: 14,
    CONF_NOTIFY_TARGET: None,
}


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Main entry with seeded subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Laundry Hub",
        data={},
        options={**OPTIONS, **(options or {})},
        subentries_data=seed_subentries(),
        version=1,
    )


@pytest.fixture
def light_calls(hass: HomeAssistant) -> dict[str, list[ServiceCall]]:
    """Mock light services."""
    return {
        "turn_on": async_mock_service(hass, "light", "turn_on"),
        "turn_off": async_mock_service(hass, "light", "turn_off"),
    }


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a loaded entry."""
    config_entry = make_entry()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture
def hub(entry: MockConfigEntry) -> LaundryHub:
    """The runtime hub."""
    runtime: LaundryHub = entry.runtime_data
    return runtime


def zone_id(hub: LaundryHub, zone_type: str, name: str) -> str:
    """Find a zone id by name."""
    return next(z.id for z in hub.zones_of(zone_type) if z.name == name)


def bin_id(hub: LaundryHub, name: str) -> str:
    """Wash bin id by name."""
    return zone_id(hub, SUBENTRY_WASH_BIN, name)


def dry_id(hub: LaundryHub, name: str) -> str:
    """Dry type id by name."""
    return zone_id(hub, SUBENTRY_DRY_TYPE, name)


def add_esphome_station(
    hass: HomeAssistant, node_name: str
) -> tuple[MockConfigEntry, str]:
    """Register a fake ESPHome config entry + device; return (entry, device_id)."""
    esp_entry = MockConfigEntry(
        domain="esphome",
        data={"device_name": node_name, "host": "1.2.3.4"},
        unique_id=node_name,
    )
    esp_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=esp_entry.entry_id,
        identifiers={("esphome", node_name)},
        name=node_name,
    )
    return esp_entry, device.id


@pytest.fixture
def frozen() -> Generator[Any]:
    """Placeholder to document freezer usage (pytest-freezer `freezer` fixture)."""
    yield None
