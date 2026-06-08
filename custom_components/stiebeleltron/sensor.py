from __future__ import annotations

from typing import Any

from .pystiebeleltron import RegisterType
from .pystiebeleltron.wpm import WpmStiebelEltronAPI
from .pystiebeleltron.web import INFO_PAGES, WebStiebelEltronCoolingAPI

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
                if reg.min == 0 and reg.max == 1:
                    entities.append(SteRegisterSensor(ctx, block.name, "hold_ro", key, reg))

    async_add_entities(entities, True)

    web_api: WebStiebelEltronCoolingAPI | None = entry.runtime_data.get("web_api")
    web_coordinator = entry.runtime_data.get("web_coordinator")
    if web_api is not None and web_coordinator is not None:
        web_ctx = SteContext(
            api=web_api,
            coordinator=web_coordinator,
            entry_id=entry.entry_id,
            title=entry.title,
            host=entry.data["host"],
        )
        web_sensors = []
        for info_page in INFO_PAGES:
            for key in web_api.get_info_page_keys(info_page.key_prefix):
                # key format: "{prefix}:{section}:{label}" — drop prefix, join rest with space
                label = " ".join(key.split(":")[1:])
                unit = web_api.get_info_unit(key)
                web_sensors.append(WebHeatPumpSensor(web_ctx, info_page, key, label, unit))
        async_add_entities(web_sensors, True)


class SteRegisterSensor(CoordinatorEntity, SensorEntity):
    """Sensor for one modbus register."""

    def __init__(self, ctx: SteContext, block_name: str, kind: str, reg_key: Any, reg) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._reg_key = reg_key
        self._reg = reg

        self._attr_device_info = ste_device_info(ctx)
        from .entity_base import extract_hp_number

        hp = extract_hp_number(reg_key)
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


class WebHeatPumpSensor(CoordinatorEntity, SensorEntity):
    """Read-only sensor for one field from an ISGWeb heat pump info page."""

    def __init__(self, ctx: SteContext, page, key: str, label: str, unit: str) -> None:
        super().__init__(ctx.coordinator)
        self._ctx = ctx
        self._key = key

        self._attr_device_info = ste_device_info(ctx)
        self._attr_name = f"{ctx.title} {page.name} {label}"
        self._attr_unique_id = f"{ctx.entry_id}:web:info:{key}"
        if unit:
            self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self) -> float | None:
        return self._ctx.api.get_register_value(self._key)
