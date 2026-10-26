"""Async client for the CERS/IRCC remote API of 2011-2012 Sony Bravia TVs.

These sets predate the JSON "ScalarWebAPI" (/sony/...) used by 2013+ models and by
Home Assistant's built-in ``braviatv`` integration. They expose instead:

* a UPnP IRCC description with an "action list" (register, getRemoteCommandList,
  getStatus, getSystemInformation, ...), usually served under ``/cers/``,
* a SOAP endpoint accepting ``X_SendIRCC`` with base64 IR codes,
* a UPnP MediaRenderer (port 52323) whose RenderingControl reports/sets volume.

Every request must carry an ``X-CERS-DEVICE-ID`` header naming a registered client.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import socket
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlencode, urljoin

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=5)
# Mode 1 registration may hold the request open until the prompt on the TV is answered.
REGISTER_TIMEOUT = aiohttp.ClientTimeout(total=60)

DMR_PORT = 52323
IRCC_DESCRIPTION_CANDIDATES = (
    "http://{host}:50001/Ircc.xml",
    "http://{host}/cers/Ircc.xml",
    "http://{host}:{dmr_port}/dmr.xml",
)
# Where older sets serve the action list when no description advertises it.
DEFAULT_ACTION_LIST = "http://{host}/cers/ActionList.xml"
DEFAULT_ACTIONS = {
    "register": "http://{host}/cers/api/register",
    "getRemoteCommandList": "http://{host}/cers/api/getRemoteCommandList",
    "getStatus": "http://{host}/cers/api/getStatus",
    "getSystemInformation": "http://{host}/cers/api/getSystemInformation",
}
DEFAULT_IRCC_PATH = "/IRCC"

IRCC_SERVICE = "urn:schemas-sony-com:service:IRCC:1"
RENDERING_SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"


def ircc_code(category: int, device: int, function: int) -> str:
    """Encode a Sony IR command (category, device, function) as an IRCC code."""
    return base64.b64encode(
        struct.pack(">IIIB", category, device, function, 3)
    ).decode()


# Used when the TV refuses or fails to return its own command list.
FALLBACK_COMMANDS: dict[str, str] = {
    "Power": ircc_code(1, 1, 21),
    "PowerOff": ircc_code(1, 1, 47),
    "VolumeUp": ircc_code(1, 1, 18),
    "VolumeDown": ircc_code(1, 1, 19),
    "Mute": ircc_code(1, 1, 20),
    "ChannelUp": ircc_code(1, 1, 16),
    "ChannelDown": ircc_code(1, 1, 17),
    "Input": ircc_code(1, 1, 37),
    "Tv": ircc_code(1, 1, 36),
    "Display": ircc_code(1, 1, 58),
    "Home": ircc_code(1, 1, 96),
    "Confirm": ircc_code(1, 1, 101),
    "Up": ircc_code(1, 1, 116),
    "Down": ircc_code(1, 1, 117),
    "Left": ircc_code(1, 1, 52),
    "Right": ircc_code(1, 1, 51),
    "Return": ircc_code(2, 151, 35),
    "Options": ircc_code(2, 151, 54),
    "Play": ircc_code(2, 151, 26),
    "Pause": ircc_code(2, 151, 25),
    "Stop": ircc_code(2, 151, 24),
    "Next": ircc_code(2, 151, 61),
    "Prev": ircc_code(2, 151, 60),
    "Red": ircc_code(2, 151, 37),
    "Green": ircc_code(2, 151, 38),
    "Yellow": ircc_code(2, 151, 39),
    "Blue": ircc_code(2, 151, 36),
    "Hdmi1": ircc_code(2, 26, 90),
    "Hdmi2": ircc_code(2, 26, 91),
    "Hdmi3": ircc_code(2, 26, 92),
    "Hdmi4": ircc_code(2, 26, 93),
    **{f"Num{n}": ircc_code(1, 1, (n - 1) % 10) for n in range(10)},
}

_SOURCE_RE = re.compile(
    r"^(tv|digital|analog|hdmi\s*\d*|component\s*\d*|video\s*\d*|pc|scart\s*\d*|av\s*\d*)$",
    re.IGNORECASE,
)


def is_source_command(name: str) -> bool:
    """Return True if a command name selects an input."""
    return bool(_SOURCE_RE.match(name))


class BraviaError(Exception):
    """Base error."""


class BraviaConnectionError(BraviaError):
    """TV unreachable (usually: switched off)."""


class BraviaAuthError(BraviaError):
    """TV rejected the client (not registered, or registration not confirmed)."""


class BraviaPinRequired(BraviaAuthError):
    """TV shows a PIN that has to be sent back via HTTP basic auth."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_text(root: ET.Element, name: str) -> str | None:
    for elem in root.iter():
        if _local(elem.tag) == name and elem.text:
            return elem.text.strip()
    return None


