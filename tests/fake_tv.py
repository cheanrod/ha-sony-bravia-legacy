"""A simulated 2011-2012 Bravia speaking the CERS/IRCC protocol."""

from __future__ import annotations

import base64

from aiohttp import web

DEVICE_ID = "MediaRemote:c0-01-fa-ce-d0-0d"

COMMAND_LIST = """<?xml version="1.0"?>
<remoteCommandList>
  <command name="Power" type="ircc" value="AAAAAQAAAAEAAAAVAw=="/>
  <command name="HDMI1" type="ircc" value="AAAAAgAAABoAAABaAw=="/>
  <command name="Netflix" type="url" value="http://example/"/>
  <command name="Tv" type="ircc" value="AAAAAQAAAAEAAAAkAw=="/>
</remoteCommandList>"""

STATUS = """<?xml version="1.0"?>
<statusList><status name="viewing">
  <statusItem field="source" value="HDMI1"/>
</status></statusList>"""


def ircc_description(base: str) -> str:
    return f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:av="urn:schemas-sony-com:av">
 <device>
  <modelName>KDL-46HX750</modelName>
  <UDN>uuid:1234</UDN>
  <serviceList><service>
   <serviceType>urn:schemas-sony-com:service:IRCC:1</serviceType>
   <serviceId>urn:schemas-sony-com:serviceId:IRCC</serviceId>
   <controlURL>/sony/IRCC</controlURL>
  </service></serviceList>
  <av:X_UNR_DeviceInfo><av:X_CERS_ActionList_URL>{base}/cers/actionList</av:X_CERS_ActionList_URL></av:X_UNR_DeviceInfo>
 </device>
</root>"""


class FakeTv:
    def __init__(
        self,
        register_mode: int = 1,
        pin: str | None = None,
        prompt: bool = False,
        description: bool = True,
    ) -> None:
        self.register_mode = register_mode
        self.pin = pin
        # prompt=True: registrations wait until accept() ("OK" pressed on the TV).
        self.prompt = prompt
        self.pending: set[str] = set()
        self.registered: set[str] = set()
        self.ircc: list[str] = []
        self.base = ""
        app = web.Application()
        # description=False: no UPnP description, only /cers/ActionList.xml.
        if description:
            app.router.add_get("/cers/Ircc.xml", self.description)
            app.router.add_get("/cers/actionList", self.action_list)
        else:
            app.router.add_get("/cers/ActionList.xml", self.action_list)
        app.router.add_get("/cers/api/register", self.register)
        app.router.add_get("/cers/api/getRemoteCommandList", self.commands)
        app.router.add_get("/cers/api/getStatus", self.status)
        app.router.add_post("/sony/IRCC", self.send_ircc)
        self.app = app

    def _authorized(self, request: web.Request) -> bool:
        return request.headers.get("X-CERS-DEVICE-ID") in self.registered

    async def description(self, request):
        return web.Response(text=ircc_description(self.base), content_type="text/xml")

    async def action_list(self, request):
        return web.Response(
            text=f"""<actionList>
  <action name="register" mode="{self.register_mode}"
          url="{self.base}/cers/api/register"/>
  <action name="getRemoteCommandList" url="{self.base}/cers/api/getRemoteCommandList"/>
  <action name="getStatus" url="{self.base}/cers/api/getStatus"/>
</actionList>""",
            content_type="text/xml",
        )

    async def register(self, request):
        assert request.query["deviceId"] == request.headers["X-CERS-DEVICE-ID"]
        assert request.query["registrationType"] == "initial"
        if self.pin is not None:
            auth = request.headers.get("Authorization")
            if auth != "Basic " + base64.b64encode(f":{self.pin}".encode()).decode():
                return web.Response(status=401)
        (self.pending if self.prompt else self.registered).add(
            request.query["deviceId"]
        )
        return web.Response(text="")

    def accept(self) -> None:
        self.registered |= self.pending
        self.pending.clear()

    async def commands(self, request):
        if not self._authorized(request):
            return web.Response(status=403)
        return web.Response(text=COMMAND_LIST, content_type="text/xml")

    async def status(self, request):
        if not self._authorized(request):
            return web.Response(status=403)
        return web.Response(text=STATUS, content_type="text/xml")

    async def send_ircc(self, request):
        assert (
            request.headers["SOAPACTION"]
            == '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'
        )
        body = await request.text()
        self.ircc.append(body.split("<IRCCCode>")[1].split("</IRCCCode>")[0])
        return web.Response(text="<ok/>")
