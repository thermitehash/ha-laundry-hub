"""Repairs: purchase-email login stopped working (password revoked)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant

from .config_flow import (
    APP_PASSWORD_URL,
    account_data,
    account_schema,
    async_validate_account,
    server_schema,
)
from .const import CONF_PURCHASE_EMAIL, DOMAIN, ISSUE_PURCHASE_AUTH
from .mailbox import PROVIDER_GENERIC


class PurchaseAuthRepairFlow(RepairsFlow):
    """Back to Step A: enter a new app password; tested before saving."""

    def __init__(self) -> None:
        """Initialize."""
        self._pending: dict[str, Any] = {}

    def _entry(self) -> Any:
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return entries[0] if entries else None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Start with the account step."""
        return await self.async_step_email_account()

    async def async_step_email_account(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Address, new app password, provider."""
        entry = self._entry()
        if entry is None:
            return self.async_abort(reason="not_loaded")
        stored = entry.data.get(CONF_PURCHASE_EMAIL) or {}
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get("provider") == PROVIDER_GENERIC:
                self._pending = dict(user_input)
                return await self.async_step_email_server()
            data = account_data(user_input, {})
            error = await async_validate_account(data)
            if error is None:
                return self._save(entry, data)
            errors["base"] = error
        return self.async_show_form(
            step_id="email_account",
            data_schema=account_schema(user_input or stored),
            errors=errors,
            description_placeholders={"app_password_url": APP_PASSWORD_URL},
        )

    async def async_step_email_server(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Generic IMAP server details."""
        entry = self._entry()
        if entry is None:  # pragma: no cover - entry removed mid-flow
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            data = account_data(self._pending, user_input)
            error = await async_validate_account(data)
            if error is None:
                return self._save(entry, data)
            errors["base"] = error
        stored = entry.data.get(CONF_PURCHASE_EMAIL) or {}
        return self.async_show_form(
            step_id="email_server",
            data_schema=server_schema(user_input or stored),
            errors=errors,
        )

    def _save(self, entry: Any, data: dict[str, Any]) -> RepairsFlowResult:
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_PURCHASE_EMAIL: data}
        )
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for an issue."""
    if issue_id != ISSUE_PURCHASE_AUTH:  # pragma: no cover - only issue we fix
        raise ValueError(issue_id)
    return PurchaseAuthRepairFlow()
