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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import selector
import probatio as vol

from .const import (
    CONF_COLOR,
    CONF_DELETE,
    CONF_FEEDBACK_TIMEOUT,
    CONF_FLASH_DURATION,
    CONF_LIGHT_ENTITY,
    CONF_MISSING_AFTER,
    CONF_NAME,
    CONF_NOTIFY_TARGET,
    CONF_ORDER,
    CONF_STATION_DEVICES,
    DEFAULT_FEEDBACK_TIMEOUT,
    DEFAULT_FLASH_DURATION,
    DEFAULT_MISSING_AFTER,
    DOMAIN,
    MAX_NAME_LEN,
    NAME,
    SEED_DRY_TYPES,
    SEED_WASH_BINS,
    STATIONS,
    SUBENTRY_DRY_TYPE,
    SUBENTRY_WASH_BIN,
)

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
    }
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


class LaundryHubOptionsFlow(OptionsFlow):
    """Edit the main settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = validate_settings(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=options)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=settings_schema(current),
            errors=errors,
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
