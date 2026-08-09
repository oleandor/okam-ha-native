"""Low-frequency status coordinator for the battery camera."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OkamApi, OkamApiError
from .const import CONF_CAMERA_ID, CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL, DOMAIN


class OkamCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: OkamApi) -> None:
        interval = int(
            entry.options.get(
                CONF_SNAPSHOT_INTERVAL,
                entry.data.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL),
            )
        )
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=max(60, interval)),
            config_entry=entry,
        )
        self.api = api
        self.camera_id = str(entry.options.get(CONF_CAMERA_ID, entry.data[CONF_CAMERA_ID]))

    async def _async_update_data(self) -> dict:
        try:
            return await self.api.status(self.camera_id)
        except OkamApiError as exc:
            raise UpdateFailed(str(exc)) from exc
