"""Shared contract types for the wb-docker-app helper.

``AppDescriptor`` is the single source of truth that flows between the helper's
pure cores: the *descriptor reader* (module A) produces it from a service's
``docker compose config`` output, and the *nginx renderer* (module B) consumes
it to emit a server-block. Field vocabulary mirrors the ``wb.*`` compose labels
defined in design.md §3.10.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppDescriptor:
    """A WB-managed docker service, as described by its compose labels + ports.

    Attributes:
        app: ``wb.app`` — the app slug (e.g. ``node-red``), also the compose
            project name and the systemd template instance.
        title: ``wb.title`` — human-readable name (e.g. ``Node-RED``).
        image: the pinned container image reference.
        public_port: ``wb.proxy.port`` — the public port nginx listens on
            (port-for-all), e.g. ``1880``.
        internal_port: the host-side loopback port from the compose ``ports``
            mapping ``127.0.0.1:<internal>:<container>``, e.g. ``21880``.
        proxy_role: ``wb.proxy.role`` — the required homeui ``required_user_type``
            gating the service (e.g. ``admin``).
    """

    app: str
    title: str
    image: str
    public_port: int
    internal_port: int
    proxy_role: str
