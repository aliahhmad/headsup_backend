#!/bin/sh
set -eu
server_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
env_file=${HEADSUP_SERVER_ENV:-"$server_dir/.env"}
if [ ! -f "$env_file" ]; then
    echo "Missing server environment file. Run init_env.py first." >&2
    exit 1
fi
exec docker compose --env-file "$env_file" -f "$server_dir/compose.yaml" "$@"
