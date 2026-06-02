"""Behavior of the internal loopback port allocator (module C).

The helper binds each container to ``127.0.0.1:<internal>`` (design.md §3.8) and
must hand out a free loopback port per app without collisions, persisting the
assignment to a registry file so it survives between helper invocations.
"""

import pytest

from wb_docker_app.ports import PortAllocationError, PortAllocator


def test_allocate_returns_a_port_in_range(tmp_path):
    alloc = PortAllocator(tmp_path / "ports.json", start=20000, end=29999)

    port = alloc.allocate("node-red")

    assert 20000 <= port <= 29999


def test_allocating_same_app_twice_returns_same_port(tmp_path):
    alloc = PortAllocator(tmp_path / "ports.json")

    assert alloc.allocate("node-red") == alloc.allocate("node-red")


def test_two_apps_get_different_ports(tmp_path):
    alloc = PortAllocator(tmp_path / "ports.json")

    assert alloc.allocate("node-red") != alloc.allocate("grafana")


def test_fresh_allocator_remembers_existing_assignment(tmp_path):
    registry = tmp_path / "ports.json"
    first = PortAllocator(registry).allocate("node-red")

    reloaded = PortAllocator(registry)

    assert reloaded.allocate("node-red") == first


def test_fresh_allocator_avoids_already_assigned_ports(tmp_path):
    registry = tmp_path / "ports.json"
    taken = PortAllocator(registry).allocate("node-red")

    other = PortAllocator(registry).allocate("grafana")

    assert other != taken


def test_release_frees_the_port_for_reuse(tmp_path):
    registry = tmp_path / "ports.json"
    alloc = PortAllocator(registry, start=20000, end=20000)
    first = alloc.allocate("node-red")

    alloc.release("node-red")

    assert alloc.allocate("grafana") == first


def test_release_is_persisted(tmp_path):
    registry = tmp_path / "ports.json"
    alloc = PortAllocator(registry, start=20000, end=20000)
    alloc.allocate("node-red")
    alloc.release("node-red")

    assert PortAllocator(registry, start=20000, end=20000).allocate("grafana") == 20000


def test_exhausting_the_range_raises_a_clear_error(tmp_path):
    alloc = PortAllocator(tmp_path / "ports.json", start=20000, end=20000)
    alloc.allocate("node-red")

    with pytest.raises(PortAllocationError, match="20000"):
        alloc.allocate("grafana")
