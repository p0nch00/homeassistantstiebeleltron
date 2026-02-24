from __future__ import annotations

from typing import Any

from .pystiebeleltron import RegisterType
from .pystiebeleltron.wpm import WpmStiebelEltronAPI

from homeassistant.components.switch import SwitchEntity
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

    entities: list[SteRegisterSwitch] = []

    for block in api._register_blocks:
        if block.register_type != RegisterType.HOLDING_REGISTER:
            continue
        for key, reg in block.registers.items():
            if reg.min == 0 and reg.max == 1:
                entities.append(SteRegisterSwitch(ctx, block.name, key, reg))

    async_add_entities(entities, True)


class SteRegisterSwitch(CoordinatorEntity, SwitchEntity):
    """Holding register 0/1 as SwitchEntity."""

    def __init__(self, ctx: SteContext, block_name: str, reg_key: Any, reg) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._reg_key = reg_key
        self._reg = reg

        self._attr_device_info = ste_device_info(ctx)
        self._attr_name = f"{ctx.title} {block_name} {reg.name}"
        self._attr_unique_id = f"{ctx.entry_id}:hold:switch:{reg.address}"

    @property
    def is_on(self) -> bool | None:
        try:
            v = self._ctx.api.get_register_value(self._reg_key)
        except Exception:
            return None
        return v == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        res = self._ctx.api.write_register_value(self._reg_key, 1)
        if hasattr(res, "__await__"):
            await res
        await self._ctx.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        res = self._ctx.api.write_register_value(self._reg_key, 0)
        if hasattr(res, "__await__"):
            await res
        await self._ctx.coordinator.async_request_refresh()
