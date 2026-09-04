#!/usr/bin/env bash
# Ship the code: pull on the server, then restart Passenger.
#
# There is no build step — this is a Flask app serving its own templates, so a
# deploy really is just these two things.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
[ -f "$ROOT/tools/deploy.env" ] && source "$ROOT/tools/deploy.env"

: "${SSH_HOST:?set SSH_HOST in tools/deploy.env}"
: "${SSH_USER:?set SSH_USER in tools/deploy.env}"
: "${APP_ROOT:?set APP_ROOT in tools/deploy.env}"
SSH_PORT="${SSH_PORT:-22}"
BRANCH="${BRANCH:-main}"

ssh -p "$SSH_PORT" "$SSH_USER@$SSH_HOST" bash -s <<REMOTE_SCRIPT
set -euo pipefail
cd '$APP_ROOT'
git fetch --quiet origin
git reset --hard "origin/$BRANCH"
mkdir -p tmp
# Passenger watches this file; touching it makes the next request start a
# fresh process. Equivalent to the Restart button in cPanel's Setup Python App.
touch tmp/restart.txt
echo "deployed \$(git rev-parse --short HEAD) to $APP_ROOT"
REMOTE_SCRIPT

if [ -n "${SITE_URL:-}" ]; then
  echo "==> health check"
  curl -fsS "$SITE_URL/api/health" && echo
fi
