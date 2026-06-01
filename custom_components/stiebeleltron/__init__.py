"""STIEBEL ELTRON integration (WPM / ISGWeb Modbus register dump)."""

from __future__ import annotations

import logging
from datetime import timedelta

from .pystiebeleltron.wpm import WpmStiebelEltronAPI
from .pystiebeleltron.web import WebStiebelEltronCoolingAPI

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_WEB_PASSWORD, CONF_WEB_USERNAME, DOMAIN

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
        "web_api": None,
        "web_coordinator": None,
    }

    web_username = entry.data.get(CONF_WEB_USERNAME, "").strip()
    web_password = entry.data.get(CONF_WEB_PASSWORD, "").strip()
    if web_username and web_password:
        web_api = WebStiebelEltronCoolingAPI(host, web_username, web_password)
        try:
            auth_ok = await web_api.connect()
            if auth_ok:
                await web_api.async_update()

                async def _async_update_web() -> None:
                    try:
                        await web_api.async_update()
                    except Exception as err:
                        raise UpdateFailed(str(err)) from err

                web_coordinator = DataUpdateCoordinator(
                    hass,
                    _LOGGER,
                    name=f"{entry.title} Web Cooling",
                    update_method=_async_update_web,
                    update_interval=timedelta(seconds=60),
                )
                await web_coordinator.async_config_entry_first_refresh()
                entry.runtime_data["web_api"] = web_api
                entry.runtime_data["web_coordinator"] = web_coordinator
            else:
                _LOGGER.warning("Web portal authentication failed for %s, web cooling entities disabled", host)
                await web_api.close()
        except Exception as err:
            _LOGGER.warning("Web portal setup failed for %s: %s", host, err)
            try:
                await web_api.close()
            except Exception:
                pass

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StiebelEltronConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    api: WpmStiebelEltronAPI = entry.runtime_data["api"]
    try:
        await api.close()
    except Exception:
        _LOGGER.debug("Error closing Modbus connection", exc_info=True)

    web_api: WebStiebelEltronCoolingAPI | None = entry.runtime_data.get("web_api")
    if web_api is not None:
        try:
            await web_api.close()
        except Exception:
            _LOGGER.debug("Error closing web API connection", exc_info=True)

    return unload_ok
