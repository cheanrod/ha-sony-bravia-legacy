"""Home Assistant level tests: config flow, setup and entities against the fake TV."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestServer
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sony_bravia_cers.const import (
    CONF_COMMANDS,
    CONF_DEVICE_ID,
    DOMAIN,
)
from tests.fake_tv import DEVICE_ID, FakeTv


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components."""


@pytest.fixture
async def tv(request, socket_enabled):
    fake = FakeTv(**getattr(request, "param", {}))
    server = TestServer(fake.app, host="127.0.0.1")
    await server.start_server()
    fake.base = f"http://127.0.0.1:{server.port}"
    fake.host = f"127.0.0.1:{server.port}"
    yield fake
    await server.close()


@pytest.mark.parametrize("tv", [{"prompt": True}], indirect=True)
async def test_flow_with_on_screen_confirmation(
    hass: HomeAssistant, tv: FakeTv
) -> None:
    """Mode 1: TV must accept the prompt; until then the confirm step repeats."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: tv.host, CONF_NAME: "Living room"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "not_confirmed"}

    tv.accept()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living room"
    assert result["result"].unique_id == "uuid:1234"
    assert result["data"][CONF_DEVICE_ID].startswith("MediaRemote:")
    assert result["data"][CONF_COMMANDS]["HDMI1"] == "AAAAAgAAABoAAABaAw=="


@pytest.mark.parametrize("tv", [{"register_mode": 3, "pin": "4711"}], indirect=True)
async def test_flow_with_pin(hass: HomeAssistant, tv: FakeTv) -> None:
    """Mode 3: TV answers 401 and shows a PIN."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: tv.host}
    )
    assert result["step_id"] == "pin"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "0000"}
    )
    assert result["errors"] == {"base": "invalid_pin"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PIN: "4711"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PIN] == "4711"


async def test_flow_cannot_connect(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1:1"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities(hass: HomeAssistant, tv: FakeTv) -> None:
    tv.registered.add(DEVICE_ID)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="uuid:1234",
        data={CONF_HOST: tv.host, CONF_NAME: "Bravia", CONF_DEVICE_ID: DEVICE_ID},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Command list was fetched and persisted on first refresh.
    assert entry.data[CONF_COMMANDS]["Tv"] == "AAAAAQAAAAEAAAAkAw=="

    state = hass.states.get("media_player.bravia")
    assert state.state == "on"
    assert state.attributes["source_list"] == ["HDMI1", "Tv"]
    assert state.attributes["source"] == "HDMI1"

    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": "media_player.bravia", "source": "Tv"},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player", "volume_up", {"entity_id": "media_player.bravia"}, blocking=True
    )
    await hass.services.async_call(
        "remote",
        "send_command",
        {"entity_id": "remote.bravia_remote", "command": ["Num1", "Confirm"]},
        blocking=True,
    )
    assert tv.ircc == [
        "AAAAAQAAAAEAAAAkAw==",  # Tv (from TV's list)
        "AAAAAQAAAAEAAAASAw==",  # VolumeUp (fallback table)
        "AAAAAQAAAAEAAAAAAw==",  # Num1
        "AAAAAQAAAAEAAABlAw==",  # Confirm
    ]
    assert "Power" in hass.states.get("remote.bravia_remote").attributes["commands"]


async def test_unregistered_triggers_reauth(hass: HomeAssistant, tv: FakeTv) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: tv.host, CONF_DEVICE_ID: "MediaRemote:unknown"},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]
