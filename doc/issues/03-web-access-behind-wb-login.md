# 3. Web access behind the WB login

Labels: `ready-for-agent`
Type: HITL

## Parent

[doc/wb-docker-apps-prd.md](../wb-docker-apps-prd.md) — PRD: wb-docker-apps.
Architecture and rationale: [doc/wb-docker-apps-design.md](../wb-docker-apps-design.md) §3.8, §3.9, §3.10; PRD modules **A** (descriptor reader), **B** (nginx render), **C** (internal-port allocator).

## What to build

Expose the service on its public "native" port through nginx, gated by the same
single login as the controller web UI — never opened to the LAN unauthenticated.

End-to-end path:

- Container binds only to `127.0.0.1:<internal>`; the helper **allocates** the
  internal loopback port and keeps a registry so repeat requests for the same
  app return the same port and there are no collisions (module C).
- The helper **reads the app descriptor** from compose labels via
  `docker compose config` / `docker ps --filter label=wb.app` → `AppDescriptor`
  (name, image, public port, internal port, proxy role, title) (module A).
- The helper **renders an nginx server-block** (port-for-all) from the
  descriptor (module B, pure function): public `listen` on `wb.proxy.port`,
  `proxy_pass` to the internal loopback port, WebSocket upgrade headers,
  `auth_request /auth/check;` with `set $required_user_type "admin";`, and
  `error_page 401` → redirect to the homeui login form.
- The block is dropped into `/etc/nginx/includes/default.wb.d/`, then nginx is
  reloaded.

Node-RED is RCE-capable (exec node, bus/GPIO access), so it is gated to the
`admin` role. The homeui session cookie is bound to the host (not the port), so
the single login works on a separate port.

## Acceptance criteria

- [ ] `http://<controller>:1880` serves Node-RED through nginx; the container
      itself is not reachable on `0.0.0.0`.
- [ ] An unauthenticated request is redirected to the homeui login (not a bare
      `401`); after logging in as an admin-role user, access is granted via the
      homeui cookie.
- [ ] A non-admin (Operator/User) role is denied.
- [ ] Internal-port allocator: unit tests for no-collision, idempotent
      same-app → same-port, and release on remove.
- [ ] nginx render: unit tests asserting presence of `auth_request`, correct
      role, public/internal ports, `error_page 401`, upgrade headers, and
      stable output.
- [ ] WebSocket-based Node-RED editor works through the proxy.

## Blocked by

- #1 (walking skeleton)

> **HITL:** requires validation on a real controller (candidate `aat3d5fw`,
> wb-2602/wb7) that `auth_request` in a third-party server-block actually gates
> to the admin role and grants access via the homeui cookie
> (design.md §6 item 1).
