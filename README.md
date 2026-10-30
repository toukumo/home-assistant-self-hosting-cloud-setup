# Self-hosted Cloud setup

Home Assistant app repository for a fresh HAOS installation, pinned to Core 2026.10.4. Add this repository URL to the HA app store and install **Self-hosted Cloud setup**. It requires a separately prepared self-hosted Cloud backend and its `ha-connection.json`.

Upload only `ha-connection.json`. Never upload backend directories, passwords, signing keys or complete server packages. The app stops and starts Core. Before Cloud login it can restore the original configuration while retaining installed files; after login it refuses automatic rollback to protect account state.

The bundled Cloud integration derives from Home Assistant Core 2026.10.4 and includes project-specific modifications. Home Assistant Core is distributed under the Apache License 2.0; see [UPSTREAM_LICENSE](UPSTREAM_LICENSE).

## Connect an existing server from a Mac or PC

Download this repository as a ZIP, extract it, and open `local_wizard/existing_server.html` in a browser. Provide the existing server API hostname and trusted public CA certificate and JWT public key, then compare their SHA-256 fingerprints with values obtained separately from the server administrator. The generated `ha-connection.json` can be imported into the HAOS setup app. No installer server is required.

The server currently allows one Home Assistant connection at a time; do not connect a second HA to an active server without a migration plan. This wizard prepares a fresh HA installation and does not replace existing Cloud account data.
