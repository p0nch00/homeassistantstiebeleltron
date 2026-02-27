from __future__ import annotations

from typing import Any

from .pystiebeleltron import RegisterType
from .pystiebeleltron.wpm import WpmStiebelEltronAPI, WpmSystemParametersRegisters

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity_base import SteContext, ste_device_info


REGISTER_VALUE_LABELS: dict[Any, dict[int, str]] = {
    # WpmSystemParametersRegisters.OPERATING_MODE: 0..5
    WpmSystemParametersRegisters.OPERATING_MODE: {
        0: "Automatic",
        1: "Manual",
        2: "Standby",
        3: "Day mode",
        4: "Setback mode",
        5: "DHW",
    },
}

DENY_SELECT = {
    WpmSystemParametersRegisters.RESET,
    WpmSystemParametersRegisters.RESTART_ISG,
}



def _range_options(reg) -> list[str]:
    """Build numeric options for min..max inclusive."""
    return [str(i) for i in range(int(reg.min), int(reg.max) + 1)]


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

    entities: list[SteRegisterSelect] = []

    for block in api._register_blocks:
        if block.register_type != RegisterType.HOLDING_REGISTER:
            continue

        for key, reg in block.registers.items():

            if key in DENY_SELECT:
                continue

            if getattr(reg, "data_type", None) != 8:
                continue
            if reg.min is None or reg.max is None:
                continue

            # Avoid turning huge ranges into select dropdowns
            if (int(reg.max) - int(reg.min)) > 30:
                continue

            entities.append(SteRegisterSelect(ctx, block.name, key, reg))

    async_add_entities(entities, True)


class SteRegisterSelect(CoordinatorEntity, SelectEntity):
    """Holding register enum as SelectEntity."""

    def __init__(self, ctx: SteContext, block_name: str, reg_key: Any, reg) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._reg_key = reg_key
        self._reg = reg

        self._attr_device_info = ste_device_info(ctx)
        self._attr_name = f"{ctx.title} {block_name} {reg.name}"
        self._attr_unique_id = f"{ctx.entry_id}:hold:select:{reg.address}"

        # If we have pretty labels for this register, use them; else numeric
        labels = REGISTER_VALUE_LABELS.get(reg_key)
        if labels:
            # Ensure stable ordering by numeric key
            self._labels = {int(k): str(v) for k, v in labels.items()}
            self._reverse = {v: k for k, v in self._labels.items()}
            self._attr_options = list(self._labels.values())
        else:
            self._labels = {}
            self._reverse = {}
            self._attr_options = _range_options(reg)

    @property
    def current_option(self) -> str | None:
        try:
            v = self._ctx.api.get_register_value(self._reg_key)
        except Exception:
            return None
        if v is None:
            return None

        try:
            vi = int(v)
        except (TypeError, ValueError):
            return None

        if self._labels:
            return self._labels.get(vi, str(vi))
        return str(vi)

    async def async_select_option(self, option: str) -> None:
        # Convert selected option -> numeric value
        if self._reverse:
            value = self._reverse.get(option)
            if value is None:
                value = int(option)
        else:
            value = int(option)

        res = self._ctx.api.write_register_value(self._reg_key, value)
        if hasattr(res, "__await__"):
            await res
        await self._ctx.coordinator.async_request_refresh()
