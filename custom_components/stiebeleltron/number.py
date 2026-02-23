from __future__ import annotations

from typing import Any

from pystiebeleltron import RegisterType
from pystiebeleltron.wpm import WpmStiebelEltronAPI

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity_base import SteContext, ste_device_info


def _step_for(reg) -> float:
    # temps/decimals in this lib tend to be data_type 2 or 7 -> 0.1 works well
    if getattr(reg, "data_type", None) in (2, 7):
        return 0.1
    return 1.0


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

    entities: list[SteRegisterNumber] = []

    for block in api._register_blocks:
        if block.register_type != RegisterType.HOLDING_REGISTER:
            continue

        for key, reg in block.registers.items():
            if reg.min is None or reg.max is None:
                continue
            # boolean-like -> handled by switch.py
            if reg.min == 0 and reg.max == 1:
                continue
            entities.append(SteRegisterNumber(ctx, block.name, key, reg))

    async_add_entities(entities, True)


class SteRegisterNumber(CoordinatorEntity, NumberEntity):
    """Holding register as NumberEntity."""

    def __init__(self, ctx: SteContext, block_name: str, reg_key: Any, reg) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._reg_key = reg_key
        self._reg = reg

        self._attr_device_info = ste_device_info(ctx)
        self._attr_name = f"{ctx.title} {block_name} {reg.name}"
        self._attr_unique_id = f"{ctx.entry_id}:hold:number:{reg.address}"

        self._attr_native_min_value = float(reg.min)
        self._attr_native_max_value = float(reg.max)
        self._attr_native_step = _step_for(reg)
        if reg.unit:
            self._attr_native_unit_of_measurement = reg.unit

    @property
    def native_value(self) -> Any:
        try:
            return self._ctx.api.get_register_value(self._reg_key)
        except Exception:
            return None

    async def async_set_native_value(self, value: float) -> None:
        res = self._ctx.api.set_register_value(self._reg_key, value)
        if hasattr(res, "__await__"):
            await res
        await self._ctx.coordinator.async_request_refresh()
