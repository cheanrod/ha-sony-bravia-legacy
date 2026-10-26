"""Polling coordinator for the Sony Bravia (2011-2012) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    BraviaAuthError,
    BraviaClient,
    BraviaConnectionError,
    BraviaError,
    send_magic_packet,
)
from .const import CONF_COMMANDS, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type BraviaConfigEntry = ConfigEntry[BraviaCoordinator]


@dataclass
class BraviaState:
    """Snapshot of the TV state."""

    is_on: bool = False
    status: dict[str, str] = field(default_factory=dict)
    volume: int | None = None
    muted: bool | None = None


class BraviaCoordinator(DataUpdateCoordinator[BraviaState]):
    """Polls the TV.

    2012 sets drop off the network in standby, so "unreachable" means "off"
    rather than an update failure.
    """

    config_entry: BraviaConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: BraviaConfigEntry, client: BraviaClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> BraviaState:
        client = self.client
        try:
            if not client.discovered:
                await client.async_discover()
            status = await client.async_get_status()
        except BraviaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BraviaConnectionError:
            return BraviaState(is_on=False)
        except BraviaError as err:
            # getStatus is optional; reaching the TV at all means it is on.
            _LOGGER.debug("getStatus failed: %s", err)
            status = {}

        state = BraviaState(is_on=True, status=status)
        try:
            state.volume = await client.async_get_volume()
            state.muted = await client.async_get_mute()
        except BraviaError as err:
            _LOGGER.debug("Volume query failed: %s", err)

        if not self.config_entry.data.get(CONF_COMMANDS):
            await self._async_fetch_commands()
        return state

    async def _async_fetch_commands(self) -> None:
        """Fetch and persist the command list so sources survive the TV being off."""
        try:
            commands = await self.client.async_update_commands()
        except BraviaError as err:
            _LOGGER.debug("Could not fetch command list: %s", err)
            return
        if commands:
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_COMMANDS: commands},
            )

    async def async_turn_on(self) -> None:
        """Wake the TV via WoL (if a MAC is set) and the IRCC power code."""
        if mac := self.config_entry.data.get(CONF_MAC):
            await self.hass.async_add_executor_job(send_magic_packet, mac)
        try:
            await self.client.async_send_command("Power")
        except BraviaConnectionError:
            if not mac:
                _LOGGER.warning(
                    "TV is unreachable in standby; configure its MAC address and "
                    "enable remote start / Wake-on-LAN on the TV to turn it on"
                )

    async def async_turn_off(self) -> None:
        """Send the discrete power-off code."""
        await self.client.async_send_command("PowerOff")
