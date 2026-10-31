#!/bin/sh
set -eu
umask 077
server_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
backup_dir=${HEADSUP_BACKUP_DIR:-"$server_dir/backups"}
mkdir -p "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_file="$backup_dir/headsup-$stamp-$$.dump"
partial=$(mktemp "$backup_dir/.headsup-backup.XXXXXX")
trap 'rm -f "$partial"' EXIT HUP INT TERM
"$server_dir/dc.sh" exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl' > "$partial"
"$server_dir/dc.sh" exec -T db pg_restore --list < "$partial" > /dev/null
mv "$partial" "$backup_file"
echo "Database backup: $backup_file"
