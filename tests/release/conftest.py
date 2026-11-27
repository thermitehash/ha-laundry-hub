"""Release-script tests need no Home Assistant instance."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations() -> None:
    """Override the HA fixture: these tests don't start Home Assistant."""


@pytest.fixture(autouse=True)
def _photo_dir() -> None:
    """Override the HA fixture."""
