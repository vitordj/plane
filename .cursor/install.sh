#!/usr/bin/env bash
#
# Repository bootstrap for the Plane (Orca) Cloud Agent environment.
#
# Runs after the repository is checked out. Idempotent and safe to re-run: it
# prepares the per-service .env files, installs Node dependencies, builds the
# workspace library packages the web/admin apps import, and creates the Python
# virtualenv for the Django API. Runtime services are started by start.sh.
set -euo pipefail

export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV="$HOME/.orca-venv"

# ---------------------------------------------------------------------------
# 1. Environment files.
#    Only create each one once so a generated SECRET_KEY (and the localhost
#    host rewrite below) stay stable across re-runs and reboots.
# ---------------------------------------------------------------------------
create_env() {
  local example="$1" target="$2"
  if [ ! -f "$target" ] && [ -f "$example" ]; then
    cp "$example" "$target"
    echo "created $target"
  fi
}
create_env ".env.example" ".env"
for svc in web admin space api live; do
  create_env "apps/$svc/.env.example" "apps/$svc/.env"
done

# The API .env examples use docker-compose service hostnames (plane-db, etc.).
# When running natively, point them at localhost and give Django a stable
# SECRET_KEY. DATABASE_URL/REDIS_URL keep their ${VAR} form and are expanded
# when the file is sourced (see start.sh and run.sh).
if ! grep -q '^SECRET_KEY=' apps/api/.env; then
  sed -i \
    -e 's/^POSTGRES_HOST=.*/POSTGRES_HOST="127.0.0.1"/' \
    -e 's/^REDIS_HOST=.*/REDIS_HOST="127.0.0.1"/' \
    -e 's/^RABBITMQ_HOST=.*/RABBITMQ_HOST="127.0.0.1"/' \
    apps/api/.env
  printf '\nSECRET_KEY="%s"\n' "$(tr -dc 'a-z0-9' < /dev/urandom | head -c50)" >> apps/api/.env
  echo "configured apps/api/.env for localhost services"
fi

# ---------------------------------------------------------------------------
# 2. Node dependencies (pnpm version comes from package.json packageManager).
# ---------------------------------------------------------------------------
corepack enable pnpm 2>/dev/null || true
pnpm install --frozen-lockfile

# ---------------------------------------------------------------------------
# 3. Build the workspace library packages that the web & admin apps import.
#    The `dev` turbo task declares dependsOn ["^build"], so these dist outputs
#    must exist before the app dev servers can resolve @plane/* packages.
# ---------------------------------------------------------------------------
pnpm turbo run build --filter="web^..." --filter="admin^..."

# ---------------------------------------------------------------------------
# 4. Python virtualenv for the Django API.
#    psycopg-c needs libpq build headers we do not ship; psycopg-binary (already
#    pinned in requirements) provides the driver, so that one line is filtered.
# ---------------------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip "setuptools>=70" wheel
REQ_FILE="$(mktemp)"
grep -v '^psycopg-c' apps/api/requirements/base.txt > "$REQ_FILE"
grep -v '^-r' apps/api/requirements/local.txt >> "$REQ_FILE"
"$VENV/bin/pip" install --quiet -r "$REQ_FILE"
rm -f "$REQ_FILE"

echo "install.sh complete"
