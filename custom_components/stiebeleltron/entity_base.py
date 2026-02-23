from __future__ import annotations

from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


@dataclass(frozen=True)
class SteContext:
    api: object
    coordinator: object
    entry_id: str
    title: str
    host: str


def ste_device_info(ctx: SteContext) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, ctx.entry_id)},
        name=ctx.title,
        manufacturer="STIEBEL ELTRON",
        model="WPM / ISGWeb (Modbus)",
        configuration_url=f"http://{ctx.host}",
    )

import re


def extract_hp_number(register_name: str) -> str | None:
    """
    Extract heat pump number from register name.
    Matches:
        *_HP1
        *_HP_1
        COMPRESSOR_1
    """
    # Match HP1 or HP_1
    m = re.search(r"HP_?(\d+)", register_name)
    if m:
        return m.group(1)

    # Match COMPRESSOR_1 etc
    m = re.search(r"COMPRESSOR_(\d+)", register_name)
    if m:
        return m.group(1)

    return None
