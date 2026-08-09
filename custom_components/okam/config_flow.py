"""UI configuration for the O-KAM Native Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OkamApi, OkamApiError, OkamAuthError
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CAMERA_ID,
    CONF_IDLE_TIMEOUT,
    CONF_SNAPSHOT_INTERVAL,
    DEFAULT_CAMERA_ID,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_SNAPSHOT_INTERVAL,
    DOMAIN,
)


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_BRIDGE_URL,
                default=defaults.get(CONF_BRIDGE_URL, "http://homeassistant.local:8099"),
            ): str,
            vol.Required(
                CONF_API_TOKEN, default=defaults.get(CONF_API_TOKEN, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_CAMERA_ID, default=defaults.get(CONF_CAMERA_ID, DEFAULT_CAMERA_ID)
            ): str,
            vol.Optional(
                CONF_IDLE_TIMEOUT,
                default=defaults.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=600)),
            vol.Optional(
                CONF_SNAPSHOT_INTERVAL,
                default=defaults.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=60, max=86400)),
        }
    )


async def _validate(hass, data: dict[str, Any]) -> dict[str, Any]:
    api = OkamApi(
        async_get_clientsession(hass), data[CONF_BRIDGE_URL], data[CONF_API_TOKEN]
    )
    await api.health()
    devices = await api.devices()
    camera_id = data.get(CONF_CAMERA_ID) or (devices[0]["camera_id"] if devices else "")
    if not camera_id or not any(item.get("camera_id") == camera_id for item in devices):
        raise ValueError("camera_not_found")
    result = dict(data)
    result[CONF_CAMERA_ID] = camera_id
    return result


class OkamConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await _validate(self.hass, user_input)
            except OkamAuthError:
                errors["base"] = "invalid_auth"
            except OkamApiError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "camera_not_found"
            else:
                await self.async_set_unique_id(data[CONF_BRIDGE_URL].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="O-KAM Native Bridge", data=data)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await _validate(self.hass, user_input)
            except OkamAuthError:
                errors["base"] = "invalid_auth"
            except OkamApiError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "camera_not_found"
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure", data_schema=_schema(entry.data), errors=errors
        )

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None
        if user_input is not None:
            data = {**self._reauth_entry.data, **user_input}
            try:
                validated = await _validate(self.hass, data)
            except OkamAuthError:
                errors["base"] = "invalid_auth"
            except OkamApiError:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "camera_not_found"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry, data=validated
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_API_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                )
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )
