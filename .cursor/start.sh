#!/usr/bin/env bash
#
# Per-boot startup for the Plane (Orca) Cloud Agent environment.
#
# Starts and reconciles the backing services (PostgreSQL, Redis, RabbitMQ,
# MinIO), ensures the app database/role/bucket exist, applies migrations, and
# registers/configures the Plane instance. Idempotent: every step checks for the
# desired state first, so it is safe to run on every boot. The app servers
# themselves run in the terminals defined in environment.json.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
VENV="$HOME/.orca-venv"

# ---------------------------------------------------------------------------
# PostgreSQL — start the default cluster and ensure the plane role + database.
# ---------------------------------------------------------------------------
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do
  sudo -u postgres pg_isready -h 127.0.0.1 -q && break
  sleep 1
done
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='plane'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE plane WITH LOGIN SUPERUSER PASSWORD 'plane';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='plane'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE plane OWNER plane;"

# ---------------------------------------------------------------------------
# Redis.
# ---------------------------------------------------------------------------
redis-cli -h 127.0.0.1 ping >/dev/null 2>&1 \
  || redis-server --daemonize yes --port 6379 --bind 127.0.0.1

# ---------------------------------------------------------------------------
# RabbitMQ — celery broker; create the plane user + vhost matching apps/api/.env.
# The broker node takes a while to come up on a cold boot, so poll await_startup
# in a loop (tolerating the transient "node down" exit 69) instead of a single
# blocking wait that would abort start.sh on the first boot.
# ---------------------------------------------------------------------------
if ! sudo rabbitmqctl -q await_startup --timeout 5 >/dev/null 2>&1; then
  sudo rabbitmq-server -detached || true
  rmq_ready=0
  for _ in $(seq 1 40); do
    if sudo rabbitmqctl -q await_startup --timeout 5 >/dev/null 2>&1; then
      rmq_ready=1
      break
    fi
    sleep 3
  done
  if [ "$rmq_ready" -ne 1 ]; then
    echo "RabbitMQ did not become ready in time" >&2
    exit 1
  fi
fi
sudo rabbitmqctl list_users 2>/dev/null | grep -q '^plane' || sudo rabbitmqctl add_user plane plane
sudo rabbitmqctl set_user_tags plane administrator
sudo rabbitmqctl list_vhosts 2>/dev/null | grep -qx plane || sudo rabbitmqctl add_vhost plane
sudo rabbitmqctl set_permissions -p plane plane ".*" ".*" ".*"

# ---------------------------------------------------------------------------
# MinIO — S3-compatible object storage on :9000 (console :9090), bucket "uploads".
# Credentials match apps/api/.env (access-key / secret-key, USE_MINIO=0 so the
# API generates presigned URLs pointing straight at the :9000 endpoint).
# ---------------------------------------------------------------------------
MINIO_DATA="$HOME/.minio-data"
mkdir -p "$MINIO_DATA"
if ! curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then
  MINIO_ROOT_USER=access-key MINIO_ROOT_PASSWORD=secret-key \
    nohup minio server "$MINIO_DATA" --address :9000 --console-address :9090 \
    > /tmp/orca-minio.log 2>&1 &
  for _ in $(seq 1 30); do
    curl -sf http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1 && break
    sleep 1
  done
fi
mc alias set orca http://127.0.0.1:9000 access-key secret-key >/dev/null 2>&1 || true
mc mb --ignore-existing orca/uploads >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Django — apply migrations and register/configure the instance (idempotent).
# ---------------------------------------------------------------------------
set -a; source apps/api/.env; set +a
export DJANGO_SETTINGS_MODULE=plane.settings.local
cd apps/api
"$VENV/bin/python" manage.py migrate --no-input
export MACHINE_SIGNATURE="$(echo "orca-cloud-$(hostname)" | sha256sum | awk '{print $1}')"
"$VENV/bin/python" manage.py register_instance "$MACHINE_SIGNATURE" || true
"$VENV/bin/python" manage.py configure_instance || true

echo "start.sh complete"
