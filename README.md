# DRIP — Definitely Reliable Irrigation Project

A MicroPython irrigation controller for Raspberry Pi Pico W / Pico 2 W.

## Configuration

Create your local settings file:

```bash
cp src/settings.env.example src/settings.env
```

Fill in your Wi-Fi credentials:

```dotenv
WIFI_SSID=YOUR_WIFI_SSID
WIFI_PASSWORD=YOUR_WIFI_PASSWORD

OTA_BASE_URL=[https://example.invalid/ota/](https://example.invalid/ota/)
OTA_MANIFEST_NAME=manifest.json
```

Use plain `KEY=value` entries without surrounding quotes.
Do not commit `settings.env`.

`OTA_BASE_URL` is currently required by the configuration loader,
even though OTA is disabled. The example URL is only a placeholder.

## Running on the Pico

1. Install MicroPython firmware matching your board.
2. Copy the contents of `src/` to the root of the Pico filesystem.
3. Include your private `settings.env`.
4. Restart the board.

Do not copy the enclosing `src/` directory, tests, or development dependencies.

Currently, `main.py` prints the application version, connects to Wi-Fi,
prints connection status, and disconnects. Irrigation control and communication
clients are not yet connected to the entry point.

## OTA Updates

OTA is currently disabled and is not ready to enable.

The existing implementation requires fixes, an HTTP/HTTPS client, and tests.
There is no supported enable switch yet; uncommenting the earlier example
is not sufficient.

Setup instructions will be added when the update workflow is implemented.
Device settings will remain separate from update packages.

## Development and Tests

Codespaces installs development dependencies during container setup.

To install them manually and run tests, execute from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Tests run on CPython with substitutes for selected MicroPython modules.
The current suite covers the settings loader, parser factory, singleton,
and software-level valve behavior.


## Branches and CI

- `develop` — ongoing development.
- `main` — reviewed changes merged through pull requests.

GitHub Actions checks Python syntax and runs tests on pushes and pull requests
to these branches. It does not currently publish OTA updates.

## Hardware Note

Valve control assumes GPIO high means open and low means closed.
Verify this against the actual relay and valve hardware before use.

The project is under development and has not been validated for unattended
irrigation.
