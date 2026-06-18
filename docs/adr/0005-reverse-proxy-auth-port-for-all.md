---
status: accepted
---
# 0005 — Reverse-proxy a service behind the homeui login (port-for-all, done right)

## Context

Controller validation (aat3d5fw) disproved the first cut of the web-access design
(design.md §3.8/§3.9). Two facts about the WB nginx/auth layout:

- `/etc/nginx/includes/default.wb.d/` is included **inside** the homeui `server{}`
  block, so it accepts only `location`/directive additions — a standalone
  `server{}` there fails `nginx -t` ("server directive is not allowed here").
- `/auth/check` and the `wb-homeui-back` upstream are defined inside the homeui
  server block. A separate server block on another port cannot reach `/auth/check`
  → the auth subrequest 404s → nginx returns **500, not the intended 401**.

We keep **port-for-all** (each service on its own public port, not a subpath),
because Home Assistant and other future services cannot run under a subpath.

## Decision

A service ships a STANDALONE nginx `server{}` block, installed to
`/etc/nginx/sites-available/<svc>.conf` and symlinked into `sites-enabled/` via
dh_link (the WB pattern, cf. `wb-mqtt-alice-proxy`). The block carries the auth
plumbing itself:

- its own `location = /auth/check { internal; … proxy_pass http://wb-homeui-back/auth/check; }`
  — the `wb-homeui-back` upstream is declared http-level and is visible to every
  server block;
- `set $required_user_type "<role>"` + `auth_request /auth/check` on the proxied
  location (Node-RED → `admin`, RCE-capable);
- `error_page 401` → redirect to `$scheme://$host/login/…` using **port-less
  `$host`**, so the login form is served by the homeui origin (80/443), not back
  on the service port (which would loop).

The homeui session cookie is host-scoped (not port-scoped), so a user logged into
homeui is authorized on the service port without a second login.

## Considered and rejected

- **A `location` drop-in in `default.wb.d/` (subpath, like the Alice integration).**
  Simplest, and `auth_request` works natively — but it is subpath-only. Home
  Assistant can't subpath, and we want one mechanism for every service. Revisit
  only if HA support is ever dropped.

## Consequences

- Each service's nginx carries ~8 lines of auth plumbing (the `/auth/check`
  copy); acceptable and identical across services.
- **Validated on aat3d5fw (live):** `nginx -t` passes with the standalone server
  block in `sites-available`. With a homeui admin user created, an unauthenticated
  request to `:1880` returns **401 → 302** to the port-less homeui login
  (`Location: http://<host>/login/?return_to=…:1880/`) without reaching the
  upstream — confirmed on both loopback and the LAN IP. (Before any user existed,
  homeui returned 200 from `/auth/check` and the gate was open — see the security
  caveat.) The only remaining item is the authenticated pass-through (a logged-in
  session reaches the service) — a 30-second manual browser check.
- **Security caveat:** the gate is only as strong as homeui auth. On a controller
  with no homeui users the proxied service (Node-RED is RCE-capable) is open on
  its port to the LAN — the same exposure homeui itself has there. The service
  docs must tell the user to create a WB login.
