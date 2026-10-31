#!/bin/sh
set -eu
server_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
"$server_dir/dc.sh" config --quiet
"$server_dir/dc.sh" build --pull api
"$server_dir/dc.sh" pull gateway
"$server_dir/dc.sh" run --rm --no-deps api python docker_support/deploy/server/check_settings.py
"$server_dir/dc.sh" exec -T api python docker_support/deploy/server/check_idle.py
"$server_dir/dc.sh" stop gateway
# Once ingress is closed, verify no last-second request admitted new work.
if ! "$server_dir/dc.sh" exec -T api python docker_support/deploy/server/check_idle.py; then
    "$server_dir/dc.sh" up -d --no-deps --wait gateway
    exit 1
fi
if ! "$server_dir/backup.sh"; then
    "$server_dir/dc.sh" up -d --no-deps --wait gateway
    exit 1
fi
"$server_dir/dc.sh" stop api
# On migration/startup failure leave ingress closed; inspect logs and the backup.
"$server_dir/dc.sh" up -d --no-deps --force-recreate --wait api
"$server_dir/dc.sh" up -d --no-deps --wait gateway
"$server_dir/dc.sh" ps
