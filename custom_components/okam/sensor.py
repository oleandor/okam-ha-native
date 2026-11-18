"""Battery and power state sensors for the O-KAM camera."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OkamRuntime
from .entity import OkamEntity


# The bridge's power state names, as the wake service reports them.
POWER_STATES = [
    "awake",
    "sleeping",
    "deep_sleep",
    "micro_power",
    "offline",
    "powered_off",
    "low_battery_off",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[OkamRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities([OkamBattery(runtime), OkamPowerState(runtime)])


class OkamBattery(OkamEntity, SensorEntity):
    """The battery level the camera reported in its most recent live session.

    It is read while the camera is awake anyway, so it never causes a wake;
    it updates whenever the camera is viewed or controlled.
    """

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: OkamRuntime) -> None:
        super().__init__(runtime.coordinator)
        self._attr_unique_id = f"{runtime.coordinator.camera_id}_battery"

    @property
    def native_value(self) -> int | None:
        value = self.coordinator.data.get("battery_percent")
        return value if isinstance(value, int) else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        charging = self.coordinator.data.get("charging")
        return {"charging": charging} if isinstance(charging, bool) else {}


class OkamPowerState(OkamEntity, SensorEntity):
    """Whether the camera sleeps, from the wake service, without waking it."""

    _attr_name = "Power state"
    _attr_icon = "mdi:sleep"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = POWER_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime: OkamRuntime) -> None:
        super().__init__(runtime.coordinator)
        self._attr_unique_id = f"{runtime.coordinator.camera_id}_power_state"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("power_state")
        return value if value in POWER_STATES else None
