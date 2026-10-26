"""Tests for the CERS/IRCC client against a simulated 2012 Bravia.

Runs without Home Assistant installed: api.py only depends on aiohttp.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

_API = pathlib.Path(__file__).parents[1] / "custom_components/sony_bravia_cers/api.py"
_spec = importlib.util.spec_from_file_location("bravia_api", _API)
api = importlib.util.module_from_spec(_spec)
sys.modules["bravia_api"] = api
_spec.loader.exec_module(api)

from tests.fake_tv import DEVICE_ID, FakeTv  # noqa: E402


@pytest.fixture
async def tv_and_client(request, allow_sockets):
    tv = FakeTv(**getattr(request, "param", {}))
    server = TestServer(tv.app, host="127.0.0.1")
    await server.start_server()
    tv.base = f"http://127.0.0.1:{server.port}"
    async with ClientSession() as session:
        client = api.BraviaClient(session, f"127.0.0.1:{server.port}", DEVICE_ID)
        yield tv, client
    await server.close()


def test_ircc_code_matches_known_codes():
    assert api.ircc_code(1, 1, 18) == "AAAAAQAAAAEAAAASAw=="  # VolumeUp
    assert api.ircc_code(2, 26, 90) == "AAAAAgAAABoAAABaAw=="  # HDMI1
    assert api.FALLBACK_COMMANDS["Num0"] == "AAAAAQAAAAEAAAAJAw=="
    assert api.FALLBACK_COMMANDS["Num1"] == "AAAAAQAAAAEAAAAAAw=="


def test_source_detection():
    assert all(
        map(api.is_source_command, ["HDMI1", "Hdmi 2", "Tv", "Component1", "Video2"])
    )
    assert not any(map(api.is_source_command, ["Power", "Netflix", "Input"]))


async def test_full_flow(tv_and_client):
    tv, client = tv_and_client
    await client.async_discover()
    assert client.ircc_url == f"{tv.base}/sony/IRCC"
    assert client.info.udn == "uuid:1234"
    assert client.info.model == "KDL-46HX750"
    assert client.register_mode == 1

    with pytest.raises(api.BraviaAuthError):
        await client.async_update_commands()

    await client.async_register()
    commands = await client.async_update_commands()
    assert commands == {
        "Power": "AAAAAQAAAAEAAAAVAw==",
        "HDMI1": "AAAAAgAAABoAAABaAw==",
        "Tv": "AAAAAQAAAAEAAAAkAw==",
    }
    assert await client.async_get_status() == {"viewing.source": "HDMI1"}

    await client.async_send_command("hdmi1")  # case-insensitive, from TV list
    await client.async_send_command("VolumeUp")  # from fallback table
    assert tv.ircc == ["AAAAAgAAABoAAABaAw==", "AAAAAQAAAAEAAAASAw=="]
    with pytest.raises(ValueError):
        client.code_for("Nonsense")


@pytest.mark.parametrize(
    "tv_and_client", [{"register_mode": 3, "pin": "1234"}], indirect=True
)
async def test_pin_registration(tv_and_client):
    tv, client = tv_and_client
    await client.async_discover()
    assert client.register_mode == 3
    with pytest.raises(api.BraviaPinRequired):
        await client.async_register()
    client.pin = "1234"
    await client.async_register()
    assert DEVICE_ID in tv.registered


@pytest.mark.parametrize(
    "tv_and_client", [{"description": False, "register_mode": 3}], indirect=True
)
async def test_action_list_at_default_path(tv_and_client):
    """Without a UPnP description, /cers/ActionList.xml supplies mode and URLs."""
    tv, client = tv_and_client
    await client.async_discover()
    assert client.register_mode == 3
    assert client.actions["register"] == f"{tv.base}/cers/api/register"
    await client.async_register()
    assert DEVICE_ID in tv.registered


async def test_unreachable_is_connection_error(allow_sockets):
    async with ClientSession() as session:
        client = api.BraviaClient(session, "127.0.0.1:1", DEVICE_ID)
        with pytest.raises(api.BraviaConnectionError):
            await client.async_discover()
        with pytest.raises(api.BraviaConnectionError):
            await client.async_get_status()
