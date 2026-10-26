"""Media player entity for the Sony Bravia (2011-2012) integration."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import FALLBACK_COMMANDS, BraviaError, is_source_command
from .coordinator import BraviaConfigEntry, BraviaCoordinator
from .entity import BraviaEntity

BASE_FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BraviaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([BraviaMediaPlayer(entry.runtime_data)])


class BraviaMediaPlayer(BraviaEntity, MediaPlayerEntity):
    """The TV as a media player."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV

    def __init__(self, coordinator: BraviaCoordinator) -> None:
        super().__init__(coordinator, "media_player")

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Volume set needs the UPnP RenderingControl service."""
        if self.coordinator.client.rendering_url:
            return BASE_FEATURES | MediaPlayerEntityFeature.VOLUME_SET
        return BASE_FEATURES

    @property
    def state(self) -> MediaPlayerState:
        """On when the TV answers on the network."""
        return (
            MediaPlayerState.ON if self.coordinator.data.is_on else MediaPlayerState.OFF
        )

    @property
    def volume_level(self) -> float | None:
        """Volume 0..1."""
        volume = self.coordinator.data.volume
        return None if volume is None else volume / 100

    @property
    def is_volume_muted(self) -> bool | None:
        """Mute state."""
        return self.coordinator.data.muted

    @property
    def source_list(self) -> list[str]:
        """Input commands from the TV's own command list."""
        commands = self.coordinator.client.commands or FALLBACK_COMMANDS
        return [name for name in commands if is_source_command(name)]

    @property
    def source(self) -> str | None:
        """Best-effort: match a getStatus value against the known inputs."""
        values = {
            v.lower().replace(" ", "") for v in self.coordinator.data.status.values()
        }
        for name in self.source_list:
            if name.lower().replace(" ", "") in values:
                return name
        return None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose raw getStatus fields; their meaning varies by model."""
        return self.coordinator.data.status

    async def async_turn_on(self) -> None:
        """Turn on."""
        await self.coordinator.async_turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self._async_send("PowerOff")

    async def async_volume_up(self) -> None:
        """Volume up."""
        await self._async_send("VolumeUp")

    async def async_volume_down(self) -> None:
        """Volume down."""
        await self._async_send("VolumeDown")

    async def async_set_volume_level(self, volume: float) -> None:
        """Set absolute volume."""
        try:
            await self.coordinator.client.async_set_volume(round(volume * 100))
        except BraviaError as err:
            raise HomeAssistantError(f"Setting volume failed: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute; IRCC "Mute" only toggles, so prefer RenderingControl."""
        if self.coordinator.client.rendering_url:
            try:
                await self.coordinator.client.async_set_mute(mute)
            except BraviaError as err:
                raise HomeAssistantError(f"Setting mute failed: {err}") from err
            await self.coordinator.async_request_refresh()
        elif mute != self.is_volume_muted:
            await self._async_send("Mute")

    async def async_select_source(self, source: str) -> None:
        """Select an input."""
        await self._async_send(source)

    async def async_media_play(self) -> None:
        """Play."""
        await self._async_send("Play")

    async def async_media_pause(self) -> None:
        """Pause."""
        await self._async_send("Pause")

    async def async_media_stop(self) -> None:
        """Stop."""
        await self._async_send("Stop")

    async def async_media_next_track(self) -> None:
        """Next channel."""
        await self._async_send("ChannelUp")

    async def async_media_previous_track(self) -> None:
        """Previous channel."""
        await self._async_send("ChannelDown")