def _services(root: ET.Element, base: str) -> dict[str, str]:
    """Map UPnP serviceType -> absolute controlURL."""
    result = {}
    for elem in root.iter():
        if _local(elem.tag) != "service":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in elem}
        if fields.get("serviceType") and fields.get("controlURL"):
            result[fields["serviceType"]] = urljoin(base, fields["controlURL"])
    return result


@dataclass
class SystemInfo:
    """Static device information."""

    name: str | None = None
    model: str | None = None
    udn: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class BraviaClient:
    """Client for one TV."""

    session: aiohttp.ClientSession
    host: str
    device_id: str
    nickname: str = "Home Assistant"
    pin: str | None = None

    register_mode: int = 1
    actions: dict[str, str] = field(default_factory=dict)
    ircc_url: str = ""
    rendering_url: str | None = None
    commands: dict[str, str] = field(default_factory=dict)
    info: SystemInfo = field(default_factory=SystemInfo)
    discovered: bool = False

    def __post_init__(self) -> None:
        self.ircc_url = f"http://{self.host}{DEFAULT_IRCC_PATH}"
        self.actions = {k: v.format(host=self.host) for k, v in DEFAULT_ACTIONS.items()}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-CERS-DEVICE-ID": self.device_id,
            "X-CERS-DEVICE-INFO": self.device_id,
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: aiohttp.ClientTimeout = TIMEOUT,
        cers: bool = True,
    ) -> str:
        all_headers = (
            {**self._headers, **(headers or {})} if cers else dict(headers or {})
        )
        if self.pin and cers:
            token = base64.b64encode(f":{self.pin}".encode()).decode()
            all_headers["Authorization"] = f"Basic {token}"
        try:
            async with self.session.request(
                method, url, data=data, headers=all_headers, timeout=timeout
            ) as resp:
                text = await resp.text(errors="replace")
                if resp.status == 401:
                    raise BraviaPinRequired(f"{url}: 401")
                if resp.status == 403:
                    raise BraviaAuthError(f"{url}: 403")
                if resp.status >= 400:
                    raise BraviaError(f"{url}: HTTP {resp.status}: {text[:200]}")
                return text
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise BraviaConnectionError(f"{url}: {err!r}") from err

    async def async_discover(self) -> None:
        """Read the UPnP descriptions to find service and action URLs.

        Falls back to the well-known 2012 paths for anything not found.
        Raises BraviaConnectionError if the TV does not answer at all.
        """
        reachable = False

        dmr_url = f"http://{self.host}:{DMR_PORT}/dmr.xml"
        try:
            root = ET.fromstring(await self._request("GET", dmr_url, cers=False))
            reachable = True
            services = _services(root, dmr_url)
            self.rendering_url = services.get(RENDERING_SERVICE)
            if IRCC_SERVICE in services:
                self.ircc_url = services[IRCC_SERVICE]
            self.info.name = _find_text(root, "friendlyName")
            self.info.model = _find_text(root, "modelName")
            self.info.udn = _find_text(root, "UDN")
        except (BraviaError, ET.ParseError) as err:
            _LOGGER.debug("No DMR description: %s", err)

        action_list_url = None
        for template in IRCC_DESCRIPTION_CANDIDATES:
            url = template.format(host=self.host, dmr_port=DMR_PORT)
            try:
                root = ET.fromstring(await self._request("GET", url, cers=False))
            except (BraviaError, ET.ParseError) as err:
                _LOGGER.debug("No IRCC description at %s: %s", url, err)
                continue
            reachable = True
            services = _services(root, url)
            if IRCC_SERVICE in services:
                self.ircc_url = services[IRCC_SERVICE]
            self.info.udn = self.info.udn or _find_text(root, "UDN")
            self.info.model = self.info.model or _find_text(root, "modelName")
            if action_list_url := _find_text(root, "X_CERS_ActionList_URL"):
                break

        candidates = [DEFAULT_ACTION_LIST.format(host=self.host)]
        if action_list_url:
            candidates.insert(0, action_list_url)
        for url in candidates:
            try:
                text = await self._request("GET", url)
                reachable = True
                if self._parse_action_list(text, url):
                    break
            except (BraviaError, ET.ParseError) as err:
                _LOGGER.debug("Could not read action list %s: %s", url, err)

        if not reachable:
            # Last resort: does anything answer on the CERS port at all?
            try:
                await self._request("GET", self.actions["getRemoteCommandList"])
            except BraviaAuthError:
                pass
        self.discovered = True
        _LOGGER.debug(
            "Discovered %s: mode=%s ircc=%s rendering=%s actions=%s",
            self.host,
            self.register_mode,
            self.ircc_url,
            self.rendering_url,
            self.actions,
        )

    def _parse_action_list(self, text: str, base: str) -> bool:
        """Read action URLs and the registration mode; True if any were found."""
        found = False
        for elem in ET.fromstring(text).iter():
            if _local(elem.tag) != "action" or not (name := elem.get("name")):
                continue
            url = elem.get("url")
            self.actions[name] = urljoin(base, url) if url else f"{base}?action={name}"
            found = True
            if name == "register" and (mode := elem.get("mode", "")).isdigit():
                self.register_mode = int(mode)
        return found

    async def async_register(self) -> None:
        """Register this client.

        Raises BraviaPinRequired if the TV shows a PIN (set ``pin`` and retry), and
        BraviaAuthError if the TV refused (e.g. not in registration mode).
        """
        params = {
            "name": self.nickname,
            "registrationType": "initial",
            "deviceId": self.device_id,
        }
        if self.register_mode >= 3:
            params["wolSupport"] = "true"
        base = self.actions["register"]
        url = f"{base}{'&' if '?' in base else '?'}{urlencode(params)}"
        await self._request("GET", url, timeout=REGISTER_TIMEOUT)

    async def async_update_commands(self) -> dict[str, str]:
        """Fetch the TV's IRCC command list (requires registration)."""
        text = await self._request("GET", self.actions["getRemoteCommandList"])
        commands = {}
        try:
            for elem in ET.fromstring(text).iter():
                if _local(elem.tag) != "command":
                    continue
                name, value = elem.get("name"), elem.get("value")
                if name and value and elem.get("type", "ircc").lower() == "ircc":
                    commands[name] = value
        except ET.ParseError as err:
            raise BraviaError(f"Invalid command list: {err}") from err
        if commands:
            self.commands = commands
        return commands

    async def async_get_system_information(self) -> dict[str, str]:
        """Return the flat key/value content of getSystemInformation, if offered."""
        try:
            root = ET.fromstring(
                await self._request("GET", self.actions["getSystemInformation"])
            )
        except (BraviaConnectionError, BraviaAuthError):
            raise
        except (BraviaError, ET.ParseError) as err:
            _LOGGER.debug("getSystemInformation failed: %s", err)
            return {}
        info = {
            _local(e.tag): e.text.strip()
            for e in root.iter()
            if e.text and e.text.strip() and len(e) == 0
        }
        self.info.extra = info
        self.info.model = self.info.model or info.get("modelName") or info.get("name")
        return info

    async def async_get_status(self) -> dict[str, str]:
        """Return getStatus items flattened to ``{"<status>.<field>": value}``.

        Doubles as the power check: raises BraviaConnectionError when off.
        """
        text = await self._request("GET", self.actions["getStatus"])
        result: dict[str, str] = {}
        try:
            for status in ET.fromstring(text).iter():
                if _local(status.tag) != "status":
                    continue
                prefix = status.get("name", "status")
                for item in status:
                    if (key := item.get("field")) is not None:
                        result[f"{prefix}.{key}"] = item.get("value", "")
        except ET.ParseError:
            _LOGGER.debug("Unparseable getStatus response: %s", text[:200])
        return result

    async def async_send_ircc(self, code: str) -> None:
        """Send one raw IRCC code."""
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body>"
            f'<u:X_SendIRCC xmlns:u="{IRCC_SERVICE}">'
            f"<IRCCCode>{code}</IRCCCode>"
            "</u:X_SendIRCC>"
            "</s:Body></s:Envelope>"
        )
        await self._request(
            "POST",
            self.ircc_url,
            data=body,
            headers={
                "SOAPACTION": f'"{IRCC_SERVICE}#X_SendIRCC"',
                "Content-Type": 'text/xml; charset="utf-8"',
            },
        )

    def code_for(self, command: str) -> str:
        """Resolve a command name (case-insensitive) or raw IRCC code."""
        table = self.commands or FALLBACK_COMMANDS
        if command in table:
            return table[command]
        lowered = {k.lower(): v for k, v in table.items()}
        if command.lower() in lowered:
            return lowered[command.lower()]
        if command.lower() in (
            fb := {k.lower(): v for k, v in FALLBACK_COMMANDS.items()}
        ):
            return fb[command.lower()]
        if command.startswith("AAAA") and command.endswith("="):
            return command
        raise ValueError(f"Unknown command: {command}")

    async def async_send_command(self, command: str) -> None:
        """Send a command by name (e.g. "VolumeUp") or raw IRCC code."""
        await self.async_send_ircc(self.code_for(command))

    async def _rendering(self, action: str, args: str) -> str:
        if not self.rendering_url:
            raise BraviaError("TV exposes no RenderingControl service")
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{RENDERING_SERVICE}">'
            f"<InstanceID>0</InstanceID><Channel>Master</Channel>{args}"
            f"</u:{action}></s:Body></s:Envelope>"
        )
        return await self._request(
            "POST",
            self.rendering_url,
            data=body,
            headers={
                "SOAPACTION": f'"{RENDERING_SERVICE}#{action}"',
                "Content-Type": 'text/xml; charset="utf-8"',
            },
            cers=False,
        )

    async def async_get_volume(self) -> int | None:
        """Return volume 0-100, or None if unavailable."""
        if not self.rendering_url:
            return None
        text = await self._rendering("GetVolume", "")
        value = _find_text(ET.fromstring(text), "CurrentVolume")
        return int(value) if value and value.isdigit() else None

    async def async_get_mute(self) -> bool | None:
        """Return mute state, or None if unavailable."""
        if not self.rendering_url:
            return None
        text = await self._rendering("GetMute", "")
        value = _find_text(ET.fromstring(text), "CurrentMute")
        return None if value is None else value in ("1", "true")

    async def async_set_volume(self, volume: int) -> None:
        """Set volume 0-100."""
        await self._rendering(
            "SetVolume", f"<DesiredVolume>{int(volume)}</DesiredVolume>"
        )

    async def async_set_mute(self, mute: bool) -> None:
        """Set mute state."""
        await self._rendering("SetMute", f"<DesiredMute>{int(mute)}</DesiredMute>")


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255") -> None:
    """Send a Wake-on-LAN packet (blocking; run in an executor)."""
    raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(raw) != 6:
        raise ValueError(f"Invalid MAC address: {mac}")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(b"\xff" * 6 + raw * 16, (broadcast, 9))
