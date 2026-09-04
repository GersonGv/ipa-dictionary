#!/usr/bin/env bash
# Upload the dump and import it into the cPanel MySQL database over SSH.
#
# Configure once in tools/deploy.env (copied from tools/deploy.env.example),
# then:
#
#   ./tools/import_remote.sh
#
# The import replaces the tables outright — the dump starts with DROP TABLE.
# That is intentional: rebuilding is how this data is updated, and a partial
# merge of a lexicon is not something worth writing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
[ -f "$ROOT/tools/deploy.env" ] && source "$ROOT/tools/deploy.env"

: "${SSH_HOST:?set SSH_HOST in tools/deploy.env}"
: "${SSH_USER:?set SSH_USER in tools/deploy.env}"
: "${APP_ROOT:?set APP_ROOT in tools/deploy.env}"
: "${DB_NAME:?set DB_NAME in tools/deploy.env}"
: "${DB_USER:?set DB_USER in tools/deploy.env}"
: "${DB_PASSWORD:?set DB_PASSWORD in tools/deploy.env}"
SSH_PORT="${SSH_PORT:-22}"
DUMP="${1:-$ROOT/build/dictionary.sql.gz}"

[ -s "$DUMP" ] || { echo "no dump at $DUMP — run ./tools/build_data.sh first" >&2; exit 1; }

REMOTE="$APP_ROOT/dictionary.sql.gz"
SSH=(ssh -p "$SSH_PORT" "$SSH_USER@$SSH_HOST")

echo "==> uploading $(du -h "$DUMP" | cut -f1) to $SSH_USER@$SSH_HOST:$REMOTE"
scp -P "$SSH_PORT" "$DUMP" "$SSH_USER@$SSH_HOST:$REMOTE"

echo "==> importing into $DB_NAME (this is the slow part — expect several minutes)"
# The password goes in via an env var read by MYSQL_PWD rather than on the
# command line, where it would show up in the server's process list.
"${SSH[@]}" "MYSQL_PWD='$DB_PASSWORD' bash -s" <<REMOTE_SCRIPT
set -euo pipefail
gunzip -c '$REMOTE' | mysql --default-character-set=utf8mb4 -u '$DB_USER' '$DB_NAME'
rm -f '$REMOTE'
echo "rows now in the database:"
MYSQL_PWD='$DB_PASSWORD' mysql -u '$DB_USER' '$DB_NAME' -e \
  'SELECT (SELECT COUNT(*) FROM words) AS words, (SELECT COUNT(*) FROM senses) AS senses, (SELECT COUNT(*) FROM examples) AS examples;'
REMOTE_SCRIPT

echo "==> done"
