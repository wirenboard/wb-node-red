# shellcheck shell=sh
#
# Shared shell library for wb-docker-app maintainer scripts (design.md §3.1,
# §3.5.1). The lifecycle logic lives once in the Python helper
# (seeding/compose/systemd/cli); maintainer scripts of each service package are
# thin sh glue that source this library and call into the CLI. The library
# exists so per-service postinst/prerm stay one-liners and the call into the
# helper is written and tested in exactly one place.

# Path to the installed CLI entry point.
WB_DOCKER_APP_BIN="/usr/bin/wb-docker-app"

# wb_docker_app_provision_mqtt
# Provision container<->broker connectivity (the `wb` docker network + mosquitto
# gateway listener + ip_nonlocal_bind sysctl), restarting mosquitto at most once.
# Idempotent and a singleton: called from the postinst of each BRIDGE service
# that needs the broker, but the network is only created when absent and
# mosquitto is only restarted when its config drifts, so several such services
# can each call it without re-restarting the broker. Host-networking services
# (which reach mosquitto on localhost directly) do NOT call this. The helper no
# longer provisions at its own install (docs/adr/0004). Delegated to the CLI.
wb_docker_app_provision_mqtt() {
	"$WB_DOCKER_APP_BIN" provision-mqtt
}

# wb_docker_app_install <app> [data_uid]
# Bring the named app up: delegates the install flow (seed the user layer,
# refresh the vendored palette, enable the systemd instance, enable+reload the
# nginx site) to the helper CLI. Optional data_uid chowns the seeded data dir to
# that uid for a non-root container user (Node-RED -> 1000); omit it for services
# whose container runs as root.
wb_docker_app_install() {
	app="$1"
	data_uid="$2"
	if [ -z "$app" ]; then
		echo "wb-docker-app.sh: install requires an app name" >&2
		return 2
	fi
	if [ -n "$data_uid" ]; then
		"$WB_DOCKER_APP_BIN" install "$app" --data-uid "$data_uid"
	else
		"$WB_DOCKER_APP_BIN" install "$app"
	fi
}

# wb_docker_app_remove <app>
# Tear the named app down: disable+stop the systemd instance only. The user
# layer under /mnt/data is left intact. This does NOT reload nginx: the static
# proxy drop-in is a dpkg-owned file deleted AFTER prerm runs, so the reload
# (wb_docker_app_reload_proxy) belongs in the service's postrm. Delegated to the
# helper CLI.
wb_docker_app_remove() {
	app="$1"
	if [ -z "$app" ]; then
		echo "wb-docker-app.sh: remove requires an app name" >&2
		return 2
	fi
	"$WB_DOCKER_APP_BIN" remove "$app"
}

# wb_docker_app_reload_proxy
# Tolerant nginx reload (nginx -t, then reload only if the config is valid),
# delegated to the helper CLI. Run from a service's postrm AFTER dpkg has
# deleted that service's static proxy drop-in, so the reload drops the now-stale
# server-block. Never fails the removal on an unrelated broken nginx config.
wb_docker_app_reload_proxy() {
	"$WB_DOCKER_APP_BIN" reload-proxy
}
