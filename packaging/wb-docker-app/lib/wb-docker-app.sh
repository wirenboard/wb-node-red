# shellcheck shell=sh
#
# Shared shell library for wb-docker-app maintainer scripts (design.md §3.1,
# §3.5.1). The heavy, dangerous lifecycle logic lives once in the Python helper
# (descriptor/seeding/compose/systemd/cli); maintainer scripts of each service
# package are thin sh glue that source this library and call into the CLI. The
# library exists so per-service postinst/prerm stay one-liners and the call into
# the helper is written and tested in exactly one place.

# Path to the installed CLI entry point.
WB_DOCKER_APP_BIN="/usr/bin/wb-docker-app"

# wb_docker_app_install <app>
# Bring the named app up: delegates the whole install flow (seed user layer,
# compose pull/up, nginx block, enable the systemd instance) to the helper CLI.
wb_docker_app_install() {
	app="$1"
	if [ -z "$app" ]; then
		echo "wb-docker-app.sh: install requires an app name" >&2
		return 2
	fi
	"$WB_DOCKER_APP_BIN" install "$app"
}

# wb_docker_app_remove <app>
# Tear the named app down: disable the systemd instance, compose down, drop the
# nginx block, release the port. Delegated to the helper CLI.
wb_docker_app_remove() {
	app="$1"
	if [ -z "$app" ]; then
		echo "wb-docker-app.sh: remove requires an app name" >&2
		return 2
	fi
	"$WB_DOCKER_APP_BIN" remove "$app"
}
