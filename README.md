# wb-node-red

One-command installation of [Node-RED](https://nodered.org/) as a Docker service
on [Wiren Board](https://wirenboard.com/) controllers:

```sh
apt install wb-node-red
```

This pulls Docker and the shared helper, brings the container up, and reverse-proxies
the Node-RED editor on its own port behind the controller's web login — pre-wired to
the local MQTT broker, with the Wiren Board palette already in place.

This repository is a small monorepo for the Docker-app framework; Node-RED is its
first service.

## Packages

| Package | Role |
| --- | --- |
| **`wb-docker-app`** | Shared helper, installed once as a dependency. Owns the host integration that must be set up once: the `wb` Docker network + mosquitto gateway listener, the templated systemd unit, and seeding/refreshing the per-service user layer. CLI verbs: `provision-mqtt`, `install <app>`, `remove <app>`, `reload-proxy`. |
| **`wb-node-red`** | The Node-RED service. A thin `Architecture: all` package that ships a fixed-port base compose, a standalone nginx server block, the default flow, and the vendored WB palette. `Depends: docker-ce, wb-docker-app`. |

## How it works

- **Image — mirror, not a custom build.** The container runs a byte-for-byte mirror
  of the upstream `nodered/node-red` image (no derived build). The Wiren Board palette
  `node-red-contrib-wirenboard` is pure JavaScript, so it is vendored into the `.deb`
  at build time (`npm`) and delivered into `/data/node_modules` at install — no npm or
  internet at runtime.
- **MQTT.** Containers reach the broker through a mosquitto listener bound to the
  gateway of a dedicated `wb` Docker network (`172.29.0.1:11883`), so the broker is not
  exposed to the LAN. `net.ipv4.ip_nonlocal_bind` lets mosquitto bind that address at
  its normal early boot, before Docker has created the network.
- **Web access.** A standalone nginx server block proxies the editor on its own public
  port and reuses the controller's web login (`auth_request` against the homeui
  back-end). Node-RED can run host commands, so the gate requires the **admin** role.
  > **Create a Wiren Board login.** Until a user exists, the controller's web UI — and
  > therefore this service — is open on the LAN.
- **Data & upgrades.** Package-owned files live under `/usr/lib/wb-docker-app/<app>/`
  and are overwritten on upgrade. The user layer (`docker-compose.override.yml`, `.env`,
  `data/`) lives under `/mnt/data/wb-docker-apps/<app>/` and is seeded only when absent,
  so edits and flows survive upgrades and reinstalls. Updates are explicit
  (`apt upgrade`); the packages are excluded from unattended upgrades.
- **Lifecycle.** Each service is an instance of the templated systemd unit
  `wb-docker-app@<app>.service`, which runs `docker compose up -d`. Manage it with the
  standard `systemctl` and `docker` tools; there is no bespoke CLI.

## Layout

- `src/wb_docker_app/` — the helper (Python). Pure cores (`mqtt`, `seeding`) and thin
  wrappers (`systemd`, `mqtt_provision`, `cli`) over an injected `runner.Runner` so the
  orchestration is unit-testable without touching the real system.
- `packaging/` — Debian packaging for `wb-docker-app` and `wb-node-red`.
- `tests/` — pytest. Cores are tested deterministically; wrappers/CLI with a fake
  `Runner` and `tmp_path`, so no real docker/systemd/nginx is invoked.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e . pytest
python -m pytest -q
```

Building real `.deb`s needs a Debian host (`dpkg-buildpackage`, `fakeroot`,
`debhelper-13`, `dh-python`). The WB palette is `npm`-vendored into `wb-node-red` at
build time, so the build host needs `npm` and registry access; nothing is fetched at
runtime on the controller.
