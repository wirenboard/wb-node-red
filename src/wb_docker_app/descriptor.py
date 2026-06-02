"""Descriptor reader (module A).

Parses the canonical ``docker compose config`` output for a WB-managed service
into an :class:`AppDescriptor`. The ``wb.*`` labels are the source of truth
(design.md §3.10); the host loopback port is read from the ``ports`` mapping.
"""

from __future__ import annotations

from .models import AppDescriptor


class DescriptorError(ValueError):
    """Raised when a compose config is not a well-formed WB service."""


def read_descriptor(config: dict) -> AppDescriptor:
    """Read a single-service ``docker compose config`` dict into a descriptor."""
    services = config.get("services") or {}
    if len(services) != 1:
        raise DescriptorError(
            f"expected exactly one service, found {len(services)}"
        )
    (name, service), = services.items()

    labels = service.get("labels") or {}

    def label(key: str) -> str:
        try:
            return labels[key]
        except KeyError:
            raise DescriptorError(f"missing required label {key!r}") from None

    app = label("wb.app")
    title = label("wb.title")
    proxy_role = label("wb.proxy.role")
    public_port = int(label("wb.proxy.port"))

    ports = service.get("ports") or []
    internal_port = int(ports[0]["published"])

    return AppDescriptor(
        app=app,
        title=title,
        image=service["image"],
        public_port=public_port,
        internal_port=internal_port,
        proxy_role=proxy_role,
    )
