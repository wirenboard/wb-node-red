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

# wb_docker_app_install <app>
# Bring the named app up: delegates the install flow (seed the user layer,
# enable the systemd instance, reload nginx for the package's static proxy
# drop-in) to the helper CLI.
wb_docker_app_install() {
	app="$1"
	if [ -z "$app" ]; then
		echo "wb-docker-app.sh: install requires an app name" >&2
		return 2
	fi
	"$WB_DOCKER_APP_BIN" install "$app"
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
