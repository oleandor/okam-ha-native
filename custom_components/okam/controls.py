"""Shared helpers for the camera control entities (light, night vision)."""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError

from . import OkamRuntime
from .api import OkamApiError
from .entity import OkamEntity


def controls_supported(runtime: OkamRuntime) -> bool:
    return bool(runtime.coordinator.data.get("controls_supported"))


class OkamControlEntity(OkamEntity):
    """An entity whose state is the bridge's last confirmed camera control state.

    Reading it never wakes the camera: the bridge reports what the camera last
    confirmed. Changing it goes through the camera's live session, which the
    bridge starts on demand and lets idle out afterwards.
    """

    _control_key: str

    def __init__(self, runtime: OkamRuntime) -> None:
        super().__init__(runtime.coordinator)
        self._api = runtime.api
        camera_id = runtime.coordinator.camera_id
        self._attr_unique_id = f"{camera_id}_{self._control_key}"

    @property
    def _controls(self) -> dict[str, Any]:
        controls = self.coordinator.data.get("controls")
        return controls if isinstance(controls, dict) else {}

    @property
    def assumed_state(self) -> bool:
        # Unknown until the camera has confirmed it once in this bridge run.
        return self._control_key not in self._controls

    async def _async_send(self, payload: dict[str, Any]) -> None:
        try:
            controls = await self._api.control(self.coordinator.camera_id, payload)
        except OkamApiError as exc:
            raise HomeAssistantError(f"O-KAM camera did not confirm: {exc}") from exc
        data = dict(self.coordinator.data)
        data["controls"] = {**self._controls, **controls}
        self.coordinator.async_set_updated_data(data)
