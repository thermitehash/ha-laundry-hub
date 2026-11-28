"""Config, options and subentry flows for Laundry Hub."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
import probatio as vol

from .access import ACCESS_ADMINS, ACCESS_HOUSEHOLD, owner_for
from .const import (
    CONF_AI_TASK_ENTITY,
    CONF_APP_ACCESS,
    CONF_COLOR,
    CONF_DELETE,
    CONF_FEEDBACK_TIMEOUT,
    CONF_FLASH_DURATION,
    CONF_FOLLOW_HA_THEME,
    CONF_LIGHT_ENTITY,
    CONF_MISSING_AFTER,
    CONF_NAME,
    CONF_NOTIFY_TARGET,
    CONF_ORDER,
    CONF_PURCHASE_ARCHIVE_DAYS,
    CONF_PURCHASE_BACKFILL,
    CONF_PURCHASE_CLOTHING_ONLY,
    CONF_PURCHASE_CUSTOM,
    CONF_PURCHASE_EMAIL,
    CONF_PURCHASE_INTERVAL,
    CONF_PURCHASE_SHOPIFY,
    CONF_PURCHASE_STORES,
    CONF_STATION_DEVICES,
    CONF_USER_OWNERS,
    CONF_WARDROBE_ENABLED,
    DEFAULT_FEEDBACK_TIMEOUT,
    DEFAULT_FLASH_DURATION,
    DEFAULT_MISSING_AFTER,
    DEFAULT_PURCHASE_ARCHIVE_DAYS,
    DEFAULT_PURCHASE_BACKFILL,
    DEFAULT_PURCHASE_INTERVAL,
    DOMAIN,
    MAX_NAME_LEN,
    MIN_PURCHASE_INTERVAL,
    NAME,
    PURCHASE_BACKFILL_CHOICES,
    PURCHASE_COUNT_DAYS,
    SEED_DRY_TYPES,
    SEED_WASH_BINS,
    STATIONS,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)
from .mailbox import (
    PROVIDER_GENERIC,
    PROVIDER_GMAIL,
    MailboxError,
    account_from_data,
    test_login,
)
from .stores import CATALOG, StoreInfo, parse_domains, parse_names

if TYPE_CHECKING:
    from .hub import LaundryHub


def _device_key(station: str) -> str:
    return f"{station}_device"


def settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for the main entry settings (setup and options)."""
    devices = defaults.get(CONF_STATION_DEVICES) or {}
    fields: dict[Any, Any] = {}
    for station in STATIONS:
        key = _device_key(station)
        default = devices.get(station)
        marker = (
            vol.Optional(key, description={"suggested_value": default})
            if default
            else vol.Optional(key)
        )
        fields[marker] = selector.DeviceSelector(
            selector.DeviceSelectorConfig(integration="esphome")
        )
    fields[
        vol.Required(
            CONF_FEEDBACK_TIMEOUT,
            default=defaults.get(CONF_FEEDBACK_TIMEOUT, DEFAULT_FEEDBACK_TIMEOUT),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0.5,
            max=10,
            step=0.5,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    fields[
        vol.Required(
            CONF_FLASH_DURATION,
            default=defaults.get(CONF_FLASH_DURATION, DEFAULT_FLASH_DURATION),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=300,
            step=1,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    fields[
        vol.Required(
            CONF_MISSING_AFTER,
            default=defaults.get(CONF_MISSING_AFTER, DEFAULT_MISSING_AFTER),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=365,
            step=1,
            unit_of_measurement="d",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    notify_default = defaults.get(CONF_NOTIFY_TARGET)
    fields[
        vol.Optional(
            CONF_NOTIFY_TARGET, description={"suggested_value": notify_default}
        )
        if notify_default
        else vol.Optional(CONF_NOTIFY_TARGET)
    ] = selector.TextSelector()
    fields[
        vol.Required(
            CONF_WARDROBE_ENABLED, default=bool(defaults.get(CONF_WARDROBE_ENABLED))
        )
    ] = selector.BooleanSelector()
    ai_default = defaults.get(CONF_AI_TASK_ENTITY)
    fields[
        vol.Optional(CONF_AI_TASK_ENTITY, description={"suggested_value": ai_default})
        if ai_default
        else vol.Optional(CONF_AI_TASK_ENTITY)
    ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))
    fields[
        vol.Required(
            CONF_FOLLOW_HA_THEME, default=bool(defaults.get(CONF_FOLLOW_HA_THEME))
        )
    ] = selector.BooleanSelector()
    fields[
        vol.Required(
            CONF_APP_ACCESS, default=defaults.get(CONF_APP_ACCESS) or ACCESS_ADMINS
        )
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[ACCESS_ADMINS, ACCESS_HOUSEHOLD],
            translation_key="app_access",
            mode=selector.SelectSelectorMode.LIST,
        )
    )
    return vol.Schema(fields)


def validate_settings(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate settings; return (options, errors)."""
    errors: dict[str, str] = {}
    devices = {
        station: user_input[_device_key(station)]
        for station in STATIONS
        if user_input.get(_device_key(station))
    }
    if len(set(devices.values())) != len(devices):
        errors["base"] = "duplicate_device"
    target = (user_input.get(CONF_NOTIFY_TARGET) or "").strip()
    if target:
        is_entity = target.startswith("notify.") and hass.states.get(target)
        service = target.removeprefix("notify.")
        if not is_entity and not hass.services.has_service("notify", service):
            errors[CONF_NOTIFY_TARGET] = "notify_not_found"
    options = {
        CONF_STATION_DEVICES: devices,
        CONF_FEEDBACK_TIMEOUT: float(user_input[CONF_FEEDBACK_TIMEOUT]),
        CONF_FLASH_DURATION: int(user_input[CONF_FLASH_DURATION]),
        CONF_MISSING_AFTER: int(user_input[CONF_MISSING_AFTER]),
        CONF_NOTIFY_TARGET: target or None,
        CONF_WARDROBE_ENABLED: bool(user_input.get(CONF_WARDROBE_ENABLED, False)),
        CONF_AI_TASK_ENTITY: user_input.get(CONF_AI_TASK_ENTITY) or None,
        CONF_FOLLOW_HA_THEME: bool(user_input.get(CONF_FOLLOW_HA_THEME, False)),
        CONF_APP_ACCESS: (
            ACCESS_HOUSEHOLD
            if user_input.get(CONF_APP_ACCESS) == ACCESS_HOUSEHOLD
            else ACCESS_ADMINS
        ),
    }
    ai_entity = user_input.get(CONF_AI_TASK_ENTITY) or None
    if ai_entity and hass.states.get(str(ai_entity)) is None:
        errors[CONF_AI_TASK_ENTITY] = "ai_entity_not_found"
    return options, errors


def seed_subentries() -> list[ConfigSubentryData]:
    """Default wash bins and dry types created on first setup."""
    seeds: list[ConfigSubentryData] = []
    for zone_type, items in (
        (SUBENTRY_WASH_BIN, SEED_WASH_BINS),
        (SUBENTRY_DRY_TYPE, SEED_DRY_TYPES),
    ):
        for order, (name, color) in enumerate(items):
            seeds.append(
                ConfigSubentryData(
                    data={
                        CONF_NAME: name,
                        CONF_COLOR: list(color),
                        CONF_LIGHT_ENTITY: None,
                        CONF_ORDER: order,
                    },
                    subentry_type=zone_type,
                    title=name,
                    unique_id=None,
                )
            )
    return seeds


class LaundryHubConfigFlow(ConfigFlow, domain=DOMAIN):
    """Main (single instance) config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = validate_settings(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=NAME,
                    data={},
                    options=options,
                    subentries=seed_subentries(),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                settings_schema({}), user_input or {}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return LaundryHubOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Wash bins and dry types are subentries."""
        return {
            SUBENTRY_WASH_BIN: WashBinSubentryFlow,
            SUBENTRY_DRY_TYPE: DryTypeSubentryFlow,
        }


APP_PASSWORD_URL = "https://myaccount.google.com/apppasswords"
DISCONNECT_KEEP = "keep"
DISCONNECT_DELETE = "delete"


def account_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Step A: email address, app password, provider."""
    return vol.Schema(
        {
            vol.Required("email", default=defaults.get("email", "")): (
                selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.EMAIL, autocomplete="username"
                    )
                )
            ),
            vol.Required("password"): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
            vol.Required(
                "provider", default=defaults.get("provider", PROVIDER_GMAIL)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[PROVIDER_GMAIL, PROVIDER_GENERIC],
                    translation_key="email_provider",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
    )


def server_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Generic IMAP: host, port, folder."""
    return vol.Schema(
        {
            vol.Required("host", default=defaults.get("host", "")): (
                selector.TextSelector()
            ),
            vol.Required("port", default=int(defaults.get("port", 993))): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                )
            ),
            vol.Required("folder", default=defaults.get("folder", "INBOX")): (
                selector.TextSelector()
            ),
        }
    )


def account_data(user_input: dict[str, Any], server: dict[str, Any]) -> dict[str, Any]:
    """What is stored in entry.data (same shape as core IMAP: plain password)."""
    data: dict[str, Any] = {
        "email": str(user_input["email"]).strip(),
        "password": str(user_input["password"]),
        "provider": user_input.get("provider", PROVIDER_GMAIL),
    }
    if data["provider"] == PROVIDER_GENERIC:
        data["host"] = str(server.get("host", "")).strip()
        data["port"] = int(server.get("port", 993))
        data["folder"] = str(server.get("folder", "INBOX")).strip() or "INBOX"
    return data


async def async_validate_account(data: dict[str, Any]) -> str | None:
    """Log in and open the folder read-only. Returns an error key or None."""
    account = account_from_data(data)
    if account is None or (
        data.get("provider") == PROVIDER_GENERIC and not data.get("host")
    ):
        return "cannot_connect"
    try:
        await test_login(account)
    except MailboxError as err:
        return err.key
    return None


class LaundryHubOptionsFlow(OptionsFlow):
    """Edit the main settings and the purchase-email connection."""

    def __init__(self) -> None:
        """Initialize."""
        self._account: dict[str, Any] | None = None
        self._pending: dict[str, Any] = {}
        self._changing_password = False
        self._pending_settings: dict[str, Any] = {}

    @property
    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def _hub(self) -> LaundryHub | None:
        return getattr(self.config_entry, "runtime_data", None)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Settings form; with Wardrobe on, a menu that adds Purchase emails."""
        if not self._current.get(CONF_WARDROBE_ENABLED):
            return await self._settings_form("init", user_input)
        if self.config_entry.data.get(CONF_PURCHASE_EMAIL):
            options = [
                "settings",
                "email_stores",
                "email_account",
                "email_options",
                "email_check",
                "email_disconnect",
            ]
        else:
            options = ["settings", "email_account"]
        return self.async_show_menu(
            step_id="init",
            menu_options=options,
            description_placeholders={"status": self._status_text()},
        )

    def _status_text(self) -> str:
        hub = self._hub
        if not self.config_entry.data.get(CONF_PURCHASE_EMAIL):
            return "not connected"
        stores = sum(
            len(self._current.get(key) or [])
            for key in (
                CONF_PURCHASE_STORES,
                CONF_PURCHASE_CUSTOM,
                CONF_PURCHASE_SHOPIFY,
            )
        )
        parts = [f"connected ({stores} stores)"]
        if hub is not None:
            status = hub.purchases.status()
            if status["last_check"]:
                checked = dt_util.parse_datetime(status["last_check"])
                if checked is not None:
                    minutes = int((dt_util.utcnow() - checked).total_seconds() // 60)
                    parts.append(f"last check {minutes} min ago")
            else:
                parts.append("not checked yet")
            if status["last_error"]:
                parts.append(f"last error: {status['last_error']}")
            parts.append(f"{status['new_cards']} new cards")
        return " · ".join(parts)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Main settings (from the menu)."""
        return await self._settings_form("settings", user_input)

    async def _settings_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = validate_settings(self.hass, user_input)
            if not errors:
                options = {**self._purchase_options(), **options}
                if CONF_USER_OWNERS in self.config_entry.options:
                    options[CONF_USER_OWNERS] = self.config_entry.options[
                        CONF_USER_OWNERS
                    ]
                if options[CONF_APP_ACCESS] == ACCESS_HOUSEHOLD:
                    self._pending_settings = options
                    return await self.async_step_app_people()
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id=step_id,
            data_schema=settings_schema(self._current),
            errors=errors,
        )

    async def _people(self) -> list[tuple[str, str, str]]:
        """(field key, user id, user name) for every real HA user."""
        users = [
            u
            for u in await self.hass.auth.async_get_users()
            if not u.system_generated and u.is_active
        ]
        out: list[tuple[str, str, str]] = []
        used: set[str] = set()
        for user in sorted(users, key=lambda u: (u.name or "").lower()):
            key = (user.name or "User").strip() or "User"
            if key in used:
                key = f"{key} ({user.id[:6]})"
            used.add(key)
            out.append((key, user.id, user.name or ""))
        return out

    async def async_step_app_people(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Household access: which Laundry Hub owner each HA user is."""
        people = await self._people()
        if user_input is not None:
            mapping = {
                user_id: str(user_input.get(key) or "") for key, user_id, _ in people
            }
            return self.async_create_entry(
                data={**self._pending_settings, CONF_USER_OWNERS: mapping}
            )
        hub = self._hub
        fields: dict[Any, Any] = {}
        for key, user_id, _name in people:
            current = owner_for(hub, user_id) if hub is not None else None
            marker = (
                vol.Optional(key, description={"suggested_value": current})
                if current
                else vol.Optional(key)
            )
            fields[marker] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person")
            )
        return self.async_show_form(
            step_id="app_people", data_schema=vol.Schema(fields)
        )

    def _purchase_options(self) -> dict[str, Any]:
        """Purchase-email options currently stored (kept by other steps)."""
        keys = (
            CONF_PURCHASE_STORES,
            CONF_PURCHASE_CUSTOM,
            CONF_PURCHASE_SHOPIFY,
            CONF_PURCHASE_INTERVAL,
            CONF_PURCHASE_BACKFILL,
            CONF_PURCHASE_CLOTHING_ONLY,
            CONF_PURCHASE_ARCHIVE_DAYS,
        )
        return {
            k: self.config_entry.options[k]
            for k in keys
            if k in self.config_entry.options
        }

    # --- Step A: account ---------------------------------------------------

    async def async_step_email_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Account: address + app password; the login is tested before saving."""
        errors: dict[str, str] = {}
        stored = self.config_entry.data.get(CONF_PURCHASE_EMAIL) or {}
        self._changing_password = bool(stored)
        if not self._current.get(CONF_AI_TASK_ENTITY):
            errors["base"] = "no_ai_entity"
        elif user_input is not None:
            if user_input.get("provider") == PROVIDER_GENERIC:
                self._pending = dict(user_input)
                return await self.async_step_email_server()
            data = account_data(user_input, {})
            error = await async_validate_account(data)
            if error is None:
                return await self._account_ok(data)
            errors["base"] = error
        return self.async_show_form(
            step_id="email_account",
            data_schema=account_schema(user_input or stored),
            errors=errors,
            description_placeholders={"app_password_url": APP_PASSWORD_URL},
        )

    async def async_step_email_server(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Generic IMAP: server, port and folder."""
        errors: dict[str, str] = {}
        stored = self.config_entry.data.get(CONF_PURCHASE_EMAIL) or {}
        if user_input is not None:
            data = account_data(self._pending, user_input)
            error = await async_validate_account(data)
            if error is None:
                return await self._account_ok(data)
            errors["base"] = error
        return self.async_show_form(
            step_id="email_server",
            data_schema=server_schema(user_input or stored),
            errors=errors,
        )

    async def _account_ok(self, data: dict[str, Any]) -> ConfigFlowResult:
        self._account = data
        if self._changing_password:
            self._save_account(data)
            return self.async_create_entry(data=dict(self.config_entry.options))
        return await self.async_step_email_stores()

    def _save_account(self, data: dict[str, Any] | None) -> None:
        new = dict(self.config_entry.data)
        if data is None:
            new.pop(CONF_PURCHASE_EMAIL, None)
        else:
            new[CONF_PURCHASE_EMAIL] = data
        self.hass.config_entries.async_update_entry(self.config_entry, data=new)

    # --- Step B: stores ----------------------------------------------------

    async def _store_counts(self) -> dict[str, int] | None:
        from .purchases import async_count_store_emails  # noqa: PLC0415

        account = account_from_data(
            self._account or self.config_entry.data.get(CONF_PURCHASE_EMAIL)
        )
        if account is None:
            return None
        try:
            return await async_count_store_emails(account, PURCHASE_COUNT_DAYS)
        except MailboxError:
            return None

    async def async_step_email_stores(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Opt in to stores (none preselected) and custom sender domains."""
        errors: dict[str, str] = {}
        if user_input is not None:
            stores = [k for k in user_input.get(CONF_PURCHASE_STORES, []) if k]
            custom, invalid = parse_domains(user_input.get(CONF_PURCHASE_CUSTOM, ""))
            names, bad_names = parse_names(user_input.get(CONF_PURCHASE_SHOPIFY, ""))
            if invalid:
                errors[CONF_PURCHASE_CUSTOM] = "invalid_domain"
            elif bad_names:
                errors[CONF_PURCHASE_SHOPIFY] = "invalid_shop_name"
            elif not stores and not custom and not names:
                errors["base"] = "no_stores"
            else:
                self._pending = {
                    CONF_PURCHASE_STORES: stores,
                    CONF_PURCHASE_CUSTOM: custom,
                    CONF_PURCHASE_SHOPIFY: names,
                }
                if self._account is None:  # editing stores only
                    return self.async_create_entry(
                        data={**self.config_entry.options, **self._pending}
                    )
                return await self.async_step_email_options()
        counts = await self._store_counts()
        labelled = sorted(
            CATALOG,
            key=lambda s: (0 if (counts or {}).get(s.key) else 1, s.name.lower()),
        )

        def label(store: StoreInfo) -> str:
            if counts is None:
                return store.name
            n = counts.get(store.key, 0)
            return f"{store.name} — {n} email{'s' if n != 1 else ''}"

        current = self._current
        chosen = (
            user_input.get(CONF_PURCHASE_STORES)
            if user_input
            else current.get(CONF_PURCHASE_STORES)
        ) or []
        custom_text = (
            user_input.get(CONF_PURCHASE_CUSTOM)
            if user_input
            else ", ".join(current.get(CONF_PURCHASE_CUSTOM) or [])
        ) or ""
        shop_text = (
            user_input.get(CONF_PURCHASE_SHOPIFY)
            if user_input
            else ", ".join(current.get(CONF_PURCHASE_SHOPIFY) or [])
        ) or ""
        schema = vol.Schema(
            {
                vol.Optional(CONF_PURCHASE_STORES, default=list(chosen)): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=s.key, label=label(s))
                                for s in labelled
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                ),
                vol.Optional(
                    CONF_PURCHASE_CUSTOM, description={"suggested_value": custom_text}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_PURCHASE_SHOPIFY, description={"suggested_value": shop_text}
                ): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="email_stores",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "count_note": (
                    f"Counts are emails from the last {PURCHASE_COUNT_DAYS} days."
                    if counts is not None
                    else "Couldn't count emails right now."
                )
            },
        )

    # --- Step C: options ---------------------------------------------------

    async def async_step_email_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How often to check, backfill, clothing only, auto-archive."""
        if user_input is not None:
            options = {
                **self.config_entry.options,
                **self._pending,
                CONF_PURCHASE_INTERVAL: max(
                    MIN_PURCHASE_INTERVAL, int(user_input[CONF_PURCHASE_INTERVAL])
                ),
                CONF_PURCHASE_BACKFILL: int(user_input[CONF_PURCHASE_BACKFILL]),
                CONF_PURCHASE_CLOTHING_ONLY: bool(
                    user_input[CONF_PURCHASE_CLOTHING_ONLY]
                ),
                CONF_PURCHASE_ARCHIVE_DAYS: int(user_input[CONF_PURCHASE_ARCHIVE_DAYS]),
            }
            if self._account is not None:
                self._save_account(self._account)
            return self.async_create_entry(data=options)
        current = self._current
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PURCHASE_INTERVAL,
                    default=int(
                        current.get(CONF_PURCHASE_INTERVAL, DEFAULT_PURCHASE_INTERVAL)
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_PURCHASE_INTERVAL,
                        max=1440,
                        step=1,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_PURCHASE_BACKFILL,
                    default=str(
                        current.get(CONF_PURCHASE_BACKFILL, DEFAULT_PURCHASE_BACKFILL)
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[str(d) for d in PURCHASE_BACKFILL_CHOICES],
                        translation_key="email_backfill",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_PURCHASE_CLOTHING_ONLY,
                    default=bool(current.get(CONF_PURCHASE_CLOTHING_ONLY, True)),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_PURCHASE_ARCHIVE_DAYS,
                    default=int(
                        current.get(
                            CONF_PURCHASE_ARCHIVE_DAYS, DEFAULT_PURCHASE_ARCHIVE_DAYS
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=365,
                        step=1,
                        unit_of_measurement="d",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="email_options", data_schema=schema)

    # --- check now / disconnect --------------------------------------------

    async def async_step_email_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check purchase emails now."""
        hub = self._hub
        if hub is None:
            return self.async_abort(reason="not_loaded")
        try:
            summary = await hub.purchases.async_check(manual=True)
        except HomeAssistantError as err:
            return self.async_abort(
                reason="check_failed", description_placeholders={"error": str(err)}
            )
        return self.async_abort(
            reason="check_done",
            description_placeholders={
                "new": str(summary.get("new_cards", 0)),
                "updated": str(summary.get("updated_cards", 0)),
            },
        )

    async def async_step_email_disconnect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete the stored credentials; keep or delete the existing cards."""
        hub = self._hub
        if user_input is not None:
            if hub is not None:
                if user_input.get("cards") == DISCONNECT_DELETE:
                    await hub.purchases.async_delete_all()
                hub.purchases.reset_state()
            self._save_account(None)
            return self.async_create_entry(data=dict(self.config_entry.options))
        cards = len(hub.purchases.cards) if hub is not None else 0
        return self.async_show_form(
            step_id="email_disconnect",
            data_schema=vol.Schema(
                {
                    vol.Required("cards", default=DISCONNECT_KEEP): (
                        selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[DISCONNECT_KEEP, DISCONNECT_DELETE],
                                translation_key="email_disconnect_cards",
                                mode=selector.SelectSelectorMode.LIST,
                            )
                        )
                    )
                }
            ),
            description_placeholders={"cards": str(cards)},
        )


class _ZoneSubentryFlow(ConfigSubentryFlow):
    """Add / edit / delete a wash bin or dry type."""

    zone_type: str

    def _schema(self, defaults: dict[str, Any], *, editing: bool) -> vol.Schema:
        light = defaults.get(CONF_LIGHT_ENTITY)
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): (
                selector.TextSelector()
            ),
            vol.Required(
                CONF_COLOR, default=list(defaults.get(CONF_COLOR, [255, 255, 255]))
            ): selector.ColorRGBSelector(),
            (
                vol.Optional(CONF_LIGHT_ENTITY, description={"suggested_value": light})
                if light
                else vol.Optional(CONF_LIGHT_ENTITY)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="light")),
            vol.Required(CONF_ORDER, default=int(defaults.get(CONF_ORDER, 0))): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=99, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                )
            ),
        }
        if editing:
            fields[vol.Optional(CONF_DELETE, default=False)] = (
                selector.BooleanSelector()
            )
        return vol.Schema(fields)

    def _validate(
        self, user_input: dict[str, Any], current_id: str | None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        errors: dict[str, str] = {}
        name = str(user_input.get(CONF_NAME, "")).strip()
        if not name or len(name) > MAX_NAME_LEN:
            errors[CONF_NAME] = "invalid_name"
        entry = self._get_entry()
        for sid, sub in entry.subentries.items():
            if (
                sid != current_id
                and sub.subentry_type == self.zone_type
                and sub.title.casefold() == name.casefold()
            ):
                errors[CONF_NAME] = "name_exists"
        color = user_input.get(CONF_COLOR) or [255, 255, 255]
        try:
            rgb = [max(0, min(255, int(c))) for c in color][:3]
        except (TypeError, ValueError):
            rgb = []
        if len(rgb) != 3:
            errors[CONF_COLOR] = "invalid_color"
        data = {
            CONF_NAME: name,
            CONF_COLOR: rgb,
            CONF_LIGHT_ENTITY: user_input.get(CONF_LIGHT_ENTITY) or None,
            CONF_ORDER: int(user_input.get(CONF_ORDER, 0)),
        }
        return data, errors

    def _next_order(self) -> int:
        orders = [
            int(sub.data.get(CONF_ORDER, 0))
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == self.zone_type
        ]
        return max(orders, default=-1) + 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = self._validate(user_input, None)
            if not errors:
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        defaults = user_input or {CONF_ORDER: self._next_order()}
        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(defaults, editing=False),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit (or delete) a zone."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"name": subentry.title, "count": "0"}
        if user_input is not None:
            if user_input.get(CONF_DELETE):
                hub: LaundryHub | None = getattr(entry, "runtime_data", None)
                if hub is None:
                    errors["base"] = "not_loaded"
                else:
                    try:
                        hub.remove_zone(subentry.subentry_id)
                    except ServiceValidationError:
                        errors["base"] = "zone_in_use"
                        placeholders["count"] = str(
                            hub.catalog.usage(subentry.subentry_id)
                        )
                    else:
                        return self.async_abort(
                            reason="zone_removed", description_placeholders=placeholders
                        )
            else:
                data, errors = self._validate(user_input, subentry.subentry_id)
                if not errors:
                    return self.async_update_and_abort(
                        entry, subentry, title=data[CONF_NAME], data=data
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(user_input or dict(subentry.data), editing=True),
            errors=errors,
            description_placeholders=placeholders,
        )


class WashBinSubentryFlow(_ZoneSubentryFlow):
    """Wash bin subentry flow."""

    zone_type = SUBENTRY_WASH_BIN


class DryTypeSubentryFlow(_ZoneSubentryFlow):
    """Dry type subentry flow."""

    zone_type = SUBENTRY_DRY_TYPE
