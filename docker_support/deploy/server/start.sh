#!/bin/sh
set -eu
server_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -n "$("$server_dir/dc.sh" ps -q api)" ]; then
    echo "An API is already running. Use update.sh for a backed-up, idle-checked update." >&2
    exit 1
fi
"$server_dir/dc.sh" config --quiet
"$server_dir/dc.sh" build --pull api
"$server_dir/dc.sh" run --rm --no-deps api python docker_support/deploy/server/check_settings.py
"$server_dir/dc.sh" up -d --wait db
"$server_dir/dc.sh" up -d --no-deps --wait api
"$server_dir/dc.sh" up -d --no-deps --wait gateway
"$server_dir/dc.sh" ps
