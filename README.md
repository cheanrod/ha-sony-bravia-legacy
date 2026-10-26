# Sony Bravia (2011–2012) for Home Assistant

A custom integration for Bravia TVs from 2011–2012 (for example the KDL‑xxEX, HX and NX series). These sets use Sony's older **CERS/IRCC** remote API. Home Assistant's built-in *Sony Bravia TV* integration only supports the JSON API that arrived in 2013.

## Features

| Entity | What it does |
|---|---|
| `media_player.<name>` | on/off, volume up/down, mute, absolute volume (if the TV offers UPnP RenderingControl), input selection, play/pause/stop, channel up/down (next/previous track) |
| `remote.<name>_remote` | sends any command the TV knows. The `commands` attribute lists them. |

Example:

```yaml
action: remote.send_command
target:
  entity_id: remote.bravia_remote
data:
  command: [Home, Down, Down, Confirm]
  delay_secs: 0.5
```

`command` accepts command names (case-insensitive) or raw IRCC codes like `AAAAAQAAAAEAAAAvAw==`.

## Installation

1. Copy `custom_components/sony_bravia_cers` into your Home Assistant `config/custom_components/` folder. You can also add `https://github.com/cheanrod/ha-sony-bravia-legacy` to HACS as a custom repository (category: Integration).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Sony Bravia (2011-2012)**.
4. Enter the TV's IP address, and optionally its MAC address (see *Turning the TV on* below).
5. Finish registration on the TV. The flow guides you through it. Depending on the model, you will either:
   - **accept a prompt** on the screen with the remote,
   - **put the TV in registration mode** first (*Settings → Network → Home Network Setup → Remote Device / Renderer → Registration*), or
   - **type the PIN** the TV shows.

Give the TV a fixed IP address (a DHCP reservation in your router).

## Turning the TV on

Most 2012 Bravias switch off their network interface in standby, so nothing on the network can wake them through the remote API. To turn the TV on from Home Assistant:

- enter the TV's **MAC address** in the config flow. Home Assistant then sends a Wake-on-LAN packet.
- enable **Remote Start** / **Wake on LAN** on the TV (*Settings → Network → Remote Start* on models that have it).

If the TV has neither option, it cannot be switched on over the network. In that case, use an IR blaster or HDMI-CEC from another device instead. Turning the TV **off** always works.

When Home Assistant runs in Docker, Wake-on-LAN broadcasts need `network_mode: host`.

## Notes and limitations

- **On/off state:** the TV counts as "on" when it answers on the network.
- **Current input:** read on a best-effort basis from the TV's `getStatus` response. The raw status fields appear as attributes on the media player, because their meaning varies by model.
- **Command list:** read from the TV once and stored. If the TV refuses to provide it, a built-in table of common Bravia codes is used.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

The tests run against a simulated TV (`tests/fake_tv.py`). They cover discovery, the three registration modes, the config flow, reauth, and the entities.

## License

MIT, see [LICENSE](LICENSE).
