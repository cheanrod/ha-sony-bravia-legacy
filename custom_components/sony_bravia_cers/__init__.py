"""Sony Bravia (2011-2012, CERS/IRCC API) integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PIN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BraviaClient
from .const import CLIENT_NICKNAME, CONF_COMMANDS, CONF_DEVICE_ID
from .coordinator import BraviaConfigEntry, BraviaCoordinator

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.REMOTE]


async def async_setup_entry(hass: HomeAssistant, entry: BraviaConfigEntry) -> bool:
    """Set up the TV from a config entry."""
    client = BraviaClient(
        session=async_get_clientsession(hass),
        host=entry.data[CONF_HOST],
        device_id=entry.data[CONF_DEVICE_ID],
        nickname=CLIENT_NICKNAME,
        pin=entry.data.get(CONF_PIN),
    )
    client.commands = dict(entry.data.get(CONF_COMMANDS) or {})

    coordinator = BraviaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BraviaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
