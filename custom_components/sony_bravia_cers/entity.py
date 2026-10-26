"""Base entity for the Sony Bravia (2011-2012) integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import BraviaError
from .const import DOMAIN
from .coordinator import BraviaCoordinator


class BraviaEntity(CoordinatorEntity[BraviaCoordinator]):
    """Common device info and command helper."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BraviaCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        info = coordinator.client.info
        base_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            connections=(
                {(CONNECTION_NETWORK_MAC, entry.data[CONF_MAC])}
                if entry.data.get(CONF_MAC)
                else set()
            ),
            manufacturer="Sony",
            model=info.model,
            name=entry.data.get(CONF_NAME) or info.name,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    async def _async_send(self, *commands: str) -> None:
        """Send IRCC commands, then refresh state."""
        try:
            for command in commands:
                await self.coordinator.client.async_send_command(command)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        except BraviaError as err:
            raise HomeAssistantError(f"Sending {commands} failed: {err}") from err
        await self.coordinator.async_request_refresh()
