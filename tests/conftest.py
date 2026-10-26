"""Shared fixtures."""

import pytest


@pytest.fixture
def allow_sockets(request):
    """Enable real sockets when pytest-homeassistant-custom-component blocks them."""
    try:
        request.getfixturevalue("socket_enabled")
    except pytest.FixtureLookupError:
        pass
