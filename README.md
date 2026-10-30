# Self-hosted Cloud setup

Home Assistant app repository for a fresh HAOS installation, pinned to Core 2026.10.4. Add this repository URL to the HA app store and install **Self-hosted Cloud setup**. It requires a separately prepared self-hosted Cloud backend and its `ha-connection.json`.

Upload only `ha-connection.json`. Never upload backend directories, passwords, signing keys or complete server packages. The app stops and starts Core. Before Cloud login it can restore the original configuration while retaining installed files; after login it refuses automatic rollback to protect account state.

The bundled Cloud integration derives from Home Assistant Core 2026.10.4 and includes project-specific modifications. Home Assistant Core is distributed under the Apache License 2.0; see [UPSTREAM_LICENSE](UPSTREAM_LICENSE).
