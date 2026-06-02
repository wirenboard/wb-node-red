"""Behavior of the nginx server-block renderer (module B).

Input is an :class:`AppDescriptor`; output is the text of a single nginx
server-block implementing port-for-all reverse proxy gated by the homeui login
(design.md §3.8 port-for-all, §3.9 auth_request). These tests assert on the
rendered string, not on how it is built.
"""

from wb_docker_app.models import AppDescriptor
from wb_docker_app.nginx import render_server_block


def _descriptor(**overrides):
    """A representative WB service descriptor (Node-RED on the admin role)."""
    fields = {
        "app": "node-red",
        "title": "Node-RED",
        "image": "registry.wirenboard.com/wb/node-red:4.0.2-wb1",
        "public_port": 1880,
        "internal_port": 21880,
        "proxy_role": "admin",
    }
    fields.update(overrides)
    return AppDescriptor(**fields)


def test_listens_on_public_port_and_proxies_to_internal_loopback_port():
    block = render_server_block(_descriptor(public_port=1880, internal_port=21880))

    assert "listen 1880;" in block
    assert "proxy_pass http://127.0.0.1:21880;" in block


def test_gates_on_homeui_auth_request_with_the_descriptor_role():
    block = render_server_block(_descriptor(proxy_role="admin"))

    assert "auth_request /auth/check;" in block
    assert 'set $required_user_type "admin";' in block


def test_redirects_to_homeui_login_form_on_401_not_a_bare_401():
    block = render_server_block(_descriptor())

    assert "error_page 401" in block
    assert "/login" in block
    # the 401 must be turned into a redirect to the login form, not served bare
    assert "=301" in block or "=302" in block or "return 302" in block


def test_includes_websocket_upgrade_plumbing_for_the_node_red_editor():
    block = render_server_block(_descriptor())

    assert "proxy_http_version 1.1;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert 'proxy_set_header Connection "upgrade";' in block


def test_forwards_standard_proxy_headers_to_the_upstream():
    block = render_server_block(_descriptor())

    assert "proxy_set_header Host $host;" in block
    assert "proxy_set_header X-Real-IP $remote_addr;" in block
    assert (
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block
    )
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in block


def test_output_is_byte_identical_across_calls_for_the_same_descriptor():
    desc = _descriptor()

    assert render_server_block(desc) == render_server_block(desc)
