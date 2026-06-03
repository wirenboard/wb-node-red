# AFK audit — wb-docker-apps (full-auto)

Hands-off run on branch `pipeline/wb-docker-apps` (baseline `master` @ `5e1573b`).
**5 of 6 issues integrated** (#1, #5, #7, #3, #4); **#6 parked** (registry infra).
**111 tests pass** on the branch (was 49). 4 review rounds; **6 findings unresolved**
after the 3-round fix budget. 40 autonomous decisions, 35 manual-verify items logged.

This is your window into what was decided in your place. Read top-down — riskiest first.

---

## ⚠️ Decisions to review first

### 1. wb-diag-collect integration edits a *foreign* package's config in place — the riskiest call, and it owns the only MAJOR open finding
Issue #5 needs services to appear in the `wb-diag-collect` archive. The agents found the
**released `wb-diag-collect` reads only one monolithic `/usr/share/wb-diag-collect/wb-diag-collect.conf`
with no `conf.d` merge**. So they:
- surgically splice a sentinel-delimited block into that file from `wb-docker-app` postinst (and remove it on prerm) — `src/wb_docker_app/diag.py`;
- added an **apt `DPkg::Post-Invoke` hook** (`packaging/wb-docker-app/apt/53wb-docker-app-reg-diag`) to re-insert the block after any apt transaction, because a `wb-diag-collect` upgrade would overwrite it;
- used **pure-text YAML splicing** (not a YAML library) to preserve the foreign file byte-for-byte, since only PyYAML is a dependency and it drops comments.

**Why this is contentious:** we are modifying a file owned by another package, and the text-splice makes assumptions about that file's exact YAML layout. The 5 of 6 open findings below are all here. Alternatives rejected: `dpkg-divert` (fragile), patching upstream to add `conf.d` (out of scope), shipping only the inert drop-in (leaves the AC unmet on current controllers).
**Recommendation:** review `diag.py` carefully, or push WB to add `conf.d` support to `wb-diag-collect` upstream and drop the in-place edit entirely.

### 2. MQTT: helper edits mosquitto config + a hard-coded subnet
Issue #4 wires the provisioner into `wb-docker-app` postinst (a new `provision-mqtt` verb, run once). It writes `/etc/mosquitto/conf.d/wb.conf` (gateway listener `172.29.0.1:11883`) and an `After=docker.service` systemd drop-in, and restarts mosquitto once. Subnet `172.29.0.0/24` / gateway `172.29.0.1` are the design's example values, exposed as overridable constants in `cli.py`.
**Review:** (a) is the helper editing mosquitto config acceptable WB policy? (PRD Further Notes #1) (b) does `172.29.0.0/24` collide with anything on real controllers? (PRD Further Notes #3)

### 3. Port injection via `--env-file` (the open §5 item)
The allocated loopback port is written to the seeded `.env` (`WB_INTERNAL_PORT`) and both the systemd unit and `ComposeRunner` pass `--env-file <data>/<app>/.env`. This is the mechanism design.md §5 explicitly left open; it's now committed but only fake-Runner-tested. Confirm real `docker compose --env-file` resolution on a controller.

### 4. Packaging lives in one repo under `packaging/<pkg>/`, not repo-per-package
Design §3.13 says "repo = package" (WB convention). The agents co-located all three packages (`wb-docker-app`, `wb-node-red`, `wb-echo`) under `packaging/` in this single repo to keep helper-code reuse and the shared test command simple, deferring a repo split. *Confidence: medium.* Decide whether to split before WB Jenkins onboarding.

### 5. Branch-naming clash (benign, but note it)
The pipeline could not create `pipeline/wb-docker-apps/issue-N` because the integration branch `pipeline/wb-docker-apps` already occupies that ref as a leaf (git dir/file conflict). Agents fell back to `pipeline/wb-docker-apps-issue-N` (hyphen). The per-issue branches still exist. No harm — just confirm the convention.

---

## Open findings (your bug queue — unresolved after 3 fix rounds)

| Sev | Finding | Where |
|-----|---------|-------|
| **major** | `diag.register()` produces invalid YAML when upstream `commands:` key is flow/inline style | `src/wb_docker_app/diag.py:99-124` |
| minor | diag splice assumes upstream uses 2-space list-item indentation; mismatch → invalid YAML | `diag.py:_our_block` |
| minor | diag `commands:` matcher would also match an inline/empty `commands:` mapping value | `diag.py:_commands_insert_pos` |
| minor | Debian changelog date: weekday does not match date (dpkg-parsechangelog violation) | `packaging/wb-docker-app/debian/changelog:11` |
| minor | (same changelog) `wb-docker-app 0.2.0` entry has wrong day-of-week | `packaging/wb-docker-app/debian/changelog:11` |
| minor | `list` verb shows only *running* containers; AC says "installed" services | `src/wb_docker_app/cli.py:list_apps` |

5 of 6 cluster on the `diag.py` foreign-config splice (see contentious decision #1) — the cheapest durable fix is upstream `conf.d` support. The changelog weekday is a 1-character fix. The `list` semantics is a small design call (running vs installed).

---

## Decisions made without you (the rest, grouped)

**Undiscussed (worth a glance):**
- systemd unit is `Type=oneshot RemainAfterExit=yes`, `ExecStart=docker compose up -d` / `ExecStop=down`, reconstructing paths + `wb-<app>` project name from `%i` — mirrors `cli.py/_compose` so systemd bring-up is byte-identical to the helper's.
- `wb-echo` chosen as the second service (echo utility, port 8080, role admin) — smallest service that exercises every isolation property without inventing functionality.
- unattended-upgrades blacklist uses anchored names (`wb-docker-app$`, `wb-node-red$`), and `wb-echo` ships its *own* drop-in — NOT a broad `wb-*` (which would freeze unrelated WB platform packages). This corrects an earlier within-run decision that hard-coded `wb-echo$` into the shared helper drop-in.
- day-2 ops documented as a new §3.11.1 inside the existing Russian design.md (co-located, no new doc).
- HITL items logged as structured verify entries rather than a new audit file (no such convention existed in the repo).

**Routine:** `nginx -t` gates the reload in *both* install and remove (factored into `_reload_nginx`); merge conflict in `test_cli.py` resolved additively (kept both issue-4 and issue-5 test groups); provisioning made a true no-op when network present + drop-ins already match (content-equality, not a marker file); register/deregister hooked at postinst-configure / prerm-remove.

---

## Manual verification checklist

- [ ] Review `diag.py` splice against the **actual** shipped `wb-diag-collect.conf` layout (flow-style `commands:`, non-2-space indent, no trailing newline) — fixes the major finding.
- [ ] Fix the `wb-docker-app` changelog weekday (`debian/changelog:11`) so `dpkg-parsechangelog` is clean.
- [ ] Decide `list` semantics: running vs installed services (`cli.py:list_apps`).
- [ ] Confirm the `pipeline/wb-docker-apps-issue-N` (hyphen) branch naming is acceptable.
- [ ] Decide repo-per-package split vs the current single-repo `packaging/` layout before Jenkins onboarding.
- [ ] Approve the helper editing mosquitto config (`/etc/mosquitto/conf.d/wb.conf` + systemd drop-in).
- [ ] Confirm `nginx -t` passes on the controller with the rendered block — i.e. the homeui `/auth/check` `auth_request` location exists in WB's nginx config (else the new gate aborts the reload by design).
- [ ] Confirm no other caller relied on the removed `_is_ours()` helper in `diag.py`.

## Manual test plan (controller / build-host required)

- [ ] **Build:** `dpkg-buildpackage` for all three packages on a Debian host (fakeroot, debhelper-13, dh-python, pybuild). Rootless tests use `dpkg-deb --build` and do **not** exercise `debian/rules`/`pybuild`; confirm the `/usr/bin/wb-docker-app` entry point and `dh_install` mappings resolve.
- [ ] **Install (#1):** on a controller already on the docker-ce repack — `apt install wb-node-red`, confirm helper auto-pulls, container comes up, `systemctl status wb-docker-app@node-red` active, survives `docker kill` + reboot, `apt purge` tears down cleanly. (Needs the `wb` network from #4.)
- [ ] **Auth (#3, aat3d5fw):** unauth browser on `:1880` → 302 to homeui `/login` (not bare 401); admin cookie grants access without re-login (host-bound cookie); Operator/User denied; Node-RED editor WebSocket works through the proxy.
- [ ] **MQTT (#4):** subnet `172.29.0.0/24` doesn't collide; SIGHUP/reload does **not** open the gateway listener (full restart required); after reboot mosquitto binds `172.29.0.1:11883` and a container on `wb` reaches it while `1883` is not newly exposed on `0.0.0.0`.
- [ ] **Diag (#5):** install `wb-docker-app` over real `wb-diag-collect`, confirm the entry lands, run `wb-diag-collect`, confirm `service/wb-docker-app.log` has per-service `systemctl status` + journal; then `apt upgrade wb-diag-collect` and confirm the Post-Invoke hook re-heals the entry; `apt remove` cleans it.
- [ ] **Unattended (#5):** `unattended-upgrade --dry-run --debug` shows `wb-docker-app`/`wb-node-red`/`wb-echo` blacklisted while an unrelated `wb-*` system package stays eligible.
- [ ] **Downgrade (#5):** `apt install wb-node-red=<older>` + `wb-docker-app update node-red` recreates the container on the older tag.
- [ ] **Isolation (#7):** `apt install wb-echo` alongside Node-RED — both run on distinct ports behind the login; mosquitto NOT restarted and `wb` network NOT touched on the second install; `apt purge wb-echo` leaves Node-RED + broker + network intact.
- [ ] **Port (#1/§5):** published container port matches the allocator-assigned `WB_INTERNAL_PORT` in the seeded `.env`, and survives `systemctl restart wb-docker-app@<app>`.

## Tools & infra needed to test

- **WB container registry** — the base composes pin `registry.wirenboard.com/wb/node-red:4.0.2-wb1` and `…/wb/echo:1.0.0-wb1`; **both are placeholders that do not exist yet**. Real images must be published before any controller `compose pull`/`up` works. (This is the heart of parked **#6**: derived-image Dockerfile + image-pipeline are blocked on the registry + CI standup.)
- **Debian build host** — fakeroot, debhelper-compat 13, dh-python, python3-setuptools/pybuild for `dpkg-buildpackage`.
- **Real controller** (candidate `aat3d5fw`, wb-2602/wb7, on the docker-ce repack) with `wb-mqtt-homeui` (provides the `/auth/check` `auth_request` handler), an **admin** and a **non-admin** homeui account, mosquitto, and `wb-diag-collect` installed.
- **PyYAML in the test venv** — 4 diag YAML-shape tests currently `skip` because `python3-yaml` isn't in `.venv`; add it if CI should enforce the YAML shape.

---

## #6 — parked (not attempted)
Derived Node-RED image + image-pipeline: blocked on WB-registry credentials and the separate image CI pipeline (PRD "Out of Scope"). The Dockerfile/pipeline artifacts were *not* authored because issue #6 was triaged as impossible-without-infra; resume it once the registry exists.
