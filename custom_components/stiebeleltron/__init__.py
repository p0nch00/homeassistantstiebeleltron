"""STIEBEL ELTRON integration (WPM / ISGWeb Modbus register dump)."""

from __future__ import annotations

import logging
from datetime import timedelta

from .pystiebeleltron.wpm import WpmStiebelEltronAPI

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SELECT,
]

type StiebelEltronConfigEntry = ConfigEntry[dict]


async def async_setup_entry(hass: HomeAssistant, entry: StiebelEltronConfigEntry) -> bool:
    """Set up STIEBEL ELTRON from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, 502)

    api = WpmStiebelEltronAPI(host, port=port, device_id=1)

    try:
        await api.connect()
        await api.async_update()
    except Exception as err:
        _LOGGER.debug("Failed to connect/update (%s:%s): %s", host, port, err, exc_info=True)
        try:
            await api.close()
        except Exception:
            pass
        raise ConfigEntryNotReady("Could not connect to device") from err

    async def _async_update() -> None:
        try:
            await api.async_update()
        except Exception as err:
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{entry.title} WPM",
        update_method=_async_update,
        update_interval=timedelta(seconds=30),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StiebelEltronConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    api: WpmStiebelEltronAPI = entry.runtime_data["api"]
    try:
        await api.close()
    except Exception:
        _LOGGER.debug("Error closing connection", exc_info=True)

    return unload_ok
