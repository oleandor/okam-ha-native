"""O-KAM Native Bridge integration setup."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OkamApi
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CAMERA_ID,
    CONF_IDLE_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    PLATFORMS,
)
from .coordinator import OkamCoordinator


@dataclass(slots=True)
class OkamRuntime:
    api: OkamApi
    coordinator: OkamCoordinator


type OkamConfigEntry = ConfigEntry[OkamRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: OkamConfigEntry) -> bool:
    api = OkamApi(
        async_get_clientsession(hass),
        entry.data[CONF_BRIDGE_URL],
        entry.data[CONF_API_TOKEN],
    )
    camera_id = str(entry.options.get(CONF_CAMERA_ID, entry.data[CONF_CAMERA_ID]))
    idle_timeout = int(
        entry.options.get(
            CONF_IDLE_TIMEOUT,
            entry.data.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT),
        )
    )
    await api.configure(camera_id, idle_timeout)
    coordinator = OkamCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = OkamRuntime(api, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OkamConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: OkamConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
