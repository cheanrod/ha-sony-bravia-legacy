"""Remote entity for the Sony Bravia (2011-2012) integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import FALLBACK_COMMANDS
from .coordinator import BraviaConfigEntry, BraviaCoordinator
from .entity import BraviaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BraviaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the remote."""
    async_add_entities([BraviaRemote(entry.runtime_data)])


class BraviaRemote(BraviaEntity, RemoteEntity):
    """Sends any IRCC command the TV knows (see the ``commands`` attribute)."""

    _attr_translation_key = "remote"

    def __init__(self, coordinator: BraviaCoordinator) -> None:
        super().__init__(coordinator, "remote")

    @property
    def is_on(self) -> bool:
        """On when the TV answers on the network."""
        return self.coordinator.data.is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the command names usable with remote.send_command."""
        return {
            "commands": sorted(self.coordinator.client.commands or FALLBACK_COMMANDS)
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        await self.coordinator.async_turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self._async_send("PowerOff")

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send commands by name (e.g. "Hdmi1") or raw IRCC code."""
        repeats = kwargs.get(ATTR_NUM_REPEATS, 1)
        delay = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        commands = list(command)
        for i in range(repeats):
            for j, cmd in enumerate(commands):
                if i or j:
                    await asyncio.sleep(delay)
                await self._async_send(cmd)
