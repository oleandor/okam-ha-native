"""Shared O-KAM camera metadata."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OkamCoordinator


class OkamEntity(CoordinatorEntity[OkamCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OkamCoordinator) -> None:
        super().__init__(coordinator)
        camera_id = coordinator.camera_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, camera_id)},
            name=coordinator.data.get("name", "O-KAM camera"),
            manufacturer="VStarcam / O-KAM",
            model="Native P2P camera",
        )

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.data.get("online"))
