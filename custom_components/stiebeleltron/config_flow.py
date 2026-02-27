"""Config flow for the STIEBEL ELTRON integration."""

from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from .pystiebeleltron.wpm import WpmStiebelEltronAPI

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _normalize_host(raw: str) -> str:
    """Normalize user input into a hostname/IP (strip http(s)://, paths, etc.)."""
    raw = (raw or "").strip()

    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.hostname:
            return parsed.hostname

    raw = raw.removeprefix("http://").removeprefix("https://")

    raw = raw.split("/")[0].strip()

    return raw


def _is_valid_host(host: str) -> bool:
    """Basic validation: accept IPs and hostnames."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    return all(part and " " not in part for part in host.split("."))


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class StiebelEltronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for STIEBEL ELTRON (WPM)."""

    VERSION = 1

    async def _async_validate_input(self, host: str, port: int) -> None:
        """Validate the user input by connecting to the device.

        NOTE: WpmStiebelEltronAPI currently takes only host in your example.
        If the library supports port in your version, you can pass it too.
        """
        api = WpmStiebelEltronAPI(host)
        try:
            await api.connect()
            await api.async_update()
        except Exception as err:
            _LOGGER.debug("Connection test failed (%s:%s): %s", host, port, err, exc_info=True)
            raise CannotConnect from err
        finally:
            try:
                await api.close()
            except Exception:
                pass

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

            if not _is_valid_host(host):
                errors["base"] = "invalid_host"
            else:
                # Prevent duplicates (use normalized host)
                self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

                try:
                    await self._async_validate_input(host, port)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected exception during config flow")
                    errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(
                    title="Stiebel Eltron",
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Handle YAML import."""
        host = _normalize_host(user_input[CONF_HOST])
        port = int(user_input.get(CONF_PORT, DEFAULT_PORT))

        self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})

        if not _is_valid_host(host):
            return self.async_abort(reason="invalid_host")

        try:
            await self._async_validate_input(host, port)
        except CannotConnect:
            return self.async_abort(reason="cannot_connect")
        except Exception:
            _LOGGER.exception("Unexpected exception during import")
            return self.async_abort(reason="unknown")

        title = user_input.get(CONF_NAME) or "Stiebel Eltron"

        return self.async_create_entry(
            title=title,
            data={CONF_HOST: host, CONF_PORT: port},
        )
