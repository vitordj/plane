#!/usr/bin/env bash
#
# Terminal launcher for the Plane (Orca) Cloud Agent environment.
#
# Dispatches the long-running dev processes referenced by environment.json's
# terminals. Each backend role sources apps/api/.env (expanding the ${VAR} refs
# in DATABASE_URL/REDIS_URL) and uses the API virtualenv from install.sh.
#
# Usage: run.sh <api|worker|beat|web|admin>
set -euo pipefail

export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/.orca-venv"
role="${1:?usage: run.sh <api|worker|beat|web|admin>}"

case "$role" in
  api|worker|beat)
    cd "$REPO_ROOT/apps/api"
    set -a; source .env; set +a
    export DJANGO_SETTINGS_MODULE=plane.settings.local
    case "$role" in
      api)    exec "$VENV/bin/python" manage.py runserver 0.0.0.0:8000 ;;
      worker) exec "$VENV/bin/celery" -A plane worker -l info ;;
      beat)   exec "$VENV/bin/celery" -A plane beat -l info ;;
    esac
    ;;
  web)   cd "$REPO_ROOT"; exec pnpm --filter web dev ;;
  admin) cd "$REPO_ROOT"; exec pnpm --filter admin dev ;;
  *) echo "unknown role: $role (expected api|worker|beat|web|admin)" >&2; exit 1 ;;
esac
