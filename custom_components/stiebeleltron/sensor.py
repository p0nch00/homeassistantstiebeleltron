from __future__ import annotations

from typing import Any

from pystiebeleltron import RegisterType
from pystiebeleltron.wpm import WpmStiebelEltronAPI

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity_base import SteContext, ste_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    api: WpmStiebelEltronAPI = entry.runtime_data["api"]
    coordinator = entry.runtime_data["coordinator"]

    ctx = SteContext(
        api=api,
        coordinator=coordinator,
        entry_id=entry.entry_id,
        title=entry.title,
        host=entry.data["host"],
    )

    entities: list[SteRegisterSensor] = []

    for block in api._register_blocks:  # library internal, but stable in 0.2.5
        if block.register_type == RegisterType.INPUT_REGISTER:
            for key, reg in block.registers.items():
                entities.append(SteRegisterSensor(ctx, block.name, "in", key, reg))

        # Holding registers *without* bounds will be exposed read-only here
        if block.register_type == RegisterType.HOLDING_REGISTER:
            for key, reg in block.registers.items():
                if reg.min is None or reg.max is None:
                    entities.append(SteRegisterSensor(ctx, block.name, "hold_ro", key, reg))

    async_add_entities(entities, True)


class SteRegisterSensor(CoordinatorEntity, SensorEntity):
    """Sensor for one modbus register."""

    def __init__(self, ctx: SteContext, block_name: str, kind: str, reg_key: Any, reg) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._reg_key = reg_key
        self._reg = reg

        self._attr_device_info = ste_device_info(ctx)
        from .entity_base import extract_hp_number

        hp = extract_hp_number(reg.name)
        if hp:
            self._attr_name = f"{ctx.title} {block_name} HP{hp} {reg.name}"
        else:
            self._attr_name = f"{ctx.title} {block_name} {reg.name}"
        self._attr_unique_id = f"{ctx.entry_id}:{kind}:{reg.address}"

        if reg.unit:
            self._attr_native_unit_of_measurement = reg.unit

    @property
    def native_value(self) -> Any:
        try:
            return self._ctx.api.get_register_value(self._reg_key)
        except Exception:
            return None
