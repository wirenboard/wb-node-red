"""nginx server-block renderer (module B).

Renders a single nginx server-block for a WB-managed service: a port-for-all
reverse proxy (design.md §3.8) gated by the homeui login via ``auth_request``
(design.md §3.9). Pure function — same :class:`AppDescriptor` in, byte-identical
text out.
"""

from __future__ import annotations

from .models import AppDescriptor


def render_server_block(desc: AppDescriptor) -> str:
    """Render the nginx server-block for ``desc`` as a deterministic string."""
    return (
        f"server {{\n"
        f"    listen {desc.public_port};\n"
        f"\n"
        f"    auth_request /auth/check;\n"
        f'    set $required_user_type "{desc.proxy_role}";\n'
        f"\n"
        f"    # auth_request returns a bare 401; send the browser to the homeui\n"
        f"    # login form instead (design.md §3.9).\n"
        f"    error_page 401 = @login_redirect;\n"
        f"    location @login_redirect {{\n"
        f"        return 302 /login/?return_to=$scheme://$http_host$request_uri;\n"
        f"    }}\n"
        f"\n"
        f"    location / {{\n"
        f"        proxy_pass http://127.0.0.1:{desc.internal_port};\n"
        f"\n"
        f"        proxy_set_header Host $host;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"\n"
        f"        # WebSocket upgrade — the Node-RED editor needs it.\n"
        f"        proxy_http_version 1.1;\n"
        f"        proxy_set_header Upgrade $http_upgrade;\n"
        f'        proxy_set_header Connection "upgrade";\n'
        f"    }}\n"
        f"}}\n"
    )
