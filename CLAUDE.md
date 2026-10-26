# CLAUDE.md

Guidance for coding agents working on this repository.

## What this is

A Home Assistant custom integration for 2011–2012 Sony Bravia TVs. These sets speak Sony's undocumented **CERS/IRCC** protocol, not the JSON API (`/sony/...`) of 2013+ models used by Home Assistant's built-in `braviatv` integration. Don't add 2013+ support here; point those users to `braviatv`.

- Repository: `github.com/cheanrod/ha-sony-bravia-legacy`
- Integration domain: `sony_bravia_cers`

## Layout

```
custom_components/sony_bravia_cers/
  api.py           protocol client; depends only on aiohttp, never import homeassistant here
  coordinator.py   polling (10 s); "unreachable" means off, not an update failure
  config_flow.py   setup, registration (3 modes), reauth
  entity.py        shared device info and the command-sending helper
  media_player.py  power, volume, sources, transport
  remote.py        remote.send_command for any IRCC command
  strings.json     UI text; translations/en.json identical, translations/de.json mirrors it
  brand/           icon(.svg/.png/@2x.png) and dark_icon variants, rendered from the SVGs
.github/workflows/ hassfest, HACS validation, tests + ruff
tests/
  fake_tv.py       simulated TV (aiohttp server) used by all tests
  test_api.py      protocol tests; also run without Home Assistant installed
  test_integration.py  config flow, setup and entities under Home Assistant
```

## Commands

```sh
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/pytest          # all tests
.venv/bin/ruff format . && .venv/bin/ruff check .
```

Ruff config is in `pyproject.toml` (line length 88). Tests that open sockets must use the `allow_sockets` fixture (`tests/conftest.py`); otherwise the Home Assistant test plugin blocks them.

## Rules

- **Never change the domain `sony_bravia_cers`.** It's stored in users' config entries, entity and device registries; renaming it forces everyone to remove and re-add the integration.
- **Keep `requirements` in `manifest.json` empty.** Everything is plain aiohttp and the standard library.
- **Nothing has been verified on a real TV.** Endpoints, response formats and the `getStatus` structure come from community reverse engineering. Any new behaviour needs a fallback for when a model answers differently, plus a `fake_tv.py` scenario and a test. Don't claim hardware behaviour as fact in code comments or docs.
- Config entry data is persisted user state: add keys freely, but don't rename or remove existing ones (`host`, `name`, `mac`, `device_id`, `pin`, `commands`) without a migration (`async_migrate_entry`, bump `VERSION`).
- When changing UI text, update all three: `strings.json`, its byte-identical copy `translations/en.json`, and `translations/de.json` (same keys, German, formal "Sie").
- `hacs.json` requires Home Assistant 2025.3.0 (first release with `AddConfigEntryEntitiesCallback`). Raise it when using newer Home Assistant APIs.
- Brand icons: edit the SVGs, then re-render, e.g. `inkscape icon.svg -w 256 -h 256 -o icon.png` and `-w 512 -h 512 -o icon@2x.png`, same for `dark_icon`.

## Releasing

HACS takes the version from the release tag, Home Assistant from the manifest, so the two must agree. The tag carries a `v`, the manifest doesn't:

1. Bump `version` in `custom_components/sony_bravia_cers/manifest.json`.
2. Commit, then `git tag -a vX.Y.Z` and push both.
3. `gh release create vX.Y.Z`: HACS offers users the release, not just the tag.

## Protocol notes

- Every CERS request carries `X-CERS-DEVICE-ID` (and `X-CERS-DEVICE-INFO`) with the registered client ID, `MediaRemote:<random mac>`, generated once per config entry.
- Discovery (`BraviaClient.async_discover`) tries, in order: `:52323/dmr.xml` (UPnP renderer: volume service, UDN, model), the IRCC descriptions in `IRCC_DESCRIPTION_CANDIDATES` (for `X_CERS_ActionList_URL` and the IRCC control URL), the advertised action list, then `/cers/ActionList.xml`. Anything not found falls back to `DEFAULT_ACTIONS` and `/IRCC`.
- The `mode` attribute of the `register` action selects registration: 1 = accept a prompt on the TV, 2 = TV must be in registration mode first, 3 = PIN sent via HTTP Basic auth with an empty user. The config flow handles all three; a 401 always means "ask for a PIN".
- Commands are base64 IRCC codes: `ircc_code(category, device, function)`. `FALLBACK_COMMANDS` is used when the TV won't return its own list.
- 2012 sets usually drop off the network in standby: turning on needs Wake-on-LAN (MAC in config) and the TV's remote start setting.
- What "CERS" stands for is unknown; Sony never documented the protocol.

References used: [ruby-cers-tv](https://github.com/lmc/ruby-cers-tv), [sonyapilib](https://github.com/alexmohr/sonyapilib).
