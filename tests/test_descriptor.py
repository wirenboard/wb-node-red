"""Behavior of the descriptor reader (module A).

Input is the parsed YAML of ``docker compose config`` for a single service —
docker normalizes that into canonical long form (labels as a map, ports as a
list of dicts), which is what these tests feed in.
"""

import pytest

from wb_docker_app.descriptor import DescriptorError, read_descriptor
from wb_docker_app.models import AppDescriptor


def _config(**overrides):
    """A canonical ``docker compose config`` dict for one WB service."""
    cfg = {
        "services": {
            "node-red": {
                "image": "registry.wirenboard.com/wb/node-red:4.0.2-wb1",
                "ports": [
                    {
                        "mode": "ingress",
                        "host_ip": "127.0.0.1",
                        "target": 1880,
                        "published": "21880",
                        "protocol": "tcp",
                    }
                ],
                "labels": {
                    "wb.app": "node-red",
                    "wb.title": "Node-RED",
                    "wb.proxy.port": "1880",
                    "wb.proxy.role": "admin",
                },
            }
        }
    }
    cfg["services"]["node-red"].update(overrides)
    return cfg


def test_reads_a_well_formed_service_into_a_descriptor():
    desc = read_descriptor(_config())

    assert desc == AppDescriptor(
        app="node-red",
        title="Node-RED",
        image="registry.wirenboard.com/wb/node-red:4.0.2-wb1",
        public_port=1880,
        internal_port=21880,
        proxy_role="admin",
    )


def test_missing_wb_label_raises_descriptor_error_naming_the_label():
    cfg = _config()
    del cfg["services"]["node-red"]["labels"]["wb.proxy.role"]

    with pytest.raises(DescriptorError, match="wb.proxy.role"):
        read_descriptor(cfg)
