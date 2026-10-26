"""Config flow for the Sony Bravia (2011-2012) integration.

Registration differs per model generation:
* mode 1: the TV pops up a prompt that has to be accepted with the remote,
* mode 2: the TV must be in registration mode before submitting
  (Settings > Network > Home Network Setup > Remote Device / Renderer > Registration),
* mode 3: the TV shows a 4-digit PIN that is sent back via HTTP basic auth.
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PIN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac

from .api import (
    BraviaAuthError,
    BraviaClient,
    BraviaConnectionError,
    BraviaError,
    BraviaPinRequired,
)
from .const import CLIENT_NICKNAME, CONF_COMMANDS, CONF_DEVICE_ID, DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

MAC_RE = re.compile(r"^([0-9a-fA-F]{2}[:-]?){5}[0-9a-fA-F]{2}$")


def _new_device_id() -> str:
    # Mimics Sony's "Media Remote" app; the TV only checks that the ID is registered.
    mac = "-".join(f"{random.randint(0, 255):02x}" for _ in range(6))
    return f"MediaRemote:{mac}"


class BraviaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: BraviaClient | None = None
        self._data: dict[str, Any] = {}

    def _make_client(self, host: str, device_id: str) -> BraviaClient:
        return BraviaClient(
            session=async_get_clientsession(self.hass),
            host=host,
            device_id=device_id,
            nickname=CLIENT_NICKNAME,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for host, name and (optional) MAC for Wake-on-LAN."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            mac = (user_input.get(CONF_MAC) or "").strip()
            if mac and not MAC_RE.match(mac):
                errors[CONF_MAC] = "invalid_mac"
            else:
                self._async_abort_entries_match({CONF_HOST: host})
                self._data = {
                    CONF_HOST: host,
                    CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                    CONF_DEVICE_ID: _new_device_id(),
                }
                if mac:
                    self._data[CONF_MAC] = format_mac(mac)
                self._client = self._make_client(host, self._data[CONF_DEVICE_ID])
                try:
                    await self._client.async_discover()
                except BraviaConnectionError:
                    errors["base"] = "cannot_connect"
                except BraviaError:
                    _LOGGER.exception("Unexpected response during discovery")
                    errors["base"] = "unknown"
                else:
                    return await self.async_step_register()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): str,
                        vol.Optional(CONF_NAME): str,
                        vol.Optional(CONF_MAC): str,
                    }
                ),
                user_input or {CONF_NAME: DEFAULT_NAME},
            ),
            errors=errors,
        )

    async def async_step_register(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Send the registration request (TV in registration mode / shows prompt)."""
        errors: dict[str, str] = {}
        if user_input is not None or self._client.register_mode != 2:
            try:
                await self._client.async_register()
            except BraviaPinRequired:
                return await self.async_step_pin()
            except BraviaAuthError:
                errors["base"] = "registration_rejected"
            except BraviaConnectionError:
                errors["base"] = "cannot_connect"
            except BraviaError:
                _LOGGER.exception("Unexpected response during registration")
                errors["base"] = "unknown"
            else:
                return await self.async_step_confirm()

        return self.async_show_form(step_id="register", errors=errors)

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the PIN shown on the TV."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._client.pin = str(user_input[CONF_PIN]).strip()
            try:
                await self._client.async_register()
            except BraviaAuthError:
                errors["base"] = "invalid_pin"
            except BraviaConnectionError:
                errors["base"] = "cannot_connect"
            except BraviaError:
                _LOGGER.exception("Unexpected response during PIN registration")
                errors["base"] = "unknown"
            else:
                self._data[CONF_PIN] = self._client.pin
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="pin",
            data_schema=vol.Schema({vol.Required(CONF_PIN): str}),
            errors=errors,
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify registration by reading the command list.

        On mode 1 TVs this fails with 403 until the prompt on screen is accepted,
        in which case the form is shown and the user retries.
        """
        errors: dict[str, str] = {}
        try:
            commands = await self._client.async_update_commands()
        except BraviaAuthError:
            if user_input is not None:
                errors["base"] = "not_confirmed"
            return self.async_show_form(step_id="confirm", errors=errors)
        except BraviaConnectionError:
            errors["base"] = "cannot_connect"
            return self.async_show_form(step_id="confirm", errors=errors)
        except BraviaError as err:
            # Registered, but the list is unavailable; built-in codes will be used.
            _LOGGER.warning("Could not read command list: %s", err)
            commands = {}

        try:
            await self._client.async_get_system_information()
        except BraviaError as err:
            _LOGGER.debug("No system information: %s", err)

        self._data[CONF_COMMANDS] = commands
        if self.source == "reauth":
            entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(
                entry, data={**entry.data, **self._data}
            )

        await self.async_set_unique_id(
            self._client.info.udn or self._data.get(CONF_MAC) or self._data[CONF_HOST]
        )
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._data[CONF_HOST]})
        return self.async_create_entry(
            title=self._data[CONF_NAME] or self._client.info.name or DEFAULT_NAME,
            data=self._data,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Registration was lost (e.g. TV factory reset): register again."""
        self._data = {CONF_DEVICE_ID: _new_device_id(), CONF_PIN: None}
        self._client = self._make_client(
            entry_data[CONF_HOST], self._data[CONF_DEVICE_ID]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start re-registration once the user is at the TV."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._client.async_discover()
            except BraviaError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_register(user_input)
        return self.async_show_form(step_id="reauth_confirm", errors=errors)
